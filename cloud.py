"""
云端账号与 Mod 列表同步（客户端）
=================================

界面只跟这个模块打交道，所有网络请求都在这里，方便出错时统一提示。

服务器地址优先级：
    config.json 里的 cloud_url  →  内置默认值  →  常见备用端口
连不上主地址时会自动试 :8080 / :8000（比如反代没配好、直接跑在 8080 上），
试通一次就记到 data/account.json，下次不用再探。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import core

try:
    import requests
except ImportError:          # core.check_dependencies() 会给出更友好的提示
    requests = None


DEFAULT_BASE = "http://150.230.169.157:8080"
FALLBACK_PORTS = (80, 8000)
ACCOUNT_PATH = core.DATA_DIR / "account.json"
UA = "REPO-Mod-Installer/1.0 (+cloud-sync)"


class CloudError(Exception):
    """消息已经是能直接显示给用户的中文"""


# ============================================================
# 本地 Mod 清单
# ============================================================

def local_mods():
    """把 installed.json 转成上传用的精简结构"""
    db = core.load_installed()
    out = []
    for full_name, info in db.items():
        out.append({
            "full_name": full_name,
            "version": str(info.get("version", "")),
            "name": full_name.split("-", 1)[-1],
        })
    out.sort(key=lambda x: x["full_name"].lower())
    return out


def diff(remote, local):
    """
    对比云端列表和本机列表。
    返回 (云端有但本机没有的, 本机有但云端没有的, 两边都有但版本不同的)
    """
    rm = {m["full_name"].lower(): m for m in remote}
    lm = {m["full_name"].lower(): m for m in local}

    missing = [rm[k] for k in rm if k not in lm]
    extra = [lm[k] for k in lm if k not in rm]
    older = []
    for k in rm:
        if k in lm:
            rv, lv = rm[k].get("version", ""), lm[k].get("version", "")
            if rv and lv and rv != lv:
                older.append({"full_name": rm[k]["full_name"],
                              "remote": rv, "local": lv})
    return missing, extra, older


def ago(ts):
    """'2026-10-20 11:02:33' → '3 分钟前'"""
    if not ts:
        return "从未同步"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            t = time.mktime(time.strptime(ts[:19], fmt))
            break
        except Exception:
            continue
    else:
        return ts

    d = int(time.time() - t)
    if d < 0:
        return "刚刚"
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    if d < 86400 * 30:
        return f"{d // 86400} 天前"
    return ts[:10]


# ============================================================
# 客户端
# ============================================================

class Cloud:
    def __init__(self, cfg=None):
        self.cfg = cfg if cfg is not None else core.load_config()
        self.token = ""
        self.username = ""
        self.user = {}
        self._session = None

        saved = self._load()
        self.base = (self.cfg.get("cloud_url") or saved.get("base")
                     or DEFAULT_BASE).rstrip("/")
        self.token = saved.get("token", "")
        self.username = saved.get("username", "")

    # -------------------------------------------------- 持久化
    def _load(self):
        try:
            if ACCOUNT_PATH.exists():
                with open(ACCOUNT_PATH, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            core.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(ACCOUNT_PATH, "w", encoding="utf-8") as f:
                json.dump({"base": self.base, "token": self.token,
                           "username": self.username}, f, ensure_ascii=False)
        except Exception:
            pass

    @property
    def logged_in(self):
        return bool(self.token and self.username)

    # -------------------------------------------------- 网络
    def _sess(self):
        if self._session is None:
            if requests is None:
                raise CloudError("缺少 requests 库，无法联网")
            self._session = requests.Session()
            # 不读环境变量里的 HTTP_PROXY：本机代理软件经常把 127.0.0.1 也塞进去，
            # 会让请求绕一圈甚至被拦掉。要代理就显式写在 config.json 的 proxy 里。
            self._session.trust_env = False
            self._session.headers.update({"User-Agent": UA,
                                          "Content-Type": "application/json"})
            proxy = (self.cfg.get("proxy") or "").strip()
            if proxy:
                self._session.proxies.update({"http": proxy, "https": proxy})
        return self._session

    def _candidates(self):
        """主地址优先，再试备用端口；已记住的地址排最前"""
        urls = [self.base]
        host = self.base.split("://", 1)[-1].split(":")[0].split("/")[0]
        scheme = "https" if self.base.startswith("https") else "http"
        for p in FALLBACK_PORTS:
            u = f"{scheme}://{host}:{p}"
            if u not in urls:
                urls.append(u)
        return urls

    def _call(self, method, path, body=None, auth=True, timeout=(6, 20)):
        last = ""
        for base in self._candidates():
            url = f"{base}{path}"
            headers = {}
            if auth and self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            try:
                r = self._sess().request(method, url, json=body, headers=headers,
                                         timeout=timeout)
            except requests.exceptions.SSLError as e:
                last = f"证书错误：{e}"
                continue
            except requests.exceptions.Timeout:
                last = "服务器响应超时"
                continue
            except requests.exceptions.ConnectionError:
                last = "连不上服务器"
                continue
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                continue

            if base != self.base:
                self.base = base          # 记住了，下次直接用它
                self._save()

            try:
                data = r.json()
            except Exception:
                data = {}

            if r.status_code == 401:
                self.token = ""
                self._save()
                raise CloudError(data.get("error") or "登录已失效，请重新登录")
            if r.status_code >= 400 or data.get("ok") is False:
                raise CloudError(data.get("error") or f"服务器返回 {r.status_code}")
            return data

        raise CloudError(f"{last}（{self.base}）\n请确认服务器已启动、网络可达")

    # -------------------------------------------------- 账号
    def health(self):
        return self._call("GET", "/api/health", auth=False, timeout=(5, 8))

    def register(self, username, password):
        d = self._call("POST", "/api/register",
                       {"username": username, "password": password}, auth=False)
        self._adopt(d)
        return d["user"]

    def login(self, username, password):
        d = self._call("POST", "/api/login",
                       {"username": username, "password": password}, auth=False)
        self._adopt(d)
        return d["user"]

    def _adopt(self, d):
        self.token = d.get("token", "")
        self.user = d.get("user", {}) or {}
        self.username = self.user.get("username", "")
        self._save()

    def logout(self):
        try:
            if self.token:
                self._call("POST", "/api/logout")
        except Exception:
            pass
        self.token = ""
        self.username = ""
        self.user = {}
        self._save()

    def me(self):
        d = self._call("GET", "/api/me")
        self.user = d.get("user", {}) or {}
        self.username = self.user.get("username", self.username)
        self._save()
        return self.user

    def set_public(self, public):
        d = self._call("POST", "/api/privacy", {"public": bool(public)})
        self.user["is_public"] = d.get("is_public", public)
        return self.user

    # -------------------------------------------------- 我的列表
    def push(self, mods=None, note=""):
        mods = local_mods() if mods is None else mods
        return self._call("PUT", "/api/mods", {"mods": mods, "note": note})

    def pull(self):
        return self._call("GET", "/api/mods")

    def clear(self):
        return self._call("DELETE", "/api/mods")

    # -------------------------------------------------- 找玩家
    def search(self, q="", limit=30):
        from urllib.parse import quote
        return self._call("GET",
                          f"/api/users/search?q={quote(q)}&limit={limit}")

    def view(self, username):
        from urllib.parse import quote
        return self._call("GET", f"/api/users/{quote(username)}")
