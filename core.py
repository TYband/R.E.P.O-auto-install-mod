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
import html
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


def _release_bundled_assets():
    """
    打包成 exe 后，把内置的索引种子和 mod 包释放到 exe 旁边。

    不释放的话，exe 第一次运行 data/ 是空的 —— 界面没有 Mod 列表，
    而且 Thunderstore 在国内经常连不上，用户会以为工具坏了。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", str(BASE_DIR)))

    # 1) 索引种子 → data/index.json（只在没有缓存时放一次）
    if not INDEX_CACHE.exists():
        for name in ("index.json.gz", "index.json"):
            src = meipass / "seed" / name
            if not src.exists():
                continue
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                if name.endswith(".gz"):
                    import gzip
                    with gzip.open(src, "rb") as fi, open(INDEX_CACHE, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
                else:
                    shutil.copy2(src, INDEX_CACHE)
                break
            except Exception:
                pass

    # 2) 内置 mods → exe 旁边的 mods/（要往里面加包，必须落在可写目录）
    bundled_mods = meipass / "mods"
    target = BASE_DIR / "mods"
    if bundled_mods.exists() and not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            for z in bundled_mods.glob("*.zip"):
                shutil.copy2(z, target / z.name)
        except Exception:
            pass


_release_bundled_assets()

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
    """精简索引字段，把 34MB 的原始索引压到合理体积（保留图标等展示用字段）"""
    versions = []
    for v in pkg.get("versions", []):
        versions.append({
            "version_number": v.get("version_number"),
            "download_url": v.get("download_url"),
            "dependencies": v.get("dependencies", []),
            "description": v.get("description", ""),
            "downloads": v.get("downloads", 0),
            "date_created": v.get("date_created", ""),
            "icon": v.get("icon", ""),
            "website_url": v.get("website_url", ""),
            "file_size": v.get("file_size", 0),
        })
    return {
        "full_name": pkg.get("full_name"),
        "name": pkg.get("name"),
        "owner": pkg.get("owner"),
        "package_url": pkg.get("package_url"),
        "versions": versions,
    }


INDEX_SCHEMA = 2      # 索引结构版本；字段变化时递增，旧缓存会自动失效


def _index_is_current(data):
    """判断缓存索引是否包含当前需要的字段"""
    if not data or not isinstance(data, list):
        return False
    first = data[0]
    if not first.get("versions"):
        return False
    v0 = first["versions"][0]
    return all(k in v0 for k in ("icon", "website_url", "file_size"))


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
                if _index_is_current(data):
                    log(f"使用本地缓存索引（{len(data)} 个 mod，{int(age/60)} 分钟前更新）")
                    return data
                log("本地索引缺少图标字段，重新下载…")
            except Exception:
                pass

    log("正在从 Thunderstore 下载 mod 索引，首次需要一会儿…")
    s = _session(proxy)
    try:
        resp = s.get(INDEX_URL, timeout=120)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        # 下载失败时退回旧缓存，总比列表空白强
        if INDEX_CACHE.exists():
            try:
                with open(INDEX_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log(f"[警告] 索引下载失败（{type(e).__name__}），改用本地旧索引")
                return data
            except Exception:
                pass
        raise

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
    首次安装前，把已有的 BepInEx 目录整体备份一次（只备份一次，避免重复占空间）。

    备份按游戏目录名隔离子文件夹，避免切换游戏时互相覆盖、或"已备份"标记误判。
    """
    if not game_dir:
        return None
    game_dir = Path(game_dir)
    tag = game_dir.name or "game"
    bdir = BACKUP_DIR / tag
    marker = bdir / ".backed_up"
    if marker.exists():
        return None
    if not (game_dir / "BepInEx").exists():
        return None

    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / "BepInEx_原始备份"
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


def load_index(force=False, proxy="", log=print):
    """载入 Thunderstore 索引（优先用本地缓存）"""
    return ModIndex(fetch_index(force=force, proxy=proxy, log=log))


