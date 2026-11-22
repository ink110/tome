#!/usr/bin/env python3
"""Format and sanity-check a cleaned lecture transcript.

format: unwrap fragments, split sentences outside quotes/brackets, group 2-4
sentences per paragraph (target ~140 chars).
check: duplicate sentences, leftover English sentences, closing punctuation
stranded at line start, paragraph stats.
"""
import argparse
import re
import sys

OPEN = {"「": "」", "“": "”", "『": "』", "（": "）", "(": ")", "《": "》", "【": "】", "[": "]"}
CLOSE = set(OPEN.values())


def split_sents(text: str):
    out, buf, depth = [], "", 0
    for ch in text:
        buf += ch
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth = max(0, depth - 1)
        elif ch in "。！？" and depth == 0:
            s = buf.strip()
            if s:
                out.append(s)
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def group(sents, target: int, max_sent: int):
    paras, cur, n = [], [], 0
    for s in sents:
        cur.append(s)
        n += len(s)
        if n >= target or len(cur) >= max_sent:
            paras.append("".join(cur))
            cur, n = [], 0
    if cur:
        paras.append("".join(cur))
    return paras


def format_text(text: str, target: int, max_sent: int) -> str:
    text = text.replace("\r\n", "\n").replace("  \n", "")
    text = re.sub(r"(?<!\n)\n(?!\n)", "", text)
    blocks = re.split(r"\n\s*\n", text.strip())
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith("#") or block.startswith(">"):
            out.append(block)
            continue
        out.extend(group(split_sents(block), target, max_sent))
    return "\n\n".join(out) + "\n"


def check_text(text: str) -> int:
    text = text.replace("  \n", "\n")
    body = "\n".join(l for l in text.split("\n") if not l.startswith(("#", ">")))
    sents = [s.strip() for s in re.split(r"(?<=[。！？])", body) if len(s.strip()) > 12]
    dup = len(sents) - len(set(sents))

    eng = re.findall(r"(?<![A-Za-z])[A-Za-z]+(?:\s+[A-Za-z]+){3,}(?![A-Za-z])", body)
    bad_start = [l[:40] for l in body.split("\n") if l and l[0] in CLOSE]
    paras = [p for p in body.split("\n\n") if p.strip()]
    avg = round(sum(len(p) for p in paras) / len(paras)) if paras else 0

    print(f"chars={len(body)} paragraphs={len(paras)} avg={avg}")
    print(f"duplicate_sentences={dup}")
    print(f"english_sentences={len(eng)}")
    for s in eng[:5]:
        print("  ENG:", s)
    print(f"bad_line_starts={len(bad_start)}")
    for s in bad_start[:5]:
        print("  BAD:", s)

    return 1 if (dup or eng or bad_start) else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("format")
    f.add_argument("path")
    f.add_argument("--target", type=int, default=140)
    f.add_argument("--max-sent", type=int, default=4)

    c = sub.add_parser("check")
    c.add_argument("path")

    args = ap.parse_args()
    text = open(args.path, encoding="utf-8").read()

    if args.cmd == "format":
        open(args.path, "w", encoding="utf-8").write(format_text(text, args.target, args.max_sent))
        print(f"formatted: {args.path}")
        return 0

    return check_text(text)


if __name__ == "__main__":
    sys.exit(main())
