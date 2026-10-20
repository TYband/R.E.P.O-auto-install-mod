# -*- coding: utf-8 -*-
"""
R.E.P.O. Mod 安装核心逻辑
=========================
不依赖图形界面，可单独调用。

功能：
  1. 自动定位 Steam 上 R.E.P.O. 的安装目录
  2. 从 Thunderstore 官方 API 获取 mod 包信息（带本地缓存）
  3. 递归解析 mod 依赖链（这是手动安装最麻烦的部分）
  4. 智能识别包内结构，解压到正确位置
  5. 安装前自动备份、记录安装清单、支持卸载
"""

import os
import re
import sys
import json
import time
import shutil
import zipfile
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


# ============================================================
# 常量
# ============================================================

REPO_APPID = "3241660"          # R.E.P.O. 的 Steam AppID
THUNDERSTORE_COMMUNITY = "repo"
INDEX_URL = f"https://thunderstore.io/c/{THUNDERSTORE_COMMUNITY}/api/v1/package/"
INDEX_MAX_AGE = 24 * 3600       # 索引缓存有效期（秒）

BASE_DIR = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
BACKUP_DIR = DATA_DIR / "backups"
INDEX_CACHE = DATA_DIR / "index.json"
INSTALLED_DB = DATA_DIR / "installed.json"
CONFIG_PATH = BASE_DIR / "config.json"

# 打包成 exe 后：若 exe 旁边没有 config.json，就把内置的那份释放出来
if getattr(sys, "frozen", False):
    _bundled = Path(getattr(sys, "_MEIPASS", str(BASE_DIR))) / "config.json"
    if not CONFIG_PATH.exists() and _bundled.exists():
        try:
            shutil.copy2(_bundled, CONFIG_PATH)
        except Exception:
            pass

# Thunderstore 包根目录下的元数据文件，不需要复制进游戏
META_FILES = {"manifest.json", "icon.png", "readme.md", "changelog.md",
              "license.txt", "license", "icon.jpg"}

USER_AGENT = "REPO-Mod-Installer/1.0 (+portable; for personal use)"


# ============================================================
# 配置
# ============================================================

DEFAULT_CONFIG = {
    "game_path": "",                 # 手动指定游戏目录；留空=自动检测
    "proxy": "",                     # 下载代理，如 http://127.0.0.1:7890
    "backup_before_install": True,
    "auto_install_list": [
        "BepInEx-BepInExPack",
        "Zehs-REPOLib",
        "nickklmao-MenuLib",
        "nickklmao-REPOConfig"
    ]
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


# ============================================================
# Steam / 游戏目录定位
# ============================================================

def _steam_roots_from_registry():
    """从注册表读取 Steam 安装位置"""
    roots = []
    try:
        import winreg
    except ImportError:
        return roots

    keys = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]
    for hive, sub, value in keys:
        try:
            with winreg.OpenKey(hive, sub) as key:
                path, _ = winreg.QueryValueEx(key, value)
                if path:
                    p = Path(path.replace("/", "\\"))
                    if p.exists():
                        roots.append(p)
        except Exception:
            continue
    return roots


def _fallback_steam_paths():
    """注册表找不到时的常见路径兜底"""
    paths = []
    for drive in "CDEFGH":
        for sub in ("Steam", "SteamLibrary", "Games/Steam", "Program Files (x86)/Steam",
                    "Program Files/Steam"):
            p = Path(f"{drive}:/{sub}")
            if p.exists():
                paths.append(p)
    return paths


def find_steam_root():
    for p in _steam_roots_from_registry():
        return p
    for p in _fallback_steam_paths():
        return p
    return None


def get_steam_libraries(steam_root):
    """解析 libraryfolders.vdf，返回所有 steamapps 目录"""
    if not steam_root:
        return []

    libs = []
    steamapps = steam_root / "steamapps"
    if steamapps.exists():
        libs.append(steamapps)

    vdf = steamapps / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                raw = m.group(1).replace("\\\\", "/")
                p = Path(raw)
                if p.exists():
                    sa = p / "steamapps"
                    if sa.exists() and sa not in libs:
                        libs.append(sa)
        except Exception:
            pass
    return libs


