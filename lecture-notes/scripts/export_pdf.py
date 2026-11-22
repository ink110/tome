#!/usr/bin/env python3
"""Export an Obsidian lecture note to PDF with an Obsidian-like layout.

- resolves ![[attachments/...]] and ![[eagle-cache/lectures/...]] image embeds (base64, downscaled via sips)
- renders <details> transcripts as expanded sections
- converts [[wiki links]] to readable text
- prints HTML to PDF with headless Chrome
"""
import argparse
import base64
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import config

try:
    import markdown
except ImportError:
    markdown = None


def find_chrome():
    configured = config.get("tools.chrome", "auto")
    if configured and configured != "auto":
        return configured
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""
IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
               "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  font-size: 15px; line-height: 1.78; color: #24292f;
  max-width: 760px; margin: 0 auto; padding: 0 4px;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 { font-size: 27px; line-height: 1.35; margin: 0 0 10px; font-weight: 650; letter-spacing: .01em; }
h2 { font-size: 21px; line-height: 1.4; margin: 34px 0 12px; padding-bottom: 6px;
     border-bottom: 1px solid #e8e8e8; font-weight: 620;
     break-after: avoid; page-break-after: avoid; }
h3 { font-size: 17px; line-height: 1.5; margin: 24px 0 8px; font-weight: 620;
     break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 13px; text-align: justify; orphans: 2; widows: 2; }
