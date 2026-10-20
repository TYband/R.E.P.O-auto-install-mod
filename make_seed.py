# -*- coding: utf-8 -*-
"""
打包前生成索引种子
==================
把本地 data/index.json 压成 gzip 塞进 exe（build/seed/index.json.gz）。

为什么要这一步：exe 在别人机器上跑起来时 data/ 是空的，界面就没有 Mod 列表。
Thunderstore 在国内经常连不上，用户会以为工具坏了。带上种子后，
第一次打开就有完整列表，联网时再自动更新。

本地也没有索引时会尝试下载一次；下载失败就写一个空种子，
运行时界面会自动再去拉。
"""

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "index.json"
OUT = ROOT / "build" / "seed" / "index.json.gz"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if not SRC.exists():
        print("[seed] 本地没有索引缓存，尝试下载一次…")
        try:
            import core
            core.fetch_index(force=True, log=print)
        except Exception as e:
            print(f"[seed] 下载失败（{type(e).__name__}: {e}）")

    data = []
    if SRC.exists():
        try:
            data = json.loads(SRC.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[seed] 读取本地索引失败：{e}")

    if not isinstance(data, list):
        data = []

    with gzip.open(OUT, "wb", 9) as f:
        f.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    size = OUT.stat().st_size / 1048576
    if data:
        print(f"[seed] 已打包 {len(data)} 个 Mod 的索引 → {OUT.name} ({size:.1f} MB)")
    else:
        print(f"[seed] 空种子（运行时界面会自动联网拉取）→ {OUT.name} ({size:.2f} MB)")


if __name__ == "__main__":
    main()
