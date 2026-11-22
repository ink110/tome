#!/usr/bin/env python3
"""Group OCR results by slide title and keep the most complete frame per slide.

Output is intentionally lightweight: title, time, chars and a short excerpt.
Do not feed all raw OCR files into the online model when writing the note.
"""
import argparse
import difflib
import json
import re
from pathlib import Path

HEADER = re.compile(
    r"^(M\s*XC|MXC|MiT|MI\b|Mi\b|IxDC|IXDC|kDC|lxDC|Experience Center)", re.I)
CODE = re.compile(
    r"^(<\?php|<\?|<meta|<link|<title|id=|pos\s|\$|et\(|\?>|\}|\)|"
    r"attributes\(\);|loginfo\(|title\(|rel=)")
PUNCT = re.compile(r"[\s，。！？、：；“”‘’（）()《》【】\[\]|·,.:;!?\"'“”]+")


def clean_lines(text: str):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or HEADER.match(s):
            continue
        if s.startswith("<?") or s.startswith("$") or s.startswith("id="):
            continue
        if s in {"```", "---"}:
            continue
        out.append(s)
    return out


def is_code(lines):
    if not lines:
        return False
    code = sum(1 for s in lines if CODE.match(s))
    return code / len(lines) > 0.3


def title_of(lines):
    for s in lines:
        if s in {"“", "”", "\"", "'"}:
            continue
        if len(s) >= 3:
            return s
    return lines[0] if lines else ""


def norm_title(s: str):
    return PUNCT.sub("", s).lower()


def time_of(name: str):
    m = re.search(r"_(\d+)s", name)
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.82)
    ap.add_argument("--include-code", action="store_true")
    ap.add_argument("--min-chars", type=int, default=8)
    args = ap.parse_args()

    files = sorted(Path(args.src).glob("*.txt"))
    groups = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = clean_lines(text)
        if len("".join(lines)) < args.min_chars:
            continue
        title = title_of(lines)
        key = norm_title(title)
        code = is_code(lines)
        item = {
            "file": path.name,
            "time": time_of(path.stem),
            "title": title,
            "chars": len(text),
            "kind": "code" if code else "slide",
            "excerpt": " / ".join(lines[:3])[:300],
        }
        found = None
        for g in groups:
            if key == g["key"] or difflib.SequenceMatcher(None, key, g["key"]).ratio() >= args.threshold:
                found = g
                break
        if found is None:
            groups.append({"key": key, "items": [item]})
        else:
            found["items"].append(item)

    selected = []
    for g in groups:
        items = g["items"]
        # Prefer a real slide over a code screenshot; then the most complete OCR.
        items_sorted = sorted(
            items, key=lambda x: (x["kind"] == "code", -x["chars"]))
        best = items_sorted[0]
        if best["kind"] == "code" and not args.include_code:
            continue
        selected.append(best)

    selected.sort(key=lambda x: x["time"])
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"slides: {len(selected)} / groups: {len(groups)} / ocr files: {len(files)}")
    for s in selected:
        print(f'{s["time"]:>5}s  {s["title"][:40]:40s}  {s["file"]}')


if __name__ == "__main__":
    main()
