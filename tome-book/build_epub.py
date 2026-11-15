#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 Obsidian 里某个系列的文章合并导出成一本 epub 电子书（本地脚本，不调任何在线 API）。

用法示例：
  python3 build_epub.py --src "<文章目录>"            # --src 必需
  python3 build_epub.py --src "..." --start 11 --end 20   # 指定集数范围
  python3 build_epub.py --src "..." --title "我的书" --out "<输出目录>/我的书.epub"
  python3 build_epub.py --src "..." --toc-depth 2     # 目录展开到二级标题

默认输出到共享配置的 output.dir（默认 ~/Desktop）。

关键坑（已固化在代码里，不要删）：
  1. 正文里的 `---` 分隔线会触发 pandoc 表格歧义，吞掉后续章节标题，
     必须把独立 `---` 换成 `***`。
  2. 口播稿时间戳行 `[MM:SS] 文本` 会被 pandoc 误判成表格，需转成
     `> **[MM:SS]** 文本` 引用块。
  3. 各文件内容里的 H1 标题格式可能不一致（缺编号/书名号/顺序反），
     统一用文件名里的标准标题替换。
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402


def clean_transcript(text: str) -> str:
    """把「附：完整口播稿」节的时间戳行转成引用块，防止被 pandoc 误判为表格。"""
    lines = text.split("\n")
    res = []
    in_transcript = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("## 附：完整口播稿"):
            in_transcript = True
            res.append(ln)
            continue
        if in_transcript:
            if not s:
                res.append("")
                continue
            m = re.match(r"^(\[[0-9]{1,2}:[0-9]{2}\])\s*(.*)$", s)
            if m:
                ts, content = m.group(1), m.group(2)
                res.append(f"> **{ts}** {content}".rstrip())
            else:
                res.append("> " + ln)
            continue
        res.append(ln)
    return "\n".join(res)


def downgrade_h2(text: str) -> str:
    """把二级、三级标题降级，使其不进入目录。

    用于「总纲篇 + 原文附录」型电子书：只让总纲篇保留多级目录，
    其余各篇只留一级标题，避免目录被几十篇的固定小节撑爆。

    配合 --toc-depth 2 时，只降二级（三级本来就不进目录）；
    配合 --toc-depth 3 时，三级也一并降掉。
    """
    text = re.sub(r"^### (?!#)", "##### ", text, flags=re.MULTILINE)
    return re.sub(r"^## (?!#)", "#### ", text, flags=re.MULTILINE)


def normalize_h1(text: str, std_title: str) -> str:
    """把正文第一个 H1 标题替换成标准标题（来自文件名），保证电子书目录整齐。"""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            lines[i] = "# " + std_title
            break
    return "\n".join(lines)


def build(args):
    pandoc = args.pandoc or tc.pandoc_bin()
    if not pandoc:
        print("错误：找不到 pandoc。请安装 pandoc，或在 ~/.config/tome/config.json "
              "里设置 tools.pandoc。", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(pandoc):
        print(f"错误：找不到 pandoc 二进制：{pandoc}", file=sys.stderr)
        print("请先安装 pandoc，或检查 ~/.config/tome/config.json 里的 tools.pandoc。",
              file=sys.stderr)
        sys.exit(1)

    title = args.title or f"{args.prefix}（第{args.start}-{args.end}集）"
    out = args.out or str(tc.output_dir() / f"{title}.epub")

    nums = [f"{i:03d}" for i in range(args.start, args.end + 1)]
    parts = []
    for idx, n in enumerate(nums):
        m = sorted(glob.glob(os.path.join(args.src, f"{args.prefix}{n}*.md")))
        if not m:
            print(f"警告：找不到 {args.prefix}{n} 对应的文章，已跳过", file=sys.stderr)
            continue
        t = open(m[0], encoding="utf-8").read()
        t = re.sub(r"^---\n.*?\n---\n?", "", t, flags=re.DOTALL)   # 去 frontmatter
        t = re.sub(r"^---\s*$", "***", t, flags=re.MULTILINE)      # --- → ***
        std_title = os.path.basename(m[0])[:-3]                    # 去掉 .md
        t = normalize_h1(t, std_title)
        t = clean_transcript(t)
        if args.deep_toc_first and idx >= args.deep_toc_first:
            t = downgrade_h2(t)                                    # 附录篇：二级标题不占目录
        parts.append(t.strip() + "\n")
        print(f"OK {n} -> {os.path.basename(m[0])}")

    if not parts:
        print("错误：没有找到任何文章，请检查 --src 和 --prefix。", file=sys.stderr)
        sys.exit(1)

    open(args.tmp, "w", encoding="utf-8").write("\n\n".join(parts))

    cmd = [
        pandoc, args.tmp,
        "-f", "markdown-yaml_metadata_block",
        "-o", out,
        "--metadata", f"title={title}",
        "--metadata", f"author={args.author}",
        "--metadata", "lang=zh-CN",
        "--toc", f"--toc-depth={args.toc_depth}",
    ]
    subprocess.run(cmd, check=True)
    print(f"完成：{out}（共 {len(parts)} 章，目录 {args.toc_depth} 级）")
    return out


def main():
    p = argparse.ArgumentParser(description="Obsidian 系列文章 → epub 电子书")
    p.add_argument("--start", type=int, default=1, help="起始集数（含），默认 1")
    p.add_argument("--end", type=int, default=80, help="结束集数（含），默认 80")
    p.add_argument("--src", required=True, help="文章所在目录（必需）")
    p.add_argument("--prefix", default="", help="文件名前缀，用于匹配和默认书名")
    p.add_argument("--title", default=None, help="书名，默认「{prefix}（第start-end集）」")
    p.add_argument("--out", default=None,
                   help="输出路径，默认 <output.dir>/{title}.epub")
    p.add_argument("--author", default="", help="作者名")
    p.add_argument("--toc-depth", type=int, default=1, help="目录层级，1=只留每集标题")
    p.add_argument("--deep-toc-first", type=int, default=0, metavar="N",
                   help="只让前 N 篇保留二级目录（配合 --toc-depth 2），其余篇的二级标题"
                        "降为四级不进目录。默认 0=全部按 --toc-depth 处理。"
                        "「总纲篇 + 原文附录」型电子书用 1。")
    p.add_argument("--pandoc", default=None,
                   help="pandoc 二进制路径；默认自动查找")
    p.add_argument("--tmp", default=str(Path(tempfile.gettempdir()) / "tome-merged.md"),
                   help="中间合并文件路径")
    build(p.parse_args())


if __name__ == "__main__":
    main()