def find_missing_deps(pkgs, index, log=print):
    """
    计算本地包集合缺哪些依赖（递归，含依赖的依赖）。

    返回 [(pkg, ver), ...]，即需要额外下载的包。
    本地已有的包（不论版本）一律视为满足，避免重复下载。
    """
    have = {p["full_name"].lower() for p in pkgs}
    needed = {}

    def version_key(v):
        nums = re.findall(r"\d+", v or "")
        return tuple(int(n) for n in nums) if nums else (0,)

    def visit(full_name, version=None, depth=0):
        key = full_name.lower()
        if key in have:
            return
        pkg, ver = index.find_version(full_name, version)
        if not pkg or not ver:
            log("  " * depth + f"[警告] 依赖包不存在或已下架: {full_name}")
            return

        if key in needed:
            if version_key(ver["version_number"]) > version_key(needed[key][1]["version_number"]):
                needed[key] = (pkg, ver)
            return

        needed[key] = (pkg, ver)
        log("  " * depth + f"缺失 → {pkg['full_name']} v{ver['version_number']}")

        for dep in ver.get("dependencies", []):
            dn, dv = _parse_dep(dep)
            visit(dn, dv, depth + 1)

    for p in pkgs:
        for dep in p.get("dependencies", []):
            dn, dv = _parse_dep(dep)
            visit(dn, dv)

    return list(needed.values())