def find_game_dir(cfg=None):
    """
    定位 R.E.P.O. 安装目录。优先级：
      1. config.json 里手动指定的 game_path
      2. Steam 库中的 appmanifest_3241660.acf
    """
    cfg = cfg or load_config()

    manual = (cfg.get("game_path") or "").strip()
    if manual:
        p = Path(manual)
        if p.exists():
            return p, "手动指定"

    steam_root = find_steam_root()
    if not steam_root:
        return None, "未找到 Steam（可在配置里手动填写游戏目录）"

    for steamapps in get_steam_libraries(steam_root):
        manifest = steamapps / f"appmanifest_{REPO_APPID}.acf"
        if not manifest.exists():
            continue
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'"installdir"\s+"([^"]+)"', text)
            if m:
                game = steamapps / "common" / m.group(1).replace("\\\\", "/")
                if game.exists():
                    return game, f"Steam 库: {steamapps.parent}"
        except Exception:
            continue

    return None, "未找到 R.E.P.O.（AppID 3241660），请确认游戏已安装"


def is_bepinex_installed(game_dir):
    """判断 BepInEx 是否已经装好"""
    if not game_dir:
        return False
    return (game_dir / "winhttp.dll").exists() and (game_dir / "BepInEx" / "core").exists()


def bepinex_version(game_dir):
    if not game_dir:
        return None
    core = game_dir / "BepInEx" / "core" / "BepInEx.dll"
    if not core.exists():
        return None
    try:
        return str(core.stat().st_mtime_ns)
    except Exception:
        return "?"


# ============================================================
# Thunderstore API
# ============================================================

def _session(proxy=""):
    if requests is None:
        raise RuntimeError("缺少 requests 库，请运行: pip install requests")
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _slim_package(pkg):
    """精简索引字段，把 34MB 的原始索引压到几 MB"""
    versions = []
    for v in pkg.get("versions", []):
        versions.append({
            "version_number": v.get("version_number"),
            "download_url": v.get("download_url"),
            "dependencies": v.get("dependencies", []),
            "description": v.get("description", ""),
            "downloads": v.get("downloads", 0),
            "date_created": v.get("date_created", ""),
        })
    return {
        "full_name": pkg.get("full_name"),
        "name": pkg.get("name"),
        "owner": pkg.get("owner"),
        "package_url": pkg.get("package_url"),
        "versions": versions,
    }


