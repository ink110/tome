#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从一个「公众人物解构」项目直接成书（合并 md + epub）。

这是 tome-book skill 的薄包装：自动推断人物、书名、输出路径，然后调用
tome-book 的 build_book.py。

用法:
  python3 make_book.py --project "<项目目录>"
  python3 make_book.py --project "..." --mode archive
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402


def find_skill_script(rel):
    """先在同一仓库里找，再退回技能安装目录。"""
    for parent in _HERE.parents:
        candidate = parent / rel
        if candidate.is_file():
            return candidate
    return Path.home() / ".claude" / "skills" / rel


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "", s).strip()


def load_project(root):
    p = os.path.join(root, "_meta", "project.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    person = os.path.basename(os.path.normpath(root))
    mains = [f for f in glob.glob(os.path.join(root, "10-画像", "*思维操作系统*.md"))
             if "核验" not in os.path.basename(f)]
    if mains:
        m = re.match(r"(.+?)·思维操作系统", os.path.basename(max(mains, key=os.path.getmtime)))
        if m:
            person = m.group(1)
    return {"person": person, "prefix": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--author", default=None)
    ap.add_argument("--editor", default="")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--cover", default=None, help="1200x1800 PNG；不给则自动找 assets/cover.png 或跳过")
    ap.add_argument("--out", default=None)
    ap.add_argument("--md-out", default=None)
    ap.add_argument("--mode", choices=["reading", "archive"], default="reading")
    ap.add_argument("--pandoc", default=None,
                    help="pandoc 路径；默认自动查找（见 tome_common.pandoc_bin）")
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.project))
    meta = load_project(root)
    person = meta.get("person") or os.path.basename(root)
    title = args.title or f"{person}·思维操作系统 2.0"
    author = args.author or person
    out = args.out or str(tc.output_dir() / f"{safe_name(title)}（阅读版）.epub")
    pandoc = args.pandoc or tc.pandoc_bin()
    if not pandoc:
        sys.exit("找不到 pandoc。请安装 pandoc，或在 ~/.config/tome/config.json 里设置 tools.pandoc。")
    md_out = args.md_out or os.path.join(
        root, "20-输出", f"{safe_name(title)}（全书）.md")
    cover = args.cover
    if not cover:
        default_cover = os.path.join(root, "assets", "cover.png")
        if os.path.exists(default_cover):
            cover = default_cover
        else:
            mk = find_skill_script("tome-book/scripts/make_cover.py")
            try:
                os.makedirs(os.path.dirname(default_cover), exist_ok=True)
                byline = " · ".join(x for x in (
                    f"{author} 口述" if author else "",
                    f"{args.editor} 整理" if args.editor else "") if x)
                subprocess.run([sys.executable, str(mk), "--title", title,
                                "--subtitle", args.subtitle,
                                "--author", byline,
                                "--out", default_cover],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                cover = default_cover
                print(f"封面：{default_cover}")
            except Exception:
                cover = None

    epub_build = find_skill_script("tome-book/scripts/build_book.py")
    if not epub_build.is_file():
        sys.exit(f"找不到 {epub_build}，请先安装 tome-book skill。")

    cmd = [
        sys.executable, str(epub_build),
        "--project", root,
        "--title", title,
        "--author", author,
        "--editor", args.editor,
        "--date", args.date,
        "--out", out,
        "--md-out", md_out,
        "--mode", args.mode,
        "--pandoc", pandoc,
    ]
    if args.subtitle:
        cmd += ["--subtitle", args.subtitle]
    if cover:
        cmd += ["--cover", cover]
    if args.work:
        cmd += ["--work", args.work]

    print("运行：", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
