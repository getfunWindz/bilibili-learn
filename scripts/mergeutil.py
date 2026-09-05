# -*- coding: utf-8 -*-
"""D2: 合集目录合并——把 src 目录下的 P 子目录复制到 dst，跳过已存在 P（不覆盖）"""
import os
import shutil


def merge_dirs(src: str, dst: str) -> tuple:
    """合并两个合集目录：返回 (copied_pages, skipped_pages)。
    规则：src 中每个 Pxx 子目录，dst 不存在同名目录则整体复制；存在则跳过（保留 dst 内容）。"""
    copied, skipped = [], []
    if not os.path.isdir(src):
        return copied, skipped
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if not os.path.isdir(s):
            continue
        if name.startswith("P") and name[1:].isdigit():
            d = os.path.join(dst, name)
            if os.path.exists(d):
                skipped.append(name)
            else:
                shutil.copytree(s, d)
                copied.append(name)
    return copied, skipped


def cmd_merge(args) -> None:
    """bili merge <src合集目录> <dst合集目录>"""
    copied, skipped = merge_dirs(args.src, args.dst)
    print(f"已复制：{', '.join(copied) if copied else '（无）'}")
    print(f"已跳过（目标已存在）：{', '.join(skipped) if skipped else '（无）'}")
    if copied:
        print(f"合并完成 → {args.dst}")
