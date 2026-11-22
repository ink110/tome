#!/usr/bin/env python3
"""Merge SRT fragments into readable paragraphs, breaking on silence gaps."""
import argparse
import glob
import os
import re


def ts2sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def srt_to_text(path: str, gap: float) -> str:
    content = open(path, encoding="utf-8").read()
    lines = []
    prev_end = None
    for block in re.split(r"\n\s*\n", content.strip()):
        parts = block.strip().split("\n")
        if len(parts) < 3:
            continue
        m = re.match(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", parts[1])
        if not m:
            continue
        start, end = ts2sec(m.group(1)), ts2sec(m.group(2))
        text = "".join(parts[2:]).strip()
        text = re.sub(r"<[^>]+>", "", text)
        if not text:
            continue
        if prev_end is not None and start - prev_end > gap:
            lines.append("\n")
        lines.append(text)
        prev_end = end
    out = "\n".join(lines)
    return re.sub(r"(?<!\n)\n(?!\n)", "", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="SRT file or directory")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gap", type=float, default=1.5, help="paragraph break if silence > N seconds")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    paths = [args.src] if os.path.isfile(args.src) else sorted(glob.glob(os.path.join(args.src, "*.srt")))
    if not paths:
        raise SystemExit(f"no .srt found in {args.src}")

    for path in paths:
        text = srt_to_text(path, args.gap)
        name = os.path.splitext(os.path.basename(path))[0] + ".txt"
        dst = os.path.join(args.out, name)
        open(dst, "w", encoding="utf-8").write(text)
        print(f"{name}: {len(text)} chars")


if __name__ == "__main__":
    main()