strong { font-weight: 650; color: #111; }
a { color: #2f6feb; text-decoration: none; }
ul, ol { margin: 6px 0 14px; padding-left: 24px; }
li { margin: 4px 0; }
table { width: 100%; border-collapse: collapse; margin: 14px 0 20px; font-size: 13.5px; }
th, td { border: 1px solid #e5e7eb; padding: 7px 9px; vertical-align: top; text-align: left; }
th { background: #f6f7f8; font-weight: 650; }
tr { break-inside: avoid; page-break-inside: avoid; }
blockquote {
  margin: 18px 0; padding: 10px 16px; color: #555;
  background: #f7f7f8; border-left: 4px solid #d6d9de;
}
blockquote p { margin: 0; white-space: pre-line; }
hr { border: 0; border-top: 1px solid #e8e8e8; margin: 28px 0; }
img { max-width: 100%; height: auto; }
figure { margin: 18px 0; break-inside: avoid; page-break-inside: avoid; text-align: center; }
figure img { display: block; margin: 0 auto 8px; border-radius: 4px; }
figcaption { font-size: 12.5px; color: #777; line-height: 1.6; text-align: center; }
.wikilink { color: #2f6feb; }
.transcript {
  margin-top: 30px; border: 1px solid #e5e7eb; border-radius: 8px;
  background: #fafafa; padding: 0 20px 16px; break-before: page;
  font-size: 14.5px; line-height: 1.72;
}
.transcript-title {
  font-weight: 650; font-size: 15px; color: #333;
  padding: 13px 0; margin-bottom: 14px; border-bottom: 1px solid #e8e8e8;
}
.transcript h3 { margin-top: 20px; }
.transcript p { text-align: justify; margin-bottom: 10px; }
.missing { color: #b42318; font-size: 12px; }
.attachment { color: #777; font-size: 12px; }
@media print { a { color: #2f6feb; } }
"""

HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>{css}</style></head>
<body>{body}</body></html>"""

DETAILS_RE = re.compile(r"<details>\s*<summary>(.*?)</summary>(.*?)</details>", re.S)
EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
WIKI_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def image_data_uri(path: Path, tmp: Path, max_side: int) -> str:
    ext = path.suffix.lower()
    if ext == ".svg":
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode()
    if ext == ".gif":
        return "data:image/gif;base64," + base64.b64encode(path.read_bytes()).decode()
    out = tmp / (path.stem + ".jpg")
    if shutil.which("sips"):
        subprocess.run(["sips", "-Z", str(max_side), "-s", "format", "jpeg",
                        "-s", "formatOptions", "82", str(path), "--out", str(out)],
                       check=True, capture_output=True)
    elif shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                        "-vf", f"scale='min({max_side},iw)':-2", "-q:v", "2", str(out)],
                       check=True, capture_output=True)
    else:
        out.write_bytes(path.read_bytes())
    return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()


def preprocess(text: str, base: Path, tmp: Path, max_side: int) -> str:
    # Obsidian lets a bullet list start right after a bold-only line; Python-Markdown needs a blank line.
    text = re.sub(r"(?m)^(\*\*[^\n]+\*\*)\n(?=(?:[-*+] |\d+\. ))", r"\1\n\n", text)

    def embed(m):
        target, alt = m.group(1).strip(), (m.group(2) or "").strip()
        candidates = [
            (base / target).resolve(),
            (base.parent / target).resolve(),
            (base.parent.parent / target).resolve(),
            (base.parent.parent.parent / target).resolve(),
        ]
        path = next((c for c in candidates if c.exists()), None)
        if path is None:
            return f'<span class="missing">[缺图：{html.escape(target)}]</span>'
        if path.suffix.lower() not in IMG_EXTS:
            return f'<span class="attachment">[附件：{html.escape(path.name)}]</span>'
        uri = image_data_uri(path, tmp, max_side)
        return f'<img src="{uri}" alt="{html.escape(alt or path.name)}">'

    text = EMBED_RE.sub(embed, text)
    text = WIKI_RE.sub(lambda m: f'<span class="wikilink">{html.escape((m.group(2) or m.group(1)).replace(".md", ""))}</span>', text)
    return text


def figureize(html_text: str) -> str:
    # image paragraph immediately followed by a bold caption paragraph -> figure
    pattern = re.compile(r'<p>(<img [^>]+>)</p>\s*<p><strong>(.*?)</strong></p>', re.S)
    return pattern.sub(lambda m: f'<figure>{m.group(1)}<figcaption>{m.group(2)}</figcaption></figure>', html_text)


def convert_md(text: str, base: Path, tmp: Path, max_side: int) -> str:
    if markdown is None:
        raise SystemExit("PDF 导出需要 Python markdown 包：pip install markdown")
    text = preprocess(text, base, tmp, max_side)
    md = markdown.Markdown(extensions=["extra", "tables", "fenced_code", "sane_lists"])
    return figureize(md.convert(text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("note", help="path to .md note")
    ap.add_argument("--out", required=True, help="output PDF path")
    ap.add_argument("--max-image", type=int, default=1400)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    note = Path(args.note).resolve()
    base = note.parent
    text = note.read_text(encoding="utf-8")

    details = []
    def stash(m):
        details.append((m.group(1).strip(), m.group(2).strip()))
        return f"\n\n<!--TRANSCRIPT_{len(details) - 1}-->\n\n"

    text = DETAILS_RE.sub(stash, text)

    tmp = Path(tempfile.mkdtemp(prefix="note-pdf-"))
    body = convert_md(text, base, tmp, args.max_image)

    for i, (summary, inner) in enumerate(details):
        block = convert_md(inner, base, tmp, args.max_image)
        section = (f'<section class="transcript"><div class="transcript-title">'
                   f'{html.escape(summary)}</div>{block}</section>')
        body = body.replace(f"<!--TRANSCRIPT_{i}-->", section)

    title = note.stem
    html_doc = HTML.format(title=html.escape(title), css=CSS, body=body)
    html_path = tmp / "note.html"
    html_path.write_text(html_doc, encoding="utf-8")

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    if not chrome:
        raise SystemExit(
            "找不到 Chrome。PDF 是可选功能；需要时安装 Google Chrome，"
            "或在配置里设置 tools.chrome。"
        )
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--allow-file-access-from-files", "--no-pdf-header-footer",
                    "--virtual-time-budget=10000",
                    f"--print-to-pdf={out}", html_path.as_uri()],
                   check=True, capture_output=True)
    if not out.exists():
        raise SystemExit("PDF not created")
    print(f"PDF: {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    shutil.rmtree(tmp, ignore_errors=True)
    if args.open:
        if sys.platform == "darwin":
            subprocess.run(["open", str(out)])
        elif os.name == "nt":
            os.startfile(str(out))
        else:
            subprocess.run(["xdg-open", str(out)])


if __name__ == "__main__":
    main()