def fetch_index(force=False, proxy="", log=print):
    """
    获取 Thunderstore 的 R.E.P.O. 社区包索引（带本地缓存）
    返回 list[dict]
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and INDEX_CACHE.exists():
        age = time.time() - INDEX_CACHE.stat().st_mtime
        if age < INDEX_MAX_AGE:
            try:
                with open(INDEX_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log(f"使用本地缓存索引（{len(data)} 个 mod，{int(age/60)} 分钟前更新）")
                return data
            except Exception:
                pass

    log("正在从 Thunderstore 下载 mod 索引，首次需要一会儿…")
    s = _session(proxy)
    resp = s.get(INDEX_URL, timeout=120)
    resp.raise_for_status()
    raw = resp.json()

    slim = [_slim_package(p) for p in raw]
    slim.sort(key=lambda p: p["full_name"].lower())

    tmp = INDEX_CACHE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False)
    tmp.replace(INDEX_CACHE)

    log(f"索引更新完成，共 {len(slim)} 个 mod")
    return slim


def _parse_dep(dep_str):
    """
    解析依赖字符串 'Namespace-PackageName-1.2.3'
    返回 (full_name, version) —— full_name = 'Namespace-PackageName'
    """
    parts = dep_str.split("-")
    if len(parts) < 3:
        return dep_str, None
    version = parts[-1]
    full_name = "-".join(parts[:-1])
    return full_name, version


class ModIndex:
    """包索引的查询封装"""

    def __init__(self, packages):
        self.by_name = {p["full_name"].lower(): p for p in packages}
        self.packages = packages

    def get(self, full_name):
        return self.by_name.get(full_name.lower())

    def latest_version(self, pkg):
        if not pkg or not pkg["versions"]:
            return None
        # API 返回的 versions 按时间倒序，第一个就是最新
        return pkg["versions"][0]

    def find_version(self, full_name, version=None):
        pkg = self.get(full_name)
        if not pkg:
            return None, None
        if version:
            for v in pkg["versions"]:
                if v["version_number"] == version:
                    return pkg, v
        return pkg, self.latest_version(pkg)

    def search(self, keyword):
        kw = keyword.lower().strip()
        if not kw:
            return self.packages
        hits = []
        for p in self.packages:
            if kw in p["full_name"].lower():
                hits.append(p)
                continue
            latest = self.latest_version(p)
            if latest and kw in (latest.get("description") or "").lower():
                hits.append(p)
        return hits

    def popular(self, limit=60):
        def score(p):
            latest = self.latest_version(p)
            return latest.get("downloads", 0) if latest else 0
        return sorted(self.packages, key=score, reverse=True)[:limit]


def resolve_dependencies(index, wanted, log=print):
    """
    递归解析依赖，返回需要安装的包列表（含依赖，已去重）
    wanted: ["BepInEx-BepInExPack", ...] 或 [("名字", "指定版本"), ...]

    冲突处理：同一个包被多个 mod 依赖不同版本时，取版本号最大的那个。
    """
    resolved = {}   # full_name_lower -> (pkg, version_info)
    visiting = set()
    missing = []

    def version_key(v):
        nums = re.findall(r"\d+", v or "")
        return tuple(int(n) for n in nums) if nums else (0,)

    def visit(full_name, version=None, depth=0):
        key = full_name.lower()
        pkg, ver = index.find_version(full_name, version)
        if not pkg or not ver:
            missing.append(full_name)
            return

        if key in resolved:
            # 已解析过：若本次要求更高版本，则替换（取最高版本更安全）
            old_ver = resolved[key][1]["version_number"]
            if version_key(ver["version_number"]) > version_key(old_ver):
                log("  " * depth + f"  ↑ {pkg['full_name']} 版本升级 {old_ver} → {ver['version_number']}")
                resolved[key] = (pkg, ver)
            return

        if key in visiting:
            return
        visiting.add(key)
        resolved[key] = (pkg, ver)

        log("  " + "  " * depth + f"↳ {pkg['full_name']} v{ver['version_number']}")

        for dep in ver.get("dependencies", []):
            dep_name, dep_ver = _parse_dep(dep)
            visit(dep_name, dep_ver, depth + 1)

        visiting.discard(key)

    for item in wanted:
        if isinstance(item, (tuple, list)):
            visit(item[0], item[1])
        else:
            visit(item)

    for m in missing:
        log(f"  [警告] 找不到依赖包: {m}（可能已下架）")

    # BepInExPack 必须最先装
    items = list(resolved.values())
    items.sort(key=lambda pv: 0 if "bepinex" in pv[0]["full_name"].lower() else 1)
    return items


# ============================================================
# 下载
# ============================================================

def download_package(pkg, ver, proxy="", log=print):
    """下载包 zip 到缓存目录，返回本地路径"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", f"{pkg['full_name']}-{ver['version_number']}")
    target = CACHE_DIR / f"{safe}.zip"

    if target.exists() and target.stat().st_size > 0:
        log(f"  使用缓存: {target.name}")
        return target

    url = ver["download_url"]
    s = _session(proxy)
    log(f"  下载 {pkg['full_name']} v{ver['version_number']} …")

    last_err = None
    for attempt in range(3):
        try:
            with s.get(url, timeout=180, stream=True, allow_redirects=True) as r:
                r.raise_for_status()
                tmp = target.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                tmp.replace(target)
            size_mb = target.stat().st_size / 1048576
            log(f"  完成 ({size_mb:.2f} MB)")
            return target
        except Exception as e:
            last_err = e
            log(f"  第 {attempt+1} 次下载失败: {e}")
            time.sleep(1.5)

    raise RuntimeError(f"下载失败: {pkg['full_name']} — {last_err}")


# ============================================================
# 解压路径规划（核心：识别包内结构）
# ============================================================

def _norm(name):
    return name.replace("\\", "/").strip("/")


