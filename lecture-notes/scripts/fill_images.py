#!/usr/bin/env python3
"""Copy mapped images into the vault media folder and fill image slots.

Two placement modes, and they can be mixed in one vault:

1. Inline (default for article-style notes) — put a marker where the image
   should appear, right after the paragraph it supports:

       <!-- IMG: IMG_010.jpg | 图注：这张图证明了什么 -->

2. Gallery — keep a __IMAGES__ placeholder (overview / main visual):

       __IMAGES__

images.json:
{
  "00-总览.md": [{"file": "IMG_001.jpg", "caption": "主视觉"}],
  "01-主题.md": [{"file": "IMG_010.jpg", "caption": "默认图注"}]
}

The marker caption wins over the caption in images.json.
Images are only copied when they are actually used.
"""
import argparse
import json
import os
import re
import shutil

MARKER = re.compile(r"<!--\s*IMG:\s*(.+?)\s*[|｜]\s*(.*?)\s*-->")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--src", required=True, help="folder containing source images")
    ap.add_argument("--placeholder", default="__IMAGES__")
    ap.add_argument("--media-dir", default="", help="媒体目录；默认 <vault>/attachments")
    ap.add_argument("--link-prefix", default="attachments", help="笔记里的链接前缀，如 eagle-cache/lectures/<专题>")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mapping = json.load(open(args.map, encoding="utf-8"))
    att_dir = args.media_dir or os.path.join(args.vault, "attachments")
    link_prefix = (args.link_prefix or "attachments").strip("/")
    os.makedirs(att_dir, exist_ok=True)

    for note, items in mapping.items():
        note_path = os.path.join(args.vault, note)
        if not os.path.exists(note_path):
            print(f"SKIP missing note: {note}")
            continue

        content = open(note_path, encoding="utf-8").read()
        has_marker = MARKER.search(content) is not None
        has_placeholder = args.placeholder in content
        if not has_marker and not has_placeholder:
            print(f"SKIP no image slot: {note}")
            continue

        prefix = note[:2] if note[:2].isdigit() else "img"
        entries = []
        by_key = {}
        seen_real = set()
        seq = 0

        for item in items:
            src = item["file"]
            if not os.path.isabs(src):
                src = os.path.join(args.src, src)
            if not os.path.exists(src):
                print(f"  MISSING {src}")
                continue
            real = os.path.realpath(src)
            if real in seen_real:
                print(f"  DUPLICATE skipped: {os.path.basename(src)}")
                continue
            seen_real.add(real)
            seq += 1
            ext = os.path.splitext(src)[1].lower()
            entry = {
                "src": src,
                "name": f"{prefix}-{seq:03d}{ext}",
                "caption": item.get("caption", "").strip(),
                "used": False,
                "copied": False,
            }
            entries.append(entry)
            by_key[os.path.basename(src)] = entry
            by_key[src] = entry

        def ensure_copy(entry):
            if entry["copied"]:
                return
            entry["copied"] = True
            dst = os.path.join(att_dir, entry["name"])
            if not args.dry_run and not os.path.exists(dst):
                shutil.copy2(entry["src"], dst)

        def block_for(entry, caption, bold=False):
            ensure_copy(entry)
            entry["used"] = True
            lines = [f"![[{link_prefix}/{entry['name']}]]"]
            if caption:
                lines.append(f"**{caption}**" if bold else f"*{caption}*")
            return "\n".join(lines)

        inline_names = set()

        def repl(m):
            key = m.group(1).strip()
            caption = m.group(2).strip()
            entry = by_key.get(key) or by_key.get(os.path.basename(key))
            if entry is None:
                print(f"  MISSING marker image: {key}")
                return m.group(0)
            if entry["name"] in inline_names:
                print(f"  DUPLICATE inline image: {key}")
                return ""
            inline_names.add(entry["name"])
            return block_for(entry, caption or entry["caption"])

        content = MARKER.sub(repl, content)

        if args.placeholder in content:
            lines = []
            for entry in entries:
                if entry["used"]:
                    continue
                lines.append(block_for(entry, entry["caption"], bold=True))
                lines.append("")
            block = "\n".join(lines).rstrip()
            content = content.replace(args.placeholder, block, 1)

        unused = [os.path.basename(e["src"]) for e in entries if not e["used"]]
        for name in unused:
            print(f"  UNUSED image: {name}")

        if not args.dry_run:
            open(note_path, "w", encoding="utf-8").write(content)
        print(f"OK {note}: {len(inline_names)} inline, "
              f"{sum(1 for e in entries if e['used']) - len(inline_names)} gallery")


if __name__ == "__main__":
    main()
