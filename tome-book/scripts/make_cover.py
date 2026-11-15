#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 1200×1800 封面 PNG（Chrome headless）。

用法:
  python3 make_cover.py --title "<书名>" \
      --subtitle "<副标题>" \
      --author "<人物> 口述" --out "<输出目录>/cover.png"
"""
import argparse
import html
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "cover.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--kicker", default="CASE BOOK")
    ap.add_argument("--accent", default="#B03A2E")
    ap.add_argument("--year", default=str(date.today().year))
    ap.add_argument("--out", default=None,
                    help="输出路径，默认 <output.dir>/cover.png")
    ap.add_argument("--chrome", default=None,
                    help="Chrome/Chromium 路径；默认自动查找")
    args = ap.parse_args()

    chrome = args.chrome or tc.chrome_bin()
    if not chrome:
        raise SystemExit("找不到 Chrome。请安装 Google Chrome / Chromium，"
                         "或在 ~/.config/tome/config.json 里设置 tools.chrome。")
    if not os.path.exists(chrome):
        raise SystemExit(f"找不到 Chrome：{chrome}")
    if not os.path.exists(TEMPLATE):
        raise SystemExit(f"找不到模板：{TEMPLATE}")

    out = os.path.expanduser(args.out or str(tc.output_dir() / "cover.png"))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tpl = open(TEMPLATE, encoding="utf-8").read()
    tpl = (tpl.replace("{{TITLE}}", html.escape(args.title))
              .replace("{{SUBTITLE}}", html.escape(args.subtitle))
              .replace("{{AUTHOR}}", html.escape(args.author))
              .replace("{{KICKER}}", html.escape(args.kicker))
              .replace("{{ACCENT}}", args.accent)
              .replace("{{YEAR}}", html.escape(args.year)))
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write(tpl)
        tmp = f.name

    cmd = [chrome, "--headless=new", "--hide-scrollbars",
           "--force-device-scale-factor=1",
           "--window-size=1200,1800",
           f"--screenshot={out}",
           f"file://{tmp}"]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.unlink(tmp)
    if not os.path.exists(out):
        raise SystemExit("封面生成失败")
    print(f"封面：{out}")


if __name__ == "__main__":
    main()
