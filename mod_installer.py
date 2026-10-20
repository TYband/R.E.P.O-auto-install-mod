# -*- coding: utf-8 -*-
"""
R.E.P.O. 一键装 Mod —— 图形界面
==============================
双击 启动.bat 即可运行。
"""

import os
import sys
import json
import queue
import hashlib
import threading
import webbrowser
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    print("=" * 62)
    print(" 无法启动界面：当前 Python 缺少 tkinter 组件")
    print("")
    print(" 解决办法（任选一个）：")
    print("   1. 重新安装 Python，安装时勾选「tcl/tk and IDLE」")
    print("   2. 在自己电脑上运行 build-exe.bat，用生成的 exe 启动")
    print("=" * 62)
    try:
        input("\n按回车键退出…")
    except EOFError:
        pass
    raise SystemExit(1)

import core

# 有 Pillow 就用它做高质量缩放 + 圆角，没有则退回 Tk 原生 PNG
try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ============================================================
# 视觉常量
# ============================================================
F = "Microsoft YaHei UI"
MONO = "Consolas"

BG = "#f4f5f7"
CARD = "#ffffff"
BORDER = "#e4e7ec"
TEXT = "#1f2430"
MUTED = "#6b7280"
ACCENT = "#3b6fe0"
ACCENT_DK = "#2a58c4"
ACCENT_LT = "#eaf0fd"
OK = "#15803d"
OK_BG = "#e7f5ec"
WARN = "#b45309"
WARN_BG = "#fdf3e3"
BAD = "#b91c1c"
BAD_BG = "#fdecec"
ROW_ALT = "#fafbfc"
ROW_HOVER = "#f1f6fe"