def install_local(folder, game_dir, only=None, force_bepinex=False,
                  backup=True, auto_fetch_deps=False, proxy="",
                  save_deps=True, log=print):
    """
    从本地 zip 文件夹安装。BepInEx 会排在最前面。

    only            : 只装这些 full_name（None = 全装）
    auto_fetch_deps : 联网自动补齐缺失依赖（需要网络；离线时自动降级为仅提示）
    save_deps       : 把下载来的依赖 zip 存回 mods/ 文件夹，下次断网也能用

    返回 (成功数, 失败列表)
    """
    folder = Path(folder)
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

    log(f"本地包目录: {folder}")
    log(f"找到 {len(pkgs)} 个 mod 包")

    # 统一成待安装条目
    items = [{"full_name": p["full_name"], "version": p["version"],
              "path": p["path"], "source": "local"} for p in pkgs]

    # ---- 依赖检查与补齐 ----
    if auto_fetch_deps:
        log("")
        log("检查依赖关系…")
        try:
            index = load_index(proxy=proxy, log=log)
            missing = find_missing_deps(pkgs, index, log=log)
        except Exception as e:
            log(f"[提示] 无法联网检查依赖（{e}），本次仅安装本地包")
            missing = []

        if missing:
            log(f"发现 {len(missing)} 个缺失依赖，开始下载…")
            for pkg, ver in missing:
                try:
                    zp = download_package(pkg, ver, proxy=proxy, log=log)
                    items.append({"full_name": pkg["full_name"],
                                  "version": ver["version_number"],
                                  "path": zp, "source": "dependency"})
                    if save_deps:
                        dst = folder / f"{pkg['full_name']}-{ver['version_number']}.zip"
                        if not dst.exists():
                            shutil.copy2(zp, dst)
                            log(f"  已存入本地包目录: {dst.name}")
                except Exception as e:
                    log(f"  [失败] 依赖 {pkg['full_name']}: {e}")
        else:
            log("依赖完整，无需补装")
    else:
        # 不联网时，只提示缺哪些
        have = {p["full_name"].lower() for p in pkgs}
        reported = set()
        for p in pkgs:
            for dep in p.get("dependencies", []):
                dep_name, _ = _parse_dep(dep)
                key = dep_name.lower()
                if key in have or key in reported or is_bepinex_pack(dep_name):
                    continue
                reported.add(key)
                log(f"  [缺少依赖] {dep_name} ← 被 {p['full_name']} 需要")
        if reported:
            log("  → 勾选「自动补齐缺失依赖」可联网自动装好这些包")

    # BepInEx 前置优先
    items.sort(key=lambda it: 0 if is_bepinex_pack(it["full_name"]) else 1)

    if backup:
        backup_dir_once(game_dir, log=log)

    installed_db = load_installed()
    ok, failed = 0, []

    total = len(items)
    for i, it in enumerate(items, 1):
        name = it["full_name"]
        tag = "依赖" if it["source"] == "dependency" else "本地"
        log(f"[{i}/{total}] ({tag}) {name} v{it['version']}")

        if is_bepinex_pack(name) and is_bepinex_installed(game_dir) and not force_bepinex:
            log("  BepInEx 已存在，跳过（需要重装请勾选「强制重装 BepInEx」）")
            continue

        try:
            fake_pkg = {"full_name": name}
            fake_ver = {"version_number": it["version"]}
            files = install_package(fake_pkg, fake_ver, it["path"], game_dir, log=log)
            installed_db[name] = {
                "version": it["version"],
                "files": files,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": it["source"],
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
    打包成 exe 时 mods 会被塞进 exe 内部，运行时先释放到 exe 旁边，
    所以优先看 exe 旁边（可写，能继续往里加包），没有再退回 _MEIPASS。
    """
    candidates = [BASE_DIR / "mods"]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "mods")

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


# ============================================================
# 翻译（免费源，不需要 API Key）
# ============================================================

TRANS_CACHE_PATH = DATA_DIR / "translate_cache.json"
TRANS_CACHE_MAX = 3000          # 缓存条数上限，超了丢最旧的

_trans_mem = None               # 内存缓存，避免每次点都读盘


def _load_trans_cache():
    global _trans_mem
    if _trans_mem is not None:
        return _trans_mem
    data = {}
    try:
        if TRANS_CACHE_PATH.exists():
            with open(TRANS_CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = raw
    except Exception:
        data = {}
    _trans_mem = data
    return data


def _save_trans_cache(cache):
    global _trans_mem
    _trans_mem = cache
    try:
        if len(cache) > TRANS_CACHE_MAX:
            for k in list(cache.keys())[:len(cache) - TRANS_CACHE_MAX]:
                cache.pop(k, None)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRANS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _tr_google(text, target, proxy, timeout=8):
    """Google 翻译的免费网页接口（client=gtx），无需 Key"""
    r = _session(proxy).get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out.strip()


def _tr_mymemory(text, target, proxy, timeout=8):
    """MyMemory 免费接口，匿名每天 5000 字符，国内可直连"""
    r = _session(proxy).get(
        "https://api.mymemory.translated.net/get",
        params={"q": text[:480], "langpair": f"en|{target}"},
        timeout=timeout,
    )
    r.raise_for_status()
    d = r.json()
    out = ((d.get("responseData") or {}).get("translatedText") or "").strip()
    if not out or "MYMEMORY WARNING" in out.upper() or "QUOTA" in out.upper():
        raise RuntimeError("免费额度用尽或返回异常")
    return html.unescape(out)


TRANS_PROVIDERS = [("google", _tr_google), ("mymemory", _tr_mymemory)]

_GOOGLE_OK = None               # 启动时探测结果，None = 还没测
_AUTO_PROXY = ""                # 探测到的本机代理，仅用于翻译

# 常见本地代理端口（Clash / V2Ray / Shadowsocks）
_LOCAL_PROXY_PORTS = ("7897", "7890", "10809", "10808", "1080")

# 这些是专有名词，拆开反而翻不出来（BepInExPack 不能变成 Bep In Ex Pack）
_TRANS_KEEP = ("bepinex", "repolib", "menulib", "lethallib", "xunity",
               "autotranslator")


def probe_translation(proxy="", log=None):
    """
    后台探一下 Google 能不能连上，能连就优先用它（译文质量比 MyMemory 好）。
    没配代理时顺手试试本机常见代理端口，通了就自动借用，只影响翻译。
    """
    global _GOOGLE_OK, _AUTO_PROXY
    if _GOOGLE_OK is not None:
        return _GOOGLE_OK

    if proxy:
        try:
            _tr_google("hello", "zh-CN", proxy, timeout=3)
            _GOOGLE_OK, _AUTO_PROXY = True, proxy
            return True
        except Exception:
            pass
    else:
        try:
            _tr_google("hello", "zh-CN", "", timeout=3)
            _GOOGLE_OK = True
            return True
        except Exception:
            pass
        for port in _LOCAL_PROXY_PORTS:
            p = f"http://127.0.0.1:{port}"
            try:
                _tr_google("hello", "zh-CN", p, timeout=3)
                _GOOGLE_OK, _AUTO_PROXY = True, p
                if log:
                    log(f"翻译：检测到本机代理 {p}，走 Google（译文更准）")
                return True
            except Exception:
                continue

    _GOOGLE_OK = False
    if log:
        log("翻译：Google 连不上，改用 MyMemory 免费接口")
    return False


def humanize_name(text):
    """
    把 MoreShopItems_Updated 这种标识符拆成可翻译的自然短语。
    专有名词（BepInEx / REPOLib / MenuLib）保持原样。
    """
    text = re.sub(r"[_\-]+", " ", text or "").strip()
    out = []
    for word in text.split():
        low = word.lower()
        if len(word) >= 8 and not any(k in low for k in _TRANS_KEEP):
            word = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", word)
        out.append(word)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


# Mod 名常用词表：整句都是这些词时直接拼中文，比机翻准，而且不用联网。
# 词表按 Thunderstore 上 R.E.P.O. 社区 6522 个包的实际用词统计出来的。
_NAME_GLOSSARY = {
    # 功能词
    "the": "", "of": "的", "for": "用于", "and": "与", "in": "内", "by": "由",
    "with": "带", "my": "我的", "all": "全部", "no": "无", "not": "不",
    "more": "更多", "plus": "增强", "better": "更好的", "best": "最佳",
    "custom": "自定义", "random": "随机", "infinite": "无限", "ultimate": "终极",
    "extra": "额外", "new": "新", "old": "旧", "fixed": "修复", "fix": "修复",
    "test": "测试", "hidden": "隐藏", "shared": "共享", "silly": "搞怪",
    # 游戏内容
    "mod": "模组", "mods": "模组", "pack": "包", "modpack": "整合包",
    "repo": "R.E.P.O.", "item": "物品", "items": "物品", "stuff": "道具",
    "shop": "商店", "valuable": "贵重物品", "valuables": "贵重物品",
    "value": "价值", "gem": "宝石", "gold": "金币", "money": "金钱",
    "cart": "推车", "box": "箱子", "truck": "卡车", "drone": "无人机",
    "gun": "枪", "weapon": "武器", "weapons": "武器", "melee": "近战",
    "enemy": "敌人", "enemies": "敌人", "monster": "怪物", "monsters": "怪物",
    "hunter": "猎人", "huntsman": "猎人", "boss": "首领", "npc": "NPC",
    "player": "玩家", "players": "玩家", "friend": "朋友", "friends": "朋友",
    "team": "团队", "lobby": "大厅", "host": "房主", "server": "服务器",
    "game": "游戏", "level": "关卡", "levels": "关卡", "map": "地图",
    "maps": "地图", "arena": "竞技场", "extraction": "撤离", "spawn": "生成",
    "revive": "复活", "death": "死亡", "life": "生命", "health": "生命值",
    "hp": "生命值", "stamina": "体力", "speed": "速度", "damage": "伤害",
    "upgrade": "升级", "upgrades": "升级", "buff": "增益", "quest": "任务",
    "unlock": "解锁", "achievement": "成就", "score": "分数", "coin": "硬币",
    # 外观与音效
    "cosmetic": "外观", "cosmetics": "外观", "hat": "帽子", "hats": "帽子",
    "robe": "长袍", "skin": "皮肤", "skins": "皮肤", "plush": "玩偶",
    "paint": "涂装", "painting": "画作", "paintings": "画作", "theme": "主题",
    "music": "音乐", "song": "歌曲", "songs": "歌曲", "sound": "音效",
    "sounds": "音效", "audio": "音频", "boombox": "音响", "voice": "语音",
    "chat": "聊天", "meme": "梗", "joke": "玩笑",
    # 界面与系统
    "menu": "菜单", "ui": "界面", "hud": "HUD", "config": "配置",
    "setting": "设置", "settings": "设置", "key": "按键", "keybind": "按键绑定",
    "translation": "汉化", "translate": "翻译", "language": "语言",
    "replacer": "替换", "remover": "移除", "delete": "删除", "save": "存档",
    "loader": "加载器", "lib": "库", "library": "库", "api": "API",
    "sync": "同步", "multiplayer": "联机", "solo": "单人", "bot": "机器人",
    "vanilla": "原版", "dlc": "DLC", "cat": "猫", "duck": "鸭子",
    "mimic": "模仿者", "late": "延迟", "join": "加入", "head": "头部",
    "body": "身体", "hand": "手", "eye": "眼睛", "scale": "体型",
    "size": "大小", "gravity": "重力", "jump": "跳跃", "sprint": "冲刺",
    "flashlight": "手电筒", "camera": "摄像机", "recoil": "后坐力",
    "empress": "女皇", "king": "国王", "queen": "女王", "minecraft": "我的世界",
}


def translate_name(name):
    """
    按词表拼中文名：MoreShopItems → 更多商店物品。
    有任何一个词不认识就返回空字符串，交给在线翻译兜底。
    """
    words = re.findall(r"[A-Za-z]+|\d+", humanize_name(name))
    if not words:
        return ""
    out = []
    for w in words:
        if w.isdigit():
            out.append(w)
            continue
        zh = _NAME_GLOSSARY.get(w.lower())
        if zh is None:
            return ""
        out.append(zh)
    return "".join(out)


def translate_text(text, target="zh-CN", proxy="", log=None):
    """
    翻译一段英文。带本地缓存，翻过的句子以后秒出且断网可用。
    依次尝试免费源，全都失败返回空字符串（不抛异常）。
    """
    text = (text or "").strip()
    if not text:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):      # 已经是中文
        return text

    cache = _load_trans_cache()
    key = f"{target}|{text}"
    if key in cache:
        return cache[key]

    # 有代理（配置的或探测到的）或 Google 直连可用 → 先走 Google，否则先走 MyMemory
    eff = proxy or _AUTO_PROXY
    use_google = bool(eff) or bool(_GOOGLE_OK)
    order = TRANS_PROVIDERS if use_google else list(reversed(TRANS_PROVIDERS))
    for pname, fn in order:
        try:
            out = fn(text, target, eff)
            if out and out.lower() != text.lower():
                cache[key] = out
                _save_trans_cache(cache)
                if log:
                    log(f"  翻译[{pname}] {text[:36]} → {out[:36]}")
                return out
        except Exception as e:
            if log:
                log(f"  翻译源 {pname} 不可用：{type(e).__name__}")
    return ""


def translate_package(name, desc, proxy="", log=None):
    """
    翻译一个 Mod 的名字和简介，返回 (中文名, 中文简介)。
    名字先查词表（MoreShopItems_Updated → 更多商店物品），查不到再联网翻。
    """
    tr_name = translate_name(name)
    if not tr_name:
        tr_name = translate_text(humanize_name(name), proxy=proxy, log=log)
    elif log:
        log(f"  翻译[词表] {name} → {tr_name}")
    tr_desc = translate_text((desc or "")[:480], proxy=proxy, log=log) if desc else ""
    return tr_name, tr_desc
