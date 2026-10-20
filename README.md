# R.E.P.O. 一键装 Mod

> 给《R.E.P.O.》用的图形化 Mod 安装器：自动装 BepInEx 前置、自动补依赖、自动认目录结构。到网吧换个机器，点一下就能装好整套 Mod。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)
![Mods](https://img.shields.io/badge/Thunderstore-6500%2B%20mods-orange)

![界面截图](docs/screenshot.png)

---

## 这个工具解决什么问题

手动装 R.E.P.O. 的 Mod 有三层坑，每一步都得自己来：

**1. 得先装 BepInEx 框架**
`BepInExPack` 压缩包解开后，要把 `BepInEx` 文件夹、`doorstop_config.ini`、`winhttp.dll` 一起拖进游戏根目录。少一个就不生效。

**2. Mod 之间有依赖链**
装 `REPOLib` 要 `MenuLib`，装 `MoreShopItems` 要 `NoItemSpawnLimit`……漏装一个，游戏启动就报错。

**3. 每个包的内部结构都不一样**

| 包 | 内部结构 | 该放哪 |
|---|---|---|
| `BepInEx-BepInExPack` | `BepInExPack/BepInEx/...` + `winhttp.dll` | 游戏根目录（要剥掉外壳文件夹） |
| `Zehs-REPOLib` | `plugins/REPOLib.dll` | `BepInEx/plugins/` |
| `clay-BetterMap` | `BetterMap.dll`（直接在根） | `BepInEx/plugins/<包名>/` |
| `PxntxrezStudio-No_Save_Delete` | `Plugins/NoSaveDelete.dll`（大写 P） | `BepInEx/plugins/<包名>/` |
| `toored-Chinese_Translation` | 自带整套 `BepInEx/core`、`patchers`、`config/Translation` | 原样铺到游戏根目录 |

最后一个最容易翻车：汉化包如果被当成普通 Mod 塞进 `plugins/<包名>/` 子文件夹，翻译器就找不到自己的配置和翻译文件，**汉化直接失效**。

这些判断，工具全包了。

---

## 特性

- **自动定位游戏** —— 读注册表 + 解析 Steam 的 `libraryfolders.vdf`，多硬盘 Steam 库也能找到
- **自动装 BepInEx** —— 首次安装自动铺好前置框架，已装则跳过（不会覆盖你的配置）
- **依赖递归解析** —— 自动补齐 REPOLib / MenuLib 之类的依赖，版本冲突取最高
- **结构智能识别** —— 按包内实际结构决定落点，`plugins/`、`Plugins/`、`patchers/`、自带 `BepInEx/` 都能正确归类
- **离线安装** —— `mods/` 目录放好 zip，网吧不联网也能装
- **在线安装** —— 界面直接浏览 Thunderstore 全部 6500+ Mod，搜名字勾选即可
- **自动清旧版本** —— 同一个 Mod 装了新旧两个版本会导致重复加载、游戏闪退，每次安装自动清理
- **安装前备份** —— 首次安装会把原有 `BepInEx` 备份到 `data/backups/`
- **精确卸载** —— 按安装清单逐个文件删除，不留垃圾

---

## 快速开始

### 方式一：用打包好的 EXE（网吧推荐）

在任意有 Python 的电脑上双击 `build-exe.bat`，得到 `dist/REPO-Mod-Installer.exe`。
Mod 包会被打进 exe 内部，**拷这一个文件就能用，目标机器不需要装 Python**。

### 方式二：源码运行

```bash
git clone https://github.com/<你的用户名>/repo-mod-installer.git
cd repo-mod-installer
pip install -r requirements.txt
```

双击 `启动.bat`（会自动挑选带 tkinter 的 Python 解释器），或直接：

```bash
python mod_installer.py
```

### 方式三：离线安装（网吧不联网）

1. 事先在 [Thunderstore](https://thunderstore.io/c/repo/) 下载需要的 zip
2. 按 `作者-Mod名-版本.zip` 命名，放进 `mods/` 文件夹
3. 把整个项目文件夹拷到 U 盘，到网吧双击 `启动.bat`
4. 点「⚡ 一键离线安装」

---

## 界面说明

| 区域 | 说明 |
|---|---|
| **① 游戏位置** | 自动检测 R.E.P.O. 安装目录；检测不到可手动指定 |
| **① 本地 Mod 包** | 显示 `mods/` 里的包数量，「⚡ 一键离线安装」= 全部装一遍 |
| **② 选择 Mod** | 联网浏览 Thunderstore；点行即勾选，支持搜索、热门 Top60、只看已安装 |
| **③ 运行日志** | 每一步在做什么，出错原因也在这里 |

| 按钮 | 作用 |
|---|---|
| ⚡ 一键离线安装 | 装 `mods/` 里的全部包（不联网） |
| 安装勾选的 Mod | 装列表里勾选的（联网，自动补依赖） |
| 一键安装我的配置 | 装 `config.json` 中 `auto_install_list` 定义的整套 |
| 卸载选中 | 按安装记录精确删除 |
| 清理下载缓存 | 清空 `data/cache/` |
| 强制重装 BepInEx | BepInEx 损坏或想换版本时勾选 |

---

## 配置

复制 `config.example.json` 为 `config.json` 后修改（`config.json` 含本机路径，已在 `.gitignore` 中排除）：

```json
{
    "game_path": "",
    "local_mods_dir": "",
    "proxy": "",
    "backup_before_install": true,
    "auto_install_list": [
        "Zehs-REPOLib",
        "clay-BetterMap"
    ]
}
```

| 字段 | 说明 |
|---|---|
| `game_path` | 游戏目录。**留空 = 自动检测**（网吧盘符不固定，建议留空） |
| `local_mods_dir` | 离线包目录。留空 = 使用项目自带的 `mods/` |
| `proxy` | 下载代理，如 `http://127.0.0.1:7890`。Thunderstore 服务器在境外，网络慢时可填 |
| `backup_before_install` | 首次安装前是否备份原有 BepInEx |
| `auto_install_list` | 「一键安装我的配置」的 Mod 清单，填 Thunderstore 全名（`作者-Mod名`） |

---

## 工作原理

```
                    ┌─────────────────┐
                    │  Thunderstore   │  在线模式：官方 API
                    │  c/repo (6522)  │
                    └────────┬────────┘
                             │
   ┌──────────────┐          ▼          ┌──────────────────┐
   │ mods/*.zip   │───►  包解析与依赖  ──►│  结构识别与落点  │
   │ 离线模式     │     递归解析依赖链   │  plugins/patchers │
   └──────────────┘                      └────────┬─────────┘
                                                  │
                    ┌─────────────────┐           ▼
                    │  Steam 库定位   │   ┌───────────────┐
                    │ appmanifest_    │──►│ 游戏根目录    │
                    │ 3241660.acf     │   │ BepInEx/...   │
                    └─────────────────┘   └───────────────┘
```

几个关键实现点：

- **游戏定位**：从注册表读 `SteamPath`，再解析 `steamapps/libraryfolders.vdf` 拿到所有库目录，按 `appmanifest_3241660.acf` 里的 `installdir` 定位游戏
- **依赖解析**：从包的 `manifest.json` 读 `dependencies`（形如 `BepInEx-BepInExPack-5.4.2100`），递归展开；同名包版本冲突时取版本号最大者
- **结构识别**：逐文件判断落点，优先级为 `自带 BepInEx/ 结构` > `plugins/ 或 patchers/ 前缀` > `doorstop/winhttp 根文件` > 其余归入 `BepInEx/plugins/<作者-Mod名-版本>/`
- **布局标准**：遵循 **r2modman / Gale** 的社区约定，因此与这两个 Mod 管理器完全兼容，不会出现重复加载

---

## 项目结构

```
repo-mod-installer/
├── 启动.bat               # 双击启动（自动挑选可用 Python）
├── build-exe.bat          # 打包成免 Python 的 exe
├── mod_installer.py       # 图形界面（Tkinter）
├── core.py                # 核心逻辑：定位/解析/解包/安装
├── config.example.json    # 配置模板
├── requirements.txt
├── LICENSE
├── docs/
│   └── screenshot.png
├── mods/                  # 离线 mod 包（*.zip 不入库）
│   └── README.md
└── data/                  # 运行时生成，不入库
    ├── index.json         # Mod 索引缓存
    ├── installed.json     # 已安装清单
    ├── cache/             # 下载缓存
    └── backups/           # BepInEx 备份
```

---

## 常见问题

**Q：装完游戏闪退？**
最常见原因是同一个 Mod 存在两个版本，BepInEx 会同时加载然后崩溃。工具每次安装会自动删掉同名 Mod 的旧版本目录。
若仍闪退，勾选「强制重装 BepInEx」再装一遍；`data/backups/` 里有首次安装前的原始备份。

**Q：怎么确认 Mod 装好了？**
启动游戏时会弹出一个黑色命令行窗口，那是 BepInEx 在加载插件，会列出加载的 dll。进游戏后按 **F1** 可查看 Mod 配置项。

**Q：汉化不生效？**
检查两个位置都在：`BepInEx/patchers/Font.dll`（字体补丁）和 `BepInEx/config/Translation/zh/`（翻译文件）。
汉化包必须原样铺到游戏根目录，不能被塞进 `plugins/` 子文件夹 —— 本工具已正确处理。

**Q：双击 `启动.bat` 报一堆 `'xx' 不是内部或外部命令`？**
旧版本脚本用 LF 换行导致 cmd 解析错乱，已修复为 CRLF + 纯 ASCII。若仍遇到，直接用 `build-exe.bat` 生成的 exe。

**Q：日志提示某 Mod 依赖某个包，本地没有，要紧吗？**
不要紧，只是提示。多数 Mod 缺少可选依赖也能正常运行，游戏能正常启动就不用管。
想补齐的话用在线模式安装，会自动拉全依赖。

**Q：支持其他游戏吗？**
目前只针对 R.E.P.O.（AppID `3241660`）调优。核心逻辑是通用的，改 `core.py` 里的 `REPO_APPID` 和 Thunderstore 社区名，理论上可用于其他 Unity + BepInEx 的游戏，但未做验证。

**Q：Linux / macOS 能用吗？**
不能。游戏定位依赖 Windows 注册表和 Steam 的 Windows 目录结构，界面用 Tkinter。

---

## 免责声明

- 本项目**不包含任何 Mod 文件**。Mod 版权归各自作者所有，请遵守其授权条款，不要重新分发。
- 本项目不隶属于 R.E.P.O. 的开发商 semiwork，也不隶属于 Thunderstore 或 BepInEx。
- Mod 会改变游戏行为。多人联机时请与队友确认，部分 Mod 可能被视为影响平衡。
- 使用本工具造成的存档、账号或游戏文件问题，由使用者自行承担。

## 致谢

- [BepInEx](https://github.com/BepInEx/BepInEx) —— Unity 游戏插件框架（LGPL-2.1）
- [Thunderstore](https://thunderstore.io/c/repo/) —— R.E.P.O. 的 Mod 社区与开放 API
- [r2modman](https://github.com/ebkr/r2modmanPlus) —— 目录布局参考

## License

[MIT](LICENSE)

---

<details>
<summary><b>English</b></summary>

### R.E.P.O. One-Click Mod Installer

A Windows GUI tool that installs mods for **R.E.P.O.** (Steam AppID `3241660`) with zero manual file shuffling.

**The problem it solves**

Installing mods for R.E.P.O. by hand involves three tedious layers: setting up the BepInEx loader, chasing transitive dependencies between mods, and figuring out that every package has a *different* internal layout (some ship `plugins/x.dll`, some put the dll at the archive root, some ship a full `BepInEx/` tree with `core/`, `patchers/` and `config/Translation/`).

**What it does**

- Locates the game via the Windows registry and Steam's `libraryfolders.vdf` (multi-drive libraries supported)
- Installs BepInEx automatically, skipping it if already present
- Resolves the dependency graph recursively, picking the highest version on conflicts
- Classifies each archive's layout and extracts every file to its correct destination
- Works fully **offline** from a local `mods/` folder — ideal for internet cafés
- Removes stale versions of the same mod (a common cause of crashes)
- Backs up the existing `BepInEx` directory before the first install
- Uses the **r2modman / Gale** directory convention, so it stays compatible with those managers

**Quick start**

```bash
git clone https://github.com/<your-name>/repo-mod-installer.git
cd repo-mod-installer
pip install -r requirements.txt
python mod_installer.py
```

Or run `build-exe.bat` once on a machine with Python to produce a standalone `REPO-Mod-Installer.exe` with the mod packages bundled inside — then just copy that single file to a USB drive.

Requires Windows and Python 3.8+ (with tkinter). Only R.E.P.O. is officially supported.

</details>