def plan_extraction(zip_names, full_name, version=""):
    """
    分析 zip 内的文件列表，规划每个文件的目标位置（相对游戏根目录）。

    布局遵循 r2modman / Gale 的社区标准：
      普通 mod  → BepInEx/plugins/<作者-Mod名-版本>/
      patchers  → BepInEx/patchers/<作者-Mod名-版本>/
      配置文件  → BepInEx/config/（必须放这里，否则不生效）
      BepInEx 本体 → 游戏根目录

    返回 list[(zip_entry, dest_relpath)]
    """
    files = [_norm(n) for n in zip_names if not n.endswith("/")]
    files = [f for f in files if f]
    if not files:
        return []

    pkg_dir = f"{full_name}-{version}".rstrip("-") if version else full_name

    # 找出所有文件共同的顶层目录（Thunderstore 包常有一层外壳）
    tops = {f.split("/")[0] for f in files}
    wrapper = ""
    if len(tops) == 1 and any("/" in f for f in files):
        wrapper = next(iter(tops)) + "/"

    plan = []
    for f in files:
        rel = f[len(wrapper):] if wrapper and f.startswith(wrapper) else f
        if not rel:
            continue

        parts = rel.split("/")
        low = [p.lower() for p in parts]
        base = parts[-1].lower()

        # 跳过包根目录的 Thunderstore 元数据（不影响加载，保持目录干净）
        if "/" not in rel and base in META_FILES:
            continue

        handled = False

        # 1) 包里已经自带 BepInEx/ 结构 → 原样保留
        #    （汉化包这类会带 core/、patchers/、plugins/XUnity.AutoTranslator/，
        #      必须按原路径铺，套进子文件夹会导致翻译器找不到资源）
        if "bepinex" in low:
            i = low.index("bepinex")
            plan.append((f, "/".join(parts[i:])))
            continue

        # 2) plugins/ 或 patchers/ → 归入本 mod 的包文件夹
        for seg, root in (("plugins", "BepInEx/plugins"),
                          ("patchers", "BepInEx/patchers")):
            if seg in low[:-1]:
                i = low.index(seg)
                rest = "/".join(parts[i + 1:])
                if rest:
                    plan.append((f, f"{root}/{pkg_dir}/{rest}"))
                handled = True
                break
        if handled:
            continue

        # 3) BepInEx 注入文件，必须位于游戏根目录
        if base in ("doorstop_config.ini", "winhttp.dll", ".doorstop_version"):
            plan.append((f, base))
            continue

        # 4) 其它零散文件（裸 dll、资源文件）→ 统一塞进本 mod 的 plugins 子目录
        plan.append((f, f"BepInEx/plugins/{pkg_dir}/{rel}"))

    return plan


def cleanup_old_versions(game_dir, full_name, keep_version):
    """
    删除同一 mod 的旧版本目录，避免 BepInEx 重复加载两个版本的 dll
    （重复加载是闪退/报错的常见原因）
    """
    removed = []
    plugins = game_dir / "BepInEx" / "plugins"
    if not plugins.exists():
        return removed

    keep = f"{full_name}-{keep_version}"
    pattern = re.compile(rf"^{re.escape(full_name)}-\d+\.\d+.*$", re.IGNORECASE)

    for d in plugins.iterdir():
        if not d.is_dir():
            continue
        if d.name.lower() == keep.lower():
            continue
        if pattern.match(d.name):
            try:
                shutil.rmtree(d)
                removed.append(d.name)
            except Exception:
                pass
    return removed



# ============================================================
# 安装清单（用于卸载/统计）
# ============================================================