# ============================================================
# 图标缓存（异步下载 + 缩放 + 圆角）
# ============================================================
class IconCache:
    def __init__(self, root, log=None):
        self.root = root
        self.log = log or (lambda *a: None)
        self.dir = core.DATA_DIR / "icons"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mem = {}            # (url, size) -> PhotoImage
        self.waiters = {}        # (url, size) -> [callback]
        self.requested = set()   # url 已发起下载
        self.done = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self._poll()

    def _path(self, url):
        return self.dir / (hashlib.md5(url.encode("utf-8")).hexdigest() + ".png")

    def get(self, url, size, callback):
        """异步拿图标；就绪后回调 callback(PhotoImage or None)"""
        if not url:
            callback(None)
            return
        key = (url, size)
        if key in self.mem:
            callback(self.mem[key])
            return

        self.waiters.setdefault(key, []).append(callback)
        path = self._path(url)

        if path.exists():
            self._load(url, path, size)
        elif url not in self.requested:
            self.requested.add(url)
            self.pool.submit(self._download, url, path)

    def _download(self, url, path):
        try:
            import requests
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": core.USER_AGENT})
            if r.status_code == 200 and r.content:
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                self.done.put((url, path))
        except Exception:
            pass

    def _poll(self):
        try:
            while True:
                url, path = self.done.get_nowait()
                for (u, size) in [k for k in self.waiters if k[0] == url]:
                    self._load(u, path, size)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _load(self, url, path, size):
        key = (url, size)
        if key in self.mem:
            return
        photo = None
        try:
            if HAS_PIL:
                im = Image.open(path).convert("RGBA")
                im = ImageOps.fit(im, (size, size), Image.LANCZOS)
                mask = Image.new("L", (size, size), 0)
                radius = max(4, size // 7)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, size - 1, size - 1), radius=radius, fill=255)
                im.putalpha(mask)
                photo = ImageTk.PhotoImage(im, master=self.root)
            else:
                photo = tk.PhotoImage(file=str(path), master=self.root)
                f = max(1, round(max(photo.width(), photo.height()) / size))
                if f > 1:
                    photo = photo.subsample(f, f)
        except Exception:
            photo = None

        self.mem[key] = photo
        for cb in self.waiters.pop(key, []):
            try:
                cb(photo)
            except Exception:
                pass


# ============================================================
# 主界面
# ============================================================
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("R.E.P.O. 一键装 Mod")
        self.root.configure(bg=BG)

        self.cfg = core.load_config()
        self.index = None
        self.game_dir = None
        self.local_dir = None
        self.selected = set()
        self.busy = False
        self.log_queue = queue.Queue()
        self.tr_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self._hover = None
        self._base_tag = {}
        self._log_open = True
        self._tr_on = False
        self._raw_name = ""

        self._setup_style()
        self._build()
        self._fit_window()

        self.icons = IconCache(root, log=self._log)

        # 后台探一下 Google 翻译通不通，通了就优先用它（译文比 MyMemory 顺）
        threading.Thread(
            target=core.probe_translation,
            kwargs={"proxy": (self.cfg.get("proxy") or "").strip()},
            daemon=True).start()
        self._init_local_dir()
        self._detect_game()
        self._load_index_from_cache()
        self.root.after(120, self._drain_log)

    # -------------------------------------------------- 样式
    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass

        st.configure(".", background=BG, foreground=TEXT, font=(F, 9))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=(F, 9))
        st.configure("CardSub.TLabel", background=CARD, foreground=MUTED, font=(F, 9))
        st.configure("Title.TLabel", background=BG, foreground=TEXT, font=(F, 15, "bold"))
        st.configure("H2.TLabel", background=CARD, foreground=TEXT, font=(F, 11, "bold"))

        # 输入框
        st.configure("TEntry", fieldbackground="#ffffff", background="#ffffff",
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     insertcolor=TEXT, padding=(8, 6))
        st.map("TEntry", bordercolor=[("focus", ACCENT)])

        # 主按钮
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     borderwidth=0, focusthickness=0, padding=(16, 9),
                     font=(F, 9, "bold"), relief="flat")
        st.map("Accent.TButton",
               background=[("active", ACCENT_DK), ("pressed", ACCENT_DK),
                           ("disabled", "#b8c7ea")],
               foreground=[("disabled", "#f2f5fb")])

        # 次级按钮
        st.configure("Ghost.TButton", background=CARD, foreground=TEXT,
                     bordercolor=BORDER, borderwidth=1, focusthickness=0,
                     padding=(11, 7), relief="flat")
        st.map("Ghost.TButton",
               background=[("active", "#f6f8fb"), ("pressed", "#eef2f8")],
               bordercolor=[("active", "#cfd6e0")])

        # 小号扁平按钮（标签页）
        st.configure("TabOn.TButton", background=ACCENT_LT, foreground=ACCENT,
                     borderwidth=0, focusthickness=0, padding=(12, 5),
                     font=(F, 9, "bold"), relief="flat")
        st.configure("TabOff.TButton", background=BG, foreground=MUTED,
                     borderwidth=0, focusthickness=0, padding=(12, 5),
                     font=(F, 9), relief="flat")
        st.map("TabOff.TButton", background=[("active", "#e9edf3")],
               foreground=[("active", TEXT)])

        st.configure("Mini.TButton", background=CARD, foreground=MUTED,
                     borderwidth=0, focusthickness=0, padding=(8, 3),
                     font=(F, 8), relief="flat")
        st.map("Mini.TButton", background=[("active", "#f0f3f7")],
               foreground=[("active", TEXT)])

        # 列表
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, borderwidth=0, relief="flat",
                     rowheight=54, font=(F, 9))
        st.configure("Treeview.Heading", background=CARD, foreground=MUTED,
                     borderwidth=0, relief="flat", padding=(6, 9),
                     font=(F, 9, "bold"))
        st.map("Treeview.Heading", background=[("active", CARD)])
        st.map("Treeview", background=[("selected", ACCENT_LT)],
               foreground=[("selected", TEXT)])

        st.configure("Accent.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor="#e9edf3", borderwidth=0, thickness=4)

        st.configure("TCheckbutton", background=BG, foreground=TEXT,
                     focusthickness=0)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("Card.TCheckbutton", background=CARD, foreground=TEXT,
                     focusthickness=0)
        st.map("Card.TCheckbutton", background=[("active", CARD)])

    # -------------------------------------------------- 小工具
    def _card(self, parent):
        """1px 边框的白色卡片：外层当边框，内层装内容"""
        outer = tk.Frame(parent, bg=BORDER)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _hline(self, parent, pady=(0, 0)):
        ln = tk.Frame(parent, bg=BORDER, height=1)
        ln.pack(fill="x", pady=pady)
        return ln

    # -------------------------------------------------- 界面
    def _build(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=(14, 12))

        # ===== 标题栏 =====
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="R.E.P.O. 一键装 Mod", bg=BG, fg=TEXT,
                 font=(F, 16, "bold")).pack(side="left")
        tk.Label(head, text="自动装 BepInEx · 自动补依赖 · 支持离线",
                 bg=BG, fg=MUTED, font=(F, 9)).pack(side="left", padx=(12, 0), pady=(5, 0))

        self.chip = tk.Label(head, text="检测中…", bg="#eef1f5", fg=MUTED,
                             font=(F, 9), padx=12, pady=5)
        self.chip.pack(side="right")

        self._hline(outer, pady=(12, 12))

        # ===== 路径区 =====
        rows = tk.Frame(outer, bg=BG)
        rows.pack(fill="x")

        r1 = tk.Frame(rows, bg=BG)
        r1.pack(fill="x")
        tk.Label(r1, text="游戏目录", bg=BG, fg=MUTED, font=(F, 9),
                 width=9, anchor="w").pack(side="left")
        self.path_var = tk.StringVar(value="检测中…")
        ttk.Entry(r1, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(r1, text="检测", style="Ghost.TButton",
                   command=self._detect_game).pack(side="left", padx=(0, 6))
        ttk.Button(r1, text="选择", style="Ghost.TButton",
                   command=self._pick_game).pack(side="left", padx=(0, 6))
        ttk.Button(r1, text="打开", style="Ghost.TButton",
                   command=self._open_game_dir).pack(side="left")

        r2 = tk.Frame(rows, bg=BG)
        r2.pack(fill="x", pady=(8, 0))
        tk.Label(r2, text="本地包", bg=BG, fg=MUTED, font=(F, 9),
                 width=9, anchor="w").pack(side="left")
        self.local_var = tk.StringVar(value="")
        ttk.Entry(r2, textvariable=self.local_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(r2, text="选择", style="Ghost.TButton",
                   command=self._pick_local_dir).pack(side="left", padx=(0, 6))
        self.btn_offline = ttk.Button(r2, text="⚡ 一键离线安装", style="Accent.TButton",
                                      command=self._install_offline)
        self.btn_offline.pack(side="left")

        self.local_hint = tk.Label(rows, text="", bg=BG, fg=MUTED, font=(F, 8),
                                   anchor="w")
        self.local_hint.pack(fill="x", pady=(6, 0))

        # ===== 主体：左列表 + 右详情 =====
        main = tk.Frame(outer, bg=BG)
        main.pack(fill="both", expand=True, pady=(12, 0))
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, minsize=352, weight=0)
        main.rowconfigure(0, weight=1)

        # ---- 左：列表 ----
        l_outer, l = self._card(main)
        l_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        bar = tk.Frame(l, bg=CARD)
        bar.pack(fill="x", padx=14, pady=(12, 8))

        tk.Label(bar, text="搜索", bg=CARD, fg=MUTED, font=(F, 9)).pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var, width=22)
        ent.pack(side="left", padx=(6, 12))
        ent.bind("<KeyRelease>", lambda e: self._refresh_list())

        self.tab_hot = ttk.Button(bar, text="热门", style="TabOn.TButton",
                                  command=self._show_popular)
        self.tab_hot.pack(side="left", padx=(0, 4))
        self.tab_all = ttk.Button(bar, text="全部", style="TabOff.TButton",
                                  command=self._show_all)
        self.tab_all.pack(side="left", padx=(0, 4))
        self.tab_inst = ttk.Button(bar, text="已安装", style="TabOff.TButton",
                                   command=self._show_installed)
        self.tab_inst.pack(side="left")

        self.count_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.count_var, bg=CARD, fg=MUTED,
                 font=(F, 8)).pack(side="right")
        ttk.Button(bar, text="刷新", style="Mini.TButton",
                   command=self._refresh_index).pack(side="right", padx=(0, 8))

        wrap = tk.Frame(l, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("sel", "name", "ver", "dl", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="tree headings",
                                 selectmode="browse", height=7)
        self.tree.heading("#0", text="")
        self.tree.heading("sel", text="")
        self.tree.heading("name", text="Mod 名称")
        self.tree.heading("ver", text="版本")
        self.tree.heading("dl", text="下载量")
        self.tree.heading("state", text="状态")

        self.tree.column("#0", width=52, stretch=False, anchor="center")
        self.tree.column("sel", width=32, stretch=False, anchor="center")
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("ver", width=88, stretch=False, anchor="center")
        self.tree.column("dl", width=80, stretch=False, anchor="e")
        self.tree.column("state", width=92, stretch=False, anchor="center")

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("even", background=CARD)
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("hover", background=ROW_HOVER)

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<space>", lambda e: self._toggle_current())

        # ---- 右：详情 ----
        r_outer, rp = self._card(main)
        r_outer.grid(row=0, column=1, sticky="nsew")

        pad = tk.Frame(rp, bg=CARD)
        pad.pack(fill="both", expand=True, padx=18, pady=16)

        self.d_icon = tk.Label(pad, bg=CARD)
        self.d_icon.pack(anchor="w")

        self.name_row = tk.Frame(pad, bg=CARD)
        self.name_row.pack(fill="x", pady=(10, 2))
        self.d_name = tk.Label(self.name_row, text="", bg=CARD, fg=TEXT,
                               font=(F, 12, "bold"), anchor="w", justify="left",
                               wraplength=250)
        self.d_name.pack(side="left", fill="x", expand=True)
        self.btn_trans = ttk.Button(self.name_row, text="翻译", style="Mini.TButton",
                                    command=self._translate_detail)
        self.btn_trans.pack(side="right", anchor="n", padx=(6, 0))

        self.d_meta = tk.Label(pad, text="", bg=CARD, fg=MUTED, font=(F, 9),
                               anchor="w", justify="left", wraplength=300)
        self.d_meta.pack(fill="x")

        self.d_desc = tk.Label(pad, text="", bg=CARD, fg="#4b5563", font=(F, 9),
                               anchor="nw", justify="left", wraplength=300)
        self.d_desc.pack(fill="x", pady=(10, 0))

        self.d_deps = tk.Label(pad, text="", bg=CARD, fg=MUTED, font=(F, 8),
                               anchor="nw", justify="left", wraplength=300)
        self.d_deps.pack(fill="x", pady=(10, 0))

        self.d_btns = tk.Frame(pad, bg=CARD)
        self.d_btns.pack(side="bottom", fill="x", pady=(12, 0))

        self.btn_detail_install = ttk.Button(self.d_btns, text="安装这个",
                                             style="Accent.TButton",
                                             command=self._install_detail)
        self.btn_detail_install.pack(side="left")
        ttk.Button(self.d_btns, text="打开网页", style="Ghost.TButton",
                   command=self._open_web).pack(side="left", padx=(8, 0))
        ttk.Button(self.d_btns, text="卸载", style="Ghost.TButton",
                   command=self._uninstall_detail).pack(side="left", padx=(8, 0))

        self._show_detail_placeholder()

        # ===== 操作栏 =====
        act = tk.Frame(outer, bg=BG)
        act.pack(fill="x", pady=(12, 0))

        self.btn_install = ttk.Button(act, text="安装勾选的 Mod", style="Accent.TButton",
                                      command=self._install_selected)
        self.btn_install.pack(side="left")
        ttk.Button(act, text="一键安装我的配置", style="Ghost.TButton",
                   command=self._install_profile).pack(side="left", padx=(8, 0))
        ttk.Button(act, text="清理缓存", style="Ghost.TButton",
                   command=self._clear_cache).pack(side="left", padx=(8, 0))

        self.auto_dep = tk.BooleanVar(value=True)
        ttk.Checkbutton(act, text="自动补依赖", variable=self.auto_dep).pack(
            side="right")
        self.force_bep = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="重装 BepInEx", variable=self.force_bep).pack(
            side="right", padx=(0, 14))

        # ===== 日志 =====
        self.log_head = tk.Frame(outer, bg=BG)
        self.log_head.pack(fill="x", pady=(12, 6))
        tk.Label(self.log_head, text="运行日志", bg=BG, fg=MUTED,
                 font=(F, 9, "bold")).pack(side="left")
        self.log_toggle = ttk.Button(self.log_head, text="收起", style="Mini.TButton",
                                     command=self._toggle_log)
        self.log_toggle.pack(side="right")

        self.log_outer, self.log_inner = self._card(outer)
        self.log_outer.pack(fill="x")

        self.log = tk.Text(self.log_inner, height=6, wrap="word", font=(MONO, 9),
                           bg="#fbfcfd", fg="#374151", relief="flat",
                           padx=12, pady=8, state="disabled",
                           insertbackground=TEXT, selectbackground=ACCENT_LT)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(self.log_inner, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")

        self.progress = ttk.Progressbar(self.root, mode="indeterminate",
                                        style="Accent.Horizontal.TProgressbar")

    def _fit_window(self):
        """按屏幕可用空间和内容实际需要定尺寸，避免高 DPI 下窗口超出屏幕被裁掉"""
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(1080, min(self.root.winfo_reqwidth() + 12, sw - 80))
        h = max(680, min(self.root.winfo_reqheight() + 12, sh - 110))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 26)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        # 标题栏/边框会吃掉一截高度，实测后再补一次，保证底部日志不被裁
        self.root.update_idletasks()
        need, have = self.root.winfo_reqheight(), self.root.winfo_height()
        if have < need:
            h = min(h + (need - have) + 8, sh - 110)
            y = max(0, (sh - h) // 2 - 26)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(1000, w), min(660, h))

    # -------------------------------------------------- 日志
    def _log(self, msg):
        self.log_queue.put(str(msg))

    def _ui(self, fn):
        """从后台线程投递任务到主线程执行。
        启动阶段 mainloop 还没跑起来，这时直接 root.after 会抛
        'main thread is not in main loop'，任务就丢了。"""
        self.ui_queue.put(fn)

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
        # 后台线程的翻译结果也在这里落地，避免跨线程操作 Tk
        try:
            while True:
                tn, td, iid = self.tr_queue.get_nowait()
                self._apply_translation(tn, td, iid)
        except queue.Empty:
            pass
        # 后台线程投递的界面任务
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    self._log(f"[错误] {e}")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def _toggle_log(self):
        if self._log_open:
            self.log_outer.pack_forget()
            self.log_toggle.configure(text="展开")
            self._log_open = False
        else:
            self.log_outer.pack(fill="x")
            self.log_toggle.configure(text="收起")
            self._log_open = True

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_install, self.btn_offline, self.btn_detail_install):
            b.configure(state=state)
        if busy:
            self.progress.pack(fill="x", side="bottom")
            self.progress.start(14)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _chip(self, text, fg, bg):
        self.chip.configure(text=text, fg=fg, bg=bg)

    # -------------------------------------------------- 本地离线包
    def _init_local_dir(self):
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
                text=f"共 {len(pkgs)} 个包（含 BepInEx {n_be} 个）· 依赖可自动补齐")
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
            messagebox.showerror("错误", "没找到游戏目录，请先点「选择」")
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
        self._log("=" * 58)
        self._log(f"离线安装 —— 目标: {self.game_dir}")

        def work():
            try:
                ok, failed = core.install_local(
                    self.local_dir, self.game_dir,
                    force_bepinex=self.force_bep.get(),
                    backup=self.cfg.get("backup_before_install", True),
                    auto_fetch_deps=self.auto_dep.get(),
                    proxy=self.cfg.get("proxy", ""),
                    log=self._log,
                )
                self._log("=" * 58)
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
            self._chip("未找到游戏", BAD, BAD_BG)
            return

        db = core.load_installed()
        if core.is_bepinex_installed(self.game_dir):
            plugins = self.game_dir / "BepInEx" / "plugins"
            n_dll = len(list(plugins.rglob("*.dll"))) if plugins.exists() else 0
            self._chip(f"BepInEx 就绪 · {len(db)} 个 Mod · {n_dll} 个 dll", OK, OK_BG)
        else:
            self._chip("BepInEx 未安装（首次安装会自动装好）", WARN, WARN_BG)

    # -------------------------------------------------- 索引
    def _load_index_from_cache(self):
        data = None
        if core.INDEX_CACHE.exists():
            try:
                with open(core.INDEX_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None

        if data and core._index_is_current(data):
            self.index = core.ModIndex(data)
            self._log(f"已载入本地索引：{len(self.index.packages)} 个 Mod")
            self._show_popular()
            # 列表先用缓存秒开，再后台去 API 换成线上最新的
            if core.needs_refresh():
                self._refresh_index(silent=True)
            return

        if data:
            self._log("本地索引是旧格式，重新拉一份…")
        else:
            self._log("本地没有 Mod 索引，自动拉取…")
        # 不再让用户自己找按钮：没有索引就直接后台拉
        self.count_var.set("正在加载 Mod 列表…")
        self._refresh_index()

    def _refresh_index(self, silent=False):
        """
        从 Thunderstore API 拉最新 Mod 列表。
        silent=True 时后台静默同步：不锁按钮、不清空当前列表。
        """
        if self.busy:
            return
        if not silent:
            self._set_busy(True)
            self._log("开始刷新索引…")
        else:
            self._log("后台同步 Thunderstore 最新列表…")

        def on_progress(done, total):
            # 服务器是 gzip 传输，Content-Length 是压缩后的大小，
            # 解压后的字节数会超过它，这时只报已下载量
            if total and total >= done:
                self._log(f"  已下载 {done/1048576:.1f} / {total/1048576:.1f} MB")
            else:
                self._log(f"  已下载 {done/1048576:.1f} MB")

        def work():
            try:
                pkgs = core.fetch_index(force=True, proxy=self.cfg.get("proxy", ""),
                                        log=self._log, progress=on_progress)
                self.index = core.ModIndex(pkgs)
                self._ui(self._show_popular)
            except Exception as e:
                self._log(f"[错误] 索引加载失败：{e}")
                if not silent:
                    self._ui(lambda: self.count_var.set("索引加载失败，点「刷新」重试"))
                else:
                    self._ui(lambda: self.count_var.set("线上同步失败，当前是本地缓存"))
            finally:
                if not silent:
                    self._ui(lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------- 列表
    def _fmt_dl(self, n):
        n = n or 0
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    def _set_tab(self, which):
        for name, btn in (("hot", self.tab_hot), ("all", self.tab_all),
                          ("inst", self.tab_inst)):
            btn.configure(style="TabOn.TButton" if name == which else "TabOff.TButton")

    def _populate(self, packages):
        prev = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._base_tag.clear()
        self._hover = None

        db = core.load_installed()
        installed_lower = {k.lower() for k in db}

        for i, p in enumerate(packages):
            latest = self.index.latest_version(p)
            if not latest:
                continue
            name = p["full_name"]
            is_inst = name.lower() in installed_lower
            mark = "☑" if name in self.selected else "☐"
            if is_inst:
                ver = db.get(name, {}).get("version", latest["version_number"])
                state = f"已装 v{ver}"
            else:
                state = "—"

            base = "even" if i % 2 == 0 else "odd"
            self._base_tag[name] = base
            self.tree.insert(
                "", "end", iid=name, text="",
                values=(mark, name, latest["version_number"],
                        self._fmt_dl(latest.get("downloads")), state),
                tags=(base,))

            icon_url = latest.get("icon", "")
            if icon_url:
                self.icons.get(icon_url, 40,
                               lambda photo, n=name: self._set_row_icon(n, photo))

        self.count_var.set(f"{len(self.tree.get_children())} 个 · 已勾选 {len(self.selected)}")

        # 列表刷新后保留原来的选中项（后台同步不会打断浏览）
        keep = prev[0] if prev and self.tree.exists(prev[0]) else ""
        kids = self.tree.get_children()
        if keep:
            self.tree.selection_set(keep)
            self.tree.focus(keep)
        elif kids and not self.tree.selection():
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def _set_row_icon(self, iid, photo):
        if not photo:
            return
        try:
            if self.tree.exists(iid):
                self.tree.item(iid, image=photo)
        except Exception:
            pass

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
        self._set_tab("all")
        self._populate(self.index.packages)

    def _show_popular(self):
        if not self.index:
            return
        self.search_var.set("")
        self._set_tab("hot")
        self._populate(self.index.popular(60))

    def _show_installed(self):
        if not self.index:
            return
        db = core.load_installed()
        self.search_var.set("")
        self._set_tab("inst")
        pkgs = []
        for name in db:
            p = self.index.get(name)
            if p:
                pkgs.append(p)
        if not pkgs:
            self._log("还没有安装任何 Mod")
        self._populate(pkgs)

    # -------------------------------------------------- 详情面板
    def _show_detail_placeholder(self):
        self.d_icon.configure(image="")
        self.d_name.configure(text="选一个 Mod 看详情", fg=MUTED)
        self.d_meta.configure(text="左边点一行，这里会显示图标、说明和依赖")
        self.d_desc.configure(text="")
        self.d_deps.configure(text="")
        self.btn_detail_install.configure(state="disabled")
        self.btn_trans.configure(text="翻译", state="disabled")
        self._tr_on = False

    def _current_pkg(self):
        sel = self.tree.selection()
        if not sel or not self.index:
            return None, None
        pkg = self.index.get(sel[0])
        if not pkg:
            return None, None
        return pkg, self.index.latest_version(pkg)

    def _show_detail(self):
        pkg, latest = self._current_pkg()
        if not pkg:
            self._show_detail_placeholder()
            return

        name = pkg["full_name"]
        db = core.load_installed()
        inst = next((v for k, v in db.items() if k.lower() == name.lower()), None)

        self._tr_on = False
        self._raw_name = pkg.get("name") or name
        self.d_name.configure(text=self._raw_name, fg=TEXT)
        self.btn_trans.configure(text="翻译", state="normal")

        bits = [pkg.get("owner") or "", f"v{latest['version_number']}",
                f"↓ {self._fmt_dl(latest.get('downloads'))}"]
        size = latest.get("file_size") or 0
        if size:
            bits.append(f"{size/1048576:.1f} MB" if size > 1048576 else f"{size/1024:.0f} KB")
        if inst:
            bits.append(f"已安装 v{inst['version']}")
        self.d_meta.configure(text="  ·  ".join([b for b in bits if b]))

        desc = (latest.get("description") or "").strip()
        self.d_desc.configure(text=desc[:300] + ("…" if len(desc) > 300 else ""))

        deps = latest.get("dependencies") or []
        if deps:
            names = []
            for d in deps[:6]:
                dn, dv = core._parse_dep(d)
                names.append(dn.split("-", 1)[-1] if "-" in dn else dn)
            more = f" 等 {len(deps)} 个" if len(deps) > 6 else ""
            self.d_deps.configure(text="依赖：" + "、".join(names) + more)
        else:
            self.d_deps.configure(text="无额外依赖")

        self.btn_detail_install.configure(
            state="disabled" if self.busy else "normal",
            text="重新安装" if inst else "安装这个")

        icon_url = latest.get("icon", "")
        self.d_icon.configure(image="")
        if icon_url:
            self.icons.get(icon_url, 112,
                           lambda photo: self._set_detail_icon(photo))

    # -------------------------------------------------- 翻译
    def _translate_detail(self):
        pkg, latest = self._current_pkg()
        if not pkg:
            return
        if self._tr_on:                      # 已经翻过 → 还原原文
            self._show_detail()
            return

        name = pkg.get("name") or pkg["full_name"]
        desc = (latest.get("description") or "").strip()[:480]
        proxy = (self.cfg.get("proxy") or "").strip()

        self.btn_trans.configure(text="翻译中…", state="disabled")
        self._log(f"翻译 {name} …")

        def work():
            try:
                tn, td = core.translate_package(name, desc, proxy=proxy, log=self._log)
            except Exception as e:
                self._log(f"[错误] 翻译失败：{e}")
                tn, td = "", ""
            # 不直接碰 Tk，交给主线程的队列处理（跨线程调 Tk 会偶发崩）
            self.tr_queue.put((tn, td, pkg["full_name"]))

        threading.Thread(target=work, daemon=True).start()

    def _apply_translation(self, tn, td, iid):
        # 翻译期间用户可能已经点了别的 Mod，别把结果写到错误的详情里
        sel = self.tree.selection()
        if not sel or sel[0] != iid:
            return
        if not tn and not td:
            self.btn_trans.configure(text="翻译", state="normal")
            self._log("翻译失败：免费翻译源都连不上。可在 config.json 填 proxy"
                      "（如 http://127.0.0.1:7897）后重试")
            return
        if tn:
            self.d_name.configure(text=f"{tn}（{self._raw_name}）")
        if td:
            self.d_desc.configure(text=td[:400])
        self.btn_trans.configure(text="还原", state="normal")
        self._tr_on = True

    def _set_detail_icon(self, photo):
        if photo:
            try:
                self.d_icon.configure(image=photo)
            except Exception:
                pass

    def _open_web(self):
        pkg, latest = self._current_pkg()
        if not pkg:
            return
        url = (latest.get("website_url") or pkg.get("package_url") or "")
        if url:
            webbrowser.open(url)

    def _install_detail(self):
        pkg, _ = self._current_pkg()
        if pkg:
            self._run_install([pkg["full_name"]], "安装 Mod")

    def _uninstall_detail(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._do_uninstall(sel[0])

    # -------------------------------------------------- 勾选
    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        col = self.tree.identify_column(event.x)
        # #0=图标  #1=勾选  #2=名称 … 只有勾选列切换勾选，点名称列走默认选中（看详情）
        if col == "#1":
            self._toggle(iid)
            return "break"

    def _on_double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _on_motion(self, event):
        iid = self.tree.identify_row(event.y)
        if iid == self._hover:
            return
        if self._hover and self.tree.exists(self._hover):
            self.tree.item(self._hover, tags=(self._base_tag.get(self._hover, "even"),))
        self._hover = iid
        if iid:
            base = self._base_tag.get(iid, "even")
            self.tree.item(iid, tags=(base, "hover"))
            self.tree.configure(cursor="hand2")
        else:
            self.tree.configure(cursor="")

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
        self.count_var.set(
            f"{len(self.tree.get_children())} 个 · 已勾选 {len(self.selected)}")

    # -------------------------------------------------- 安装
    def _run_install(self, wanted, title):
        if self.busy:
            messagebox.showinfo("提示", "正在安装中，请稍候")
            return
        if not self.game_dir:
            messagebox.showerror("错误", "没找到游戏目录，请先点「选择」")
            return
        if not self.index:
            messagebox.showerror("错误", "Mod 列表还没加载，请先点「刷新索引」")
            return
        if not wanted:
            messagebox.showinfo("提示", "请先勾选要安装的 Mod")
            return

        self._set_busy(True)
        self._log("")
        self._log("=" * 58)
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
                self._log("=" * 58)
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
        self._show_detail()
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

    def _do_uninstall(self, name):
        db = core.load_installed()
        if not any(k.lower() == name.lower() for k in db):
            messagebox.showinfo("提示", f"{name} 没有安装记录，无法卸载")
            return
        if not messagebox.askyesno("确认", f"确定卸载 {name}？"):
            return
        ok, msg = core.uninstall_mod(self.index, name, self.game_dir, log=self._log)
        self._update_status()
        self._refresh_list()
        self._show_detail_placeholder()
        messagebox.showinfo("完成" if ok else "失败", msg)

    def _uninstall_selected(self):
        sel = self.tree.selection()
        if sel:
            self._do_uninstall(sel[0])

    def _clear_cache(self):
        core.clear_cache(log=self._log)


def main():
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
