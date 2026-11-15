#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查一个「公众人物解构」项目的完成度。

用法:
  python3 check.py --project "<项目目录>"
  python3 check.py --project "..." --strict   # 有未完成项时退出码 1
"""
import argparse
import glob
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def md_files(d):
    return [f for f in glob.glob(os.path.join(d, "*.md"))
            if not os.path.basename(f).startswith("_")]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    root = os.path.expanduser(args.project)
    ok = True

    print(f"项目：{root}\n")

    # 一手
    col = os.path.join(root, "00-收集")
    eps = md_files(col) if os.path.isdir(col) else []
    raws = glob.glob(os.path.join(col, "*_raw.txt")) if os.path.isdir(col) else []
    metas = glob.glob(os.path.join(col, "*_meta.json")) if os.path.isdir(col) else []
    nums = []
    for f in eps:
        m = re.search(r"(\d{3})：", os.path.basename(f))
        if m:
            nums.append(int(m.group(1)))
    gaps = []
    if nums:
        gaps = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    print(f"一手：蒸馏稿 {len(eps)}｜原文 {len(raws)}｜元信息 {len(metas)}")
    if nums:
        print(f"      集号 {min(nums)}–{max(nums)}；缺号 {gaps or '无'}")
    else:
        print("      ⚠️ 没有解析到集号（文件名应为 <前缀>001：<主题>.md）")
    if len(raws) and len(eps) < len(raws):
        print(f"      ⚠️ 还有 {len(raws) - len(eps)} 集未蒸馏")
        ok = False

    # 外部
    ext_dir = os.path.join(root, "00-收集-外部")
    exts = md_files(ext_dir) if os.path.isdir(ext_dir) else []
    print(f"外部：{len(exts)} 篇")

    # 调研
    res_dir = os.path.join(root, "10-画像/_调研")
    res = md_files(res_dir) if os.path.isdir(res_dir) else []
    incomplete = []
    for f in res:
        s = read(f)
        if not all(re.search(rf"^##\s*{i}[\.\s]", s, re.M) for i in range(1, 9)):
            incomplete.append(os.path.basename(f))
    print(f"分片调研：{len(res)} 片；8 节不全：{incomplete or '无'}")
    if incomplete:
        ok = False

    # 外部调研
    ext_res_dir = os.path.join(root, "10-画像/_调研-外部")
    ext_res = sorted(glob.glob(os.path.join(ext_res_dir, "[A-I]-*.md"))) if os.path.isdir(ext_res_dir) else []
    print(f"外部调研：{len(ext_res)} 个维度  {[os.path.basename(x)[0] for x in ext_res]}")

    # 画像
    portrait = None
    for f in glob.glob(os.path.join(root, "10-画像", "*思维操作系统*.md")):
        if "核验" not in os.path.basename(f):
            portrait = f
            break
    if portrait:
        s = read(portrait)
        items = re.findall(r"^###\s+(\d{2})\s*·", s, re.M)
        cats = re.findall(r"^##\s+[一二三四五六]\s*·", s, re.M)
        print(f"画像：{os.path.basename(portrait)}｜{len(items)} 条｜{len(cats)} 类")
        for key in ["person:", "version:", "movable-skills:"]:
            if key not in s[:2000]:
                print(f"      ⚠️ frontmatter 缺 {key}")
                ok = False
        for need in ["诚实边界", "附录 A", "附录 B"]:
            if need not in s:
                print(f"      ⚠️ 缺章节：{need}")
                ok = False
    else:
        print("画像：未找到")
        ok = False

    # 核验表
    ver = glob.glob(os.path.join(root, "10-画像", "*核验表*.md"))
    if ver:
        s = read(ver[0])
        rows = len(re.findall(r"^\|\s*T\d+", s, re.M))
        print(f"核验表：{os.path.basename(ver[0])}｜{rows} 条")
        if rows == 0:
            print("      ⚠️ 核验表没有 T 条目")
            ok = False
    else:
        print("核验表：未找到")
        ok = False

    # 书
    md_book = glob.glob(os.path.join(root, "20-输出", "*全书*.md"))
    epubs = glob.glob(os.path.join(tc.output_dir(), "*.epub"))
    print(f"合并 md：{os.path.basename(md_book[0]) if md_book else '未找到'}")
    print(f"输出目录 epub：{len(epubs)} 个（同名请人工确认）")
    if not md_book:
        ok = False

    # 索引
    for name in ["案例索引.md", "金句索引.md"]:
        fp = os.path.join(root, "20-输出", name)
        print(f"{name}：{'有' if os.path.exists(fp) else '未找到'}")

    print("\n结论：" + ("可以进入 G3 验收" if ok else "还有未完成项，见上方 ⚠️"))
    if args.strict and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