def load_installed():
    if not INSTALLED_DB.exists():
        return {}
    try:
        with open(INSTALLED_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_installed(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(INSTALLED_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def prune_missing(game_dir):
    """
    清掉清单里文件已不存在的记录。
    这样把工具文件夹拷到另一台电脑（网吧）时，不会残留旧机器的「已安装」状态。
    """
    db = load_installed()
    if not db or not game_dir:
        return db

    stale = []
    for name, info in db.items():
        files = info.get("files") or []
        if not files:
            continue
        if not any((game_dir / f).exists() for f in files):
            stale.append(name)

    if stale:
        for name in stale:
            del db[name]
        save_installed(db)
    return db


def scan_existing_mods(game_dir, log=None):
    """
    扫描游戏里已经存在的 mod（包括用 r2modman 或手动装的），
    把它们补进安装清单，这样界面上的「已安装」状态才是真实的。
    """
    db = prune_missing(game_dir)
    plugins = game_dir / "BepInEx" / "plugins" if game_dir else None
    if not plugins or not plugins.exists():
        return db

    pat = re.compile(r"^(.+)-(\d+\.\d+\.\d+[^-]*)$")
    added = 0
    for d in plugins.iterdir():
        if not d.is_dir():
            continue
        m = pat.match(d.name)
        if not m:
            continue
        name, ver = m.group(1), m.group(2)
        if any(k.lower() == name.lower() for k in db):
            continue
        files = [str(p.relative_to(game_dir)).replace("\\", "/")
                 for p in d.rglob("*") if p.is_file()]
        db[name] = {
            "version": ver,
            "files": files,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "scan",
        }
        added += 1

    if added:
        save_installed(db)
        if log:
            log(f"已识别出 {added} 个之前装好的 Mod")
    return db


# ============================================================
# 安装 / 卸载
# ============================================================

def backup_dir_once(game_dir, log=print):
    """
    首次安装前，把已有的 BepInEx 目录整体备份一次（只备份一次，避免重复占空间）
    """
    if not game_dir:
        return None
    marker = BACKUP_DIR / ".backed_up"
    if marker.exists():
        return None
    if not (game_dir / "BepInEx").exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / "BepInEx_原始备份"
    log(f"  备份原有 BepInEx 到 {target}")
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(game_dir / "BepInEx", target)
        marker.write_text(str(time.time()), encoding="utf-8")
        return target
    except Exception as e:
        log(f"  [警告] 备份失败: {e}")
        return None


def install_package(pkg, ver, zip_path, game_dir, log=print):
    """
    安装单个包，返回安装的文件相对路径列表
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"安装包不存在: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError(f"安装包已损坏，不是有效的 zip: {zip_path.name}")

    with zipfile.ZipFile(zip_path) as zf:
        plan = plan_extraction(zf.namelist(), pkg["full_name"], ver["version_number"])
        if not plan:
            log(f"  [跳过] {pkg['full_name']} 包里没有可安装的文件")
            return []

        # 先清掉同名 mod 的旧版本，防止重复加载
        old = cleanup_old_versions(game_dir, pkg["full_name"], ver["version_number"])
        if old:
            log(f"  已移除旧版本: {', '.join(old)}")

        written = []
        for entry, dest_rel in plan:
            dest = game_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(dest_rel.replace("\\", "/"))

    log(f"  已安装 {len(written)} 个文件")
    return written


def install_mods(index, wanted, game_dir, proxy="", force_bepinex=False,
                 backup=True, log=print):
    """
    完整安装流程：解析依赖 → 下载 → 解压安装 → 记录清单
    返回 (成功数, 失败列表)
    """
    if not game_dir:
        raise RuntimeError("未定位到游戏目录")

    log("解析依赖关系…")
    todo = resolve_dependencies(index, wanted, log=log)
    if not todo:
        log("没有需要安装的包")
        return 0, []

    if backup:
        backup_dir_once(game_dir, log=log)

    installed_db = load_installed()
    ok, failed = 0, []

    total = len(todo)
    for i, (pkg, ver) in enumerate(todo, 1):
        name = pkg["full_name"]
        log(f"[{i}/{total}] {name} v{ver['version_number']}")

        is_bepinex = "bepinexpack" in name.lower().replace("-", "")
        if is_bepinex and is_bepinex_installed(game_dir) and not force_bepinex:
            log("  BepInEx 已存在，跳过（需要重装请勾选“强制重装 BepInEx”）")
            continue

        try:
            zip_path = download_package(pkg, ver, proxy=proxy, log=log)
            files = install_package(pkg, ver, zip_path, game_dir, log=log)
            installed_db[name] = {
                "version": ver["version_number"],
                "files": files,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_installed(installed_db)
            ok += 1
        except Exception as e:
            log(f"  [失败] {e}")
            failed.append((name, str(e)))

    # 确保 plugins 目录存在
    (game_dir / "BepInEx" / "plugins").mkdir(parents=True, exist_ok=True)

    return ok, failed


# ============================================================
# 离线安装（从本地 zip 文件夹装，全程不联网）
# ============================================================

def read_manifest(zip_path):
    """读包内的 manifest.json（Thunderstore 标准元数据）"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                if n.lower().endswith("manifest.json") and n.count("/") <= 1:
                    raw = z.read(n).decode("utf-8-sig", errors="ignore")
                    return json.loads(raw)
    except Exception:
        pass
    return {}


def scan_local_packages(folder):
    """
    扫描文件夹里的 mod zip。
    文件名需为 Thunderstore 格式：作者-Mod名-版本.zip
    返回 [{full_name, name, version, path, dependencies, description}]
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    pat = re.compile(r"^(.+)-(\d+\.\d+\.\d+[^-]*)\.zip$", re.IGNORECASE)
    pkgs = []
    for f in sorted(folder.glob("*.zip")):
        m = pat.match(f.name)
        if not m:
            continue
        full_name, ver = m.group(1), m.group(2)
        man = read_manifest(f)
        pkgs.append({
            "full_name": full_name,
            "name": man.get("name", full_name.split("-", 1)[-1]),
            "version": ver,
            "path": f,
            "dependencies": man.get("dependencies", []),
            "description": man.get("description", ""),
        })
    return pkgs


def is_bepinex_pack(full_name):
    return "bepinexpack" in full_name.lower().replace("-", "")


def install_local(folder, game_dir, only=None, force_bepinex=False,
                  backup=True, log=print):
    """
    从本地 zip 文件夹安装。BepInEx 会排在最前面。

    only: 只装这些 full_name（None = 全装）
    返回 (成功数, 失败列表)
    """
    if not game_dir:
        raise RuntimeError("未定位到游戏目录")

    pkgs = scan_local_packages(folder)
    if not pkgs:
        raise RuntimeError(f"这个文件夹里没找到 mod 压缩包：{folder}")

    if only:
        want = {n.lower() for n in only}
        pkgs = [p for p in pkgs if p["full_name"].lower() in want]
        if not pkgs:
            raise RuntimeError("选中的 mod 在本地文件夹里都不存在")

    # BepInEx 前置优先
    pkgs.sort(key=lambda p: 0 if is_bepinex_pack(p["full_name"]) else 1)

    log(f"本地包目录: {folder}")
    log(f"找到 {len(pkgs)} 个 mod 包")

    # 依赖完整性检查（本地模式只提示，不联网补）
    have = {p["full_name"].lower() for p in pkgs}
    for p in pkgs:
        for dep in p.get("dependencies", []):
            dep_name, _ = _parse_dep(dep)
            if dep_name.lower() not in have and not is_bepinex_pack(dep_name):
                log(f"  [备注] {p['full_name']} 声明依赖 {dep_name}，本地包里没有")
                log("         （不影响安装，游戏能正常启动就不用管）")

    if backup:
        backup_dir_once(game_dir, log=log)

    installed_db = load_installed()
    ok, failed = 0, []

    total = len(pkgs)
    for i, p in enumerate(pkgs, 1):
        name = p["full_name"]
        log(f"[{i}/{total}] {name} v{p['version']}")

        if is_bepinex_pack(name) and is_bepinex_installed(game_dir) and not force_bepinex:
            log("  BepInEx 已存在，跳过（需要重装请勾选「强制重装 BepInEx」）")
            continue

        try:
            fake_pkg = {"full_name": name}
            fake_ver = {"version_number": p["version"]}
            files = install_package(fake_pkg, fake_ver, p["path"], game_dir, log=log)
            installed_db[name] = {
                "version": p["version"],
                "files": files,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "local",
            }
            save_installed(installed_db)
            ok += 1
        except Exception as e:
            log(f"  [失败] {e}")
            failed.append((name, str(e)))

    (game_dir / "BepInEx" / "plugins").mkdir(parents=True, exist_ok=True)
    return ok, failed


def default_local_dir():
    """
    找工具自带的 mods 文件夹。
    打包成 exe 时 mods 会被塞进 exe 内部，运行时解压到 _MEIPASS，所以两处都要看。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "mods")
    candidates.append(BASE_DIR / "mods")

    for d in candidates:
        if d.exists() and any(d.glob("*.zip")):
            return d
    return None


def uninstall_mod(index, full_name, game_dir, log=print):
    """按安装清单卸载一个 mod"""
    db = load_installed()
    key = None
    for k in db:
        if k.lower() == full_name.lower():
            key = k
            break
    if not key:
        return False, "没有该 mod 的安装记录"

    files = db[key].get("files", [])
    removed = 0
    for rel in files:
        p = game_dir / rel
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except Exception as e:
            log(f"  [警告] 删除失败 {rel}: {e}")

    # 清理空目录
    for rel in files:
        d = (game_dir / rel).parent
        try:
            while d != game_dir and d.exists() and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
        except Exception:
            pass

    del db[key]
    save_installed(db)
    log(f"已卸载 {key}，删除 {removed} 个文件")
    return True, f"已卸载 {key}"


def clear_cache(log=print):
    """清理下载缓存（索引保留）"""
    if CACHE_DIR.exists():
        n = 0
        for f in CACHE_DIR.glob("*.zip"):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
        log(f"已清理 {n} 个缓存包")


def check_dependencies():
    if requests is None:
        raise RuntimeError(
            "缺少 requests 库。请在命令行运行：\n    pip install requests\n"
            "或者直接运行 启动.bat（会自动安装）"
        )
