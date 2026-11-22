#!/usr/bin/env python3
"""Validate Obsidian lecture notes: body duplicates/English, details pairing, links."""
import argparse
import glob
import os
import re
import sys

CLOSE = set("」”）》】")


def details_bodies(text: str):
    return re.findall(r"<details>\s*\n<summary>.*?</summary>\s*\n(.*?)\n</details>", text, re.S)


def body_text(text: str):
    """Readable body: drop frontmatter, code blocks, details, captions and Eagle links."""
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("*图注：", "**图注：", "![[", ">")) or "eagle://" in s:
            continue
        kept.append(line)
    return "\n".join(kept)


def check_note(path: str, allow_english: bool):
    text = open(path, encoding="utf-8").read()
    errors = []

    od, cd = text.count("<details>"), text.count("</details>")
    if od != cd:
        errors.append(f"details unbalanced: {od}/{cd}")

    bodies = details_bodies(text)
    body = body_text(text)
    if body.strip():
        bodies.append(body)

    for body in bodies:
        sents = [s.strip() for s in re.split(r"(?<=[。！？])", body) if len(s.strip()) > 12]
        dup = len(sents) - len(set(sents))
        if dup:
            errors.append(f"duplicate sentences: {dup}")

        if not allow_english:
            eng = re.findall(r"(?<![A-Za-z])[A-Za-z]+(?:\s+[A-Za-z]+){3,}(?![A-Za-z])", body)
            if eng:
                errors.append(f"English sentences: {len(eng)} (first: {eng[0][:50]})")

        bad = [l[:40] for l in body.split("\n") if l and l[0] in CLOSE]
        if bad:
            errors.append(f"lines starting with closing punctuation: {len(bad)}")

    if "__IMAGES__" in text:
        errors.append("unreplaced __IMAGES__ placeholder")
    if "<!-- IMG:" in text or "<!-- IMG：" in text:
        errors.append("unreplaced inline image marker")

    base = os.path.dirname(os.path.abspath(path))
    for m in re.finditer(r"!?\[\[([^\]]+)\]\]", text):
        target = m.group(1).split("|")[0].strip()
        if target.startswith(("http://", "https://")):
            continue
        if not (os.path.exists(os.path.join(base, target)) or
                os.path.exists(os.path.join(base, target + ".md"))):
            errors.append(f"broken link: {target}")

    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vault", help="note directory (vault-relative links resolved here)")
    ap.add_argument("--allow-english", action="store_true", help="skip English sentence check")
    args = ap.parse_args()

    notes = sorted(glob.glob(os.path.join(args.vault, "*.md")))
    if not notes:
        raise SystemExit(f"no .md found in {args.vault}")

    failed = 0
    total_embeds = 0
    for path in notes:
        text = open(path, encoding="utf-8").read()
        total_embeds += len(re.findall(r"!\[\[([^\]]+)\]\]", text))
        errors = check_note(path, args.allow_english)
        if errors:
            failed += 1
            print(f"FAIL {os.path.basename(path)}")
            for e in errors:
                print("  -", e)
        else:
            print(f"OK   {os.path.basename(path)}")

    print(f"\nnotes={len(notes)} embeds={total_embeds} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
