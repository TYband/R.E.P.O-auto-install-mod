# -*- coding: utf-8 -*-
"""
R.E.P.O. 一键装 Mod —— 图形界面
==============================
双击 启动.bat 即可运行。
"""

import os
import sys
import queue
import threading
import webbrowser
import subprocess
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    print("=" * 62)
    print(" 无法启动界面：当前 Python 缺少 tkinter 组件")
    print("")
    print(" 解决办法（任选一个）：")
    print("   1. 重新安装 Python，安装时勾选「tcl/tk and IDLE」")
    print("   2. 在自己电脑上运行 打包成exe.bat，用生成的 exe 启动")
    print("=" * 62)
    try:
        input("\n按回车键退出…")
    except EOFError:
        pass
    raise SystemExit(1)

import core


FONT = "Microsoft YaHei UI"
MONO = "Consolas"


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("R.E.P.O. 一键装 Mod")
        self.root.geometry("980x700")
        self.root.minsize(820, 600)

        self.cfg = core.load_config()
        self.index = None
        self.game_dir = None
        self.local_dir = None
        self.selected = set()        # 勾选的 full_name
        self.busy = False
        self.log_queue = queue.Queue()

        self._setup_style()
        self._build()
        self._init_local_dir()
        self._detect_game()
        self._load_index_from_cache()
        self.root.after(120, self._drain_log)

    # -------------------------------------------------- 样式
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", font=(FONT, 9))
        style.configure("Treeview", rowheight=26, font=(FONT, 9),
                        fieldbackground="white", background="white")
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"))
        style.configure("Title.TLabel", font=(FONT, 15, "bold"))
        style.configure("Sub.TLabel", font=(FONT, 9), foreground="#666666")
        style.configure("Ok.TLabel", font=(FONT, 9), foreground="#1a7f37")
        style.configure("Bad.TLabel", font=(FONT, 9), foreground="#c0392b")
        style.configure("Go.TButton", font=(FONT, 10, "bold"))

    # -------------------------------------------------- 界面
    def _build(self):
        # ===== 顶部：游戏目录 =====
        top = ttk.LabelFrame(self.root, text=" ① 游戏位置 ", padding=10)
        top.pack(fill="x", padx=12, pady=(12, 6))

        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="R.E.P.O. 目录:").pack(side="left")
        self.path_var = tk.StringVar(value="检测中…")
        ttk.Entry(row, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="自动检测", command=self._detect_game).pack(side="left", padx=2)
        ttk.Button(row, text="手动选择", command=self._pick_game).pack(side="left", padx=2)
        ttk.Button(row, text="打开目录", command=self._open_game_dir).pack(side="left", padx=2)

        self.status_var = tk.StringVar(value="")
        self.status_lbl = ttk.Label(top, textvariable=self.status_var, style="Sub.TLabel")
        self.status_lbl.pack(anchor="w", pady=(6, 0))

        # ---- 本地离线包 ----
        sep = ttk.Separator(top, orient="horizontal")
        sep.pack(fill="x", pady=8)

        row2 = ttk.Frame(top)
        row2.pack(fill="x")
        ttk.Label(row2, text="本地 Mod 包:").pack(side="left")
        self.local_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.local_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="选择文件夹", command=self._pick_local_dir).pack(side="left", padx=2)
        self.btn_offline = ttk.Button(row2, text="⚡ 一键离线安装", style="Go.TButton",
                                      command=self._install_offline)
        self.btn_offline.pack(side="left", padx=(6, 2))

        self.local_hint = ttk.Label(top, text="", style="Sub.TLabel")
        self.local_hint.pack(anchor="w", pady=(4, 0))

        # ===== 中部：mod 列表 =====
        mid = ttk.LabelFrame(self.root, text=" ② 选择 Mod ", padding=10)
        mid.pack(fill="both", expand=True, padx=12, pady=6)

        bar = ttk.Frame(mid)
        bar.pack(fill="x", pady=(0, 6))

        ttk.Label(bar, text="搜索:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var, width=28)
        ent.pack(side="left", padx=(4, 8))
        ent.bind("<KeyRelease>", lambda e: self._refresh_list())

        ttk.Button(bar, text="全部 Mod", command=self._show_all).pack(side="left", padx=2)
        ttk.Button(bar, text="热门 Top60", command=self._show_popular).pack(side="left", padx=2)
        ttk.Button(bar, text="已安装", command=self._show_installed).pack(side="left", padx=2)
        ttk.Button(bar, text="刷新索引", command=self._refresh_index).pack(side="left", padx=2)

        self.count_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.count_var, style="Sub.TLabel").pack(side="right")

        # 列表
        wrap = ttk.Frame(mid)
        wrap.pack(fill="both", expand=True)

        cols = ("sel", "name", "ver", "dl", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("sel", text="选")
        self.tree.heading("name", text="Mod 名称")
        self.tree.heading("ver", text="版本")
        self.tree.heading("dl", text="下载量")
        self.tree.heading("state", text="状态")

        self.tree.column("sel", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=440, anchor="w")
        self.tree.column("ver", width=110, anchor="center", stretch=False)
        self.tree.column("dl", width=90, anchor="e", stretch=False)
        self.tree.column("state", width=110, anchor="center", stretch=False)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", lambda e: self._toggle_current())

        self.tree.tag_configure("installed", foreground="#1a7f37")
        self.tree.tag_configure("notinstalled", foreground="#333333")

        # ===== 按钮区 =====
        act = ttk.Frame(self.root, padding=(12, 2))
        act.pack(fill="x")

        self.btn_install = ttk.Button(act, text="安装勾选的 Mod", style="Go.TButton",
                                      command=self._install_selected)
        self.btn_install.pack(side="left", ipady=4, ipadx=8)

        ttk.Button(act, text="一键安装我的配置", command=self._install_profile).pack(
            side="left", padx=6, ipady=4, ipadx=8)
        ttk.Button(act, text="卸载选中", command=self._uninstall_selected).pack(
            side="left", padx=2, ipady=4)
        ttk.Button(act, text="清理下载缓存", command=self._clear_cache).pack(
            side="left", padx=2, ipady=4)

        self.force_bep = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="强制重装 BepInEx", variable=self.force_bep).pack(
            side="right")

        # ===== 日志 =====
        logf = ttk.LabelFrame(self.root, text=" ③ 运行日志 ", padding=8)
        logf.pack(fill="both", expand=False, padx=12, pady=(6, 12))

        self.log = tk.Text(logf, height=11, wrap="word", font=(MONO, 9),
                           bg="#fbfbfb", relief="flat", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")

    # -------------------------------------------------- 日志
    def _log(self, msg):
        self.log_queue.put(str(msg))

    def _drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_install.configure(state=state)
        self.btn_offline.configure(state=state)
        if busy:
            self.progress.pack(fill="x", side="bottom")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # -------------------------------------------------- 本地离线包
    def _init_local_dir(self):
        """优先用配置里的目录，其次用工具自带的 mods/ 文件夹"""
        cand = (self.cfg.get("local_mods_dir") or "").strip()
        d = None
        if cand and Path(cand).exists():
            d = Path(cand)
        else:
            d = core.default_local_dir()
        self.local_dir = d
        self._update_local_label()

    def _update_local_label(self):
        if not self.local_dir:
            self.local_var.set("（未设置）")
            self.local_hint.configure(
                text="提示：把 mod 压缩包放进本工具目录下的 mods\\ 文件夹，就能一键离线安装")
            return
        pkgs = core.scan_local_packages(self.local_dir)
        n_be = sum(1 for p in pkgs if core.is_bepinex_pack(p["full_name"]))
        self.local_var.set(str(self.local_dir))
        if pkgs:
            self.local_hint.configure(
                text=f"共 {len(pkgs)} 个包（含 BepInEx {n_be} 个）· 离线安装，不联网")
        else:
            self.local_hint.configure(text="这个文件夹里没有找到 作者-Mod名-版本.zip 格式的包")

    def _pick_local_dir(self):
        d = filedialog.askdirectory(title="选择存放 mod 压缩包的文件夹")
        if not d:
            return
        self.local_dir = Path(d)
        self.cfg["local_mods_dir"] = str(self.local_dir)
        core.save_config(self.cfg)
        self._update_local_label()
        self._log(f"本地包目录: {self.local_dir}")

    def _install_offline(self):
        if self.busy:
            messagebox.showinfo("提示", "正在安装中，请稍候")
            return
        if not self.game_dir:
            messagebox.showerror("错误", "没找到游戏目录，请先点「手动选择」")
            return
        if not self.local_dir:
            self._pick_local_dir()
            if not self.local_dir:
                return

        pkgs = core.scan_local_packages(self.local_dir)
        if not pkgs:
            messagebox.showwarning(
                "没有找到 mod 包",
                f"文件夹里没有 作者-Mod名-版本.zip 格式的压缩包：\n{self.local_dir}")
            return

        self._set_busy(True)
        self._log("")
        self._log("=" * 60)
        self._log(f"离线安装 —— 目标: {self.game_dir}")

        def work():
            try:
                ok, failed = core.install_local(
                    self.local_dir, self.game_dir,
                    force_bepinex=self.force_bep.get(),
                    backup=self.cfg.get("backup_before_install", True),
                    log=self._log,
                )
                self._log("=" * 60)
                if failed:
                    self._log(f"完成：成功 {ok} 个，失败 {len(failed)} 个")
                    for n, err in failed:
                        self._log(f"  ✗ {n}: {err}")
                else:
                    self._log(f"全部完成：成功安装 {ok} 个 Mod")
                self.root.after(0, self._after_install)
            except Exception as e:
                self._log(f"[错误] {e}")
                self.root.after(0, lambda: messagebox.showerror("安装失败", str(e)))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------- 游戏目录
    def _detect_game(self):
        game, reason = core.find_game_dir(self.cfg)
        self.game_dir = game
        if game:
            self.path_var.set(str(game))
            self.cfg["game_path"] = str(game)
            core.save_config(self.cfg)
            core.scan_existing_mods(game, log=self._log)
        else:
            self.path_var.set("（未检测到）")
            self._log(f"[!] {reason}")

        self._update_status()
        self._refresh_list()

    def _pick_game(self):
        d = filedialog.askdirectory(title="选择 R.E.P.O. 游戏目录（里面有 REPO.exe）")
        if not d:
            return
        p = Path(d)
        if not (p / "REPO.exe").exists() and not (p / "BepInEx").exists():
            if not messagebox.askyesno("确认", "这个目录里没看到 REPO.exe，确定要用它吗？"):
                return
        self.game_dir = p
        self.cfg["game_path"] = str(p)
        core.save_config(self.cfg)
        self.path_var.set(str(p))
        core.scan_existing_mods(p, log=self._log)
        self._update_status()
        self._refresh_list()
        self._log(f"已指定游戏目录: {p}")

    def _open_game_dir(self):
        if not self.game_dir:
            messagebox.showwarning("提示", "还没定位到游戏目录")
            return
        try:
            os.startfile(str(self.game_dir))
        except Exception:
            subprocess.Popen(["explorer", str(self.game_dir)])

    def _update_status(self):
        if not self.game_dir:
            self.status_var.set("未找到游戏，请点「手动选择」指定目录")
            self.status_lbl.configure(style="Bad.TLabel")
            return

        db = core.load_installed()
        if core.is_bepinex_installed(self.game_dir):
            plugins = self.game_dir / "BepInEx" / "plugins"
            n_dll = len(list(plugins.rglob("*.dll"))) if plugins.exists() else 0
            self.status_var.set(
                f"BepInEx 已就绪 · 已安装 {len(db)} 个 Mod · plugins 内 {n_dll} 个 dll")
            self.status_lbl.configure(style="Ok.TLabel")
        else:
            self.status_var.set("BepInEx 未安装（首次安装会自动装好，无需手动）")
            self.status_lbl.configure(style="Bad.TLabel")

    # -------------------------------------------------- 索引
    def _load_index_from_cache(self):
        if core.INDEX_CACHE.exists():
            try:
                import json
                with open(core.INDEX_CACHE, "r", encoding="utf-8") as f:
                    self.index = core.ModIndex(json.load(f))
                self._log(f"已载入本地索引：{len(self.index.packages)} 个 Mod")
                self._show_popular()
                return
            except Exception:
                pass
        self.count_var.set("点「刷新索引」获取 Mod 列表")

    def _refresh_index(self):
        if self.busy:
            return
        self._set_busy(True)
        self._log("开始刷新索引…")

        def work():
            try:
                pkgs = core.fetch_index(force=True, proxy=self.cfg.get("proxy", ""),
                                        log=self._log)
                self.index = core.ModIndex(pkgs)
                self.root.after(0, lambda: self._show_popular())
            except Exception as e:
                self._log(f"[错误] {e}")
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------- 列表
    def _fmt_dl(self, n):
        n = n or 0
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    def _populate(self, packages):
        self.tree.delete(*self.tree.get_children())
        db = core.load_installed()
        installed_lower = {k.lower() for k in db}

        for p in packages:
            latest = self.index.latest_version(p)
            if not latest:
                continue
            name = p["full_name"]
            is_inst = name.lower() in installed_lower
            mark = "☑" if name in self.selected else "☐"
            state = f"已装 v{db[name]['version']}" if is_inst and name in db else (
                "已安装" if is_inst else "—")
            self.tree.insert("", "end", iid=name, values=(
                mark, name, latest["version_number"],
                self._fmt_dl(latest.get("downloads")), state
            ), tags=("installed" if is_inst else "notinstalled",))

        self.count_var.set(f"共 {len(self.tree.get_children())} 个 Mod · 已勾选 {len(self.selected)} 个")

    def _refresh_list(self):
        if not self.index:
            return
        kw = self.search_var.get().strip()
        if kw:
            self._populate(self.index.search(kw))
        else:
            self._populate(self.index.popular(80))

    def _show_all(self):
        if not self.index:
            return
        self.search_var.set("")
        self._populate(self.index.packages)

    def _show_popular(self):
        if not self.index:
            return
        self.search_var.set("")
        self._populate(self.index.popular(60))

    def _show_installed(self):
        if not self.index:
            return
        db = core.load_installed()
        self.search_var.set("")
        pkgs = []
        for name in db:
            p = self.index.get(name)
            if p:
                pkgs.append(p)
        if not pkgs:
            self._log("还没有安装任何 Mod")
        self._populate(pkgs)

    # -------------------------------------------------- 勾选
    def _on_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return
        if col in ("#1", "#2"):
            self._toggle(item)
            return "break"

    def _toggle_current(self):
        sel = self.tree.selection()
        if sel:
            self._toggle(sel[0])

    def _toggle(self, iid):
        if iid in self.selected:
            self.selected.discard(iid)
            self.tree.set(iid, "sel", "☐")
        else:
            self.selected.add(iid)
            self.tree.set(iid, "sel", "☑")
        self.count_var.set(f"共 {len(self.tree.get_children())} 个 Mod · 已勾选 {len(self.selected)} 个")

    # -------------------------------------------------- 安装
    def _run_install(self, wanted, title):
        if self.busy:
            messagebox.showinfo("提示", "正在安装中，请稍候")
            return
        if not self.game_dir:
            messagebox.showerror("错误", "没找到游戏目录，请先点「手动选择」")
            return
        if not self.index:
            messagebox.showerror("错误", "Mod 列表还没加载，请先点「刷新索引」")
            return
        if not wanted:
            messagebox.showinfo("提示", "请先勾选要安装的 Mod")
            return

        self._set_busy(True)
        self._log("")
        self._log("=" * 60)
        self._log(f"{title} —— 目标: {self.game_dir}")

        def work():
            try:
                core.check_dependencies()
                ok, failed = core.install_mods(
                    self.index, wanted, self.game_dir,
                    proxy=self.cfg.get("proxy", ""),
                    force_bepinex=self.force_bep.get(),
                    backup=self.cfg.get("backup_before_install", True),
                    log=self._log,
                )
                self._log("=" * 60)
                if failed:
                    self._log(f"完成：成功 {ok} 个，失败 {len(failed)} 个")
                    for n, err in failed:
                        self._log(f"  ✗ {n}: {err}")
                else:
                    self._log(f"全部完成：成功安装 {ok} 个 Mod")
                self.root.after(0, self._after_install)
            except Exception as e:
                self._log(f"[错误] {e}")
                self.root.after(0, lambda: messagebox.showerror("安装失败", str(e)))
            finally:
                self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _after_install(self):
        self._update_status()
        self._refresh_list()
        messagebox.showinfo(
            "完成",
            "Mod 安装完成！\n\n"
            "启动游戏时会弹出一个黑色命令行窗口，\n"
            "那是 BepInEx 在加载 Mod，属正常现象。\n\n"
            "如需调整 Mod 参数，进游戏后按 F1 打开配置界面。"
        )

    def _install_selected(self):
        self._run_install(sorted(self.selected), "安装勾选的 Mod")

    def _install_profile(self):
        wanted = self.cfg.get("auto_install_list", [])
        if not wanted:
            messagebox.showinfo(
                "提示",
                "config.json 里的 auto_install_list 是空的。\n\n"
                "可以在里面填上你常用的 Mod 名字（如 Zehs-REPOLib），\n"
                "以后到网吧点这个按钮就能一键装好整套配置。"
            )
            return
        self._log(f"使用配置文件中的 {len(wanted)} 个 Mod")
        self._run_install(wanted, "一键安装我的配置")

    def _uninstall_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中一个 Mod")
            return
        name = sel[0]
        db = core.load_installed()
        if not any(k.lower() == name.lower() for k in db):
            messagebox.showinfo("提示", f"{name} 没有安装记录，无法卸载")
            return
        if not messagebox.askyesno("确认", f"确定卸载 {name}？"):
            return
        ok, msg = core.uninstall_mod(self.index, name, self.game_dir, log=self._log)
        self._update_status()
        self._refresh_list()
        messagebox.showinfo("完成" if ok else "失败", msg)

    def _clear_cache(self):
        core.clear_cache(log=self._log)


def main():
    # 依赖检查
    try:
        core.check_dependencies()
    except Exception as e:
        import tkinter.messagebox as mb
        r = tk.Tk()
        r.withdraw()
        if mb.askyesno("缺少依赖", f"{e}\n\n现在自动安装 requests 吗？"):
            subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
            mb.showinfo("完成", "安装完成，请重新打开本程序")
        r.destroy()
        return

    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
