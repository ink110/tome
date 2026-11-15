#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Obsidian 项目构建一本「阅读优化」epub + 合并 md。

一条命令做三件事：
  1. 合并：总纲 / 逐集 / 外部素材 / 传播案例 / 附录
  2. 清洗：frontmatter、---、H1、口播稿分段、时间戳
  3. 后处理：表格转卡片、封面、扉页、篇章页、nav/NCX、OPF、CSS、打包

用法:
  python3 build_book.py --project "<项目目录>" \
      --title "<书名>" \
      --out "<输出目录>/<书名>.epub" \
      --md-out "<项目目录>/20-输出/<书名>.md"

  python3 build_book.py --config book.json
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
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


def write(p, s):
    os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def esc(s):
    return html.escape(s or "", quote=True)


def split_frontmatter(text):
    text = text.replace("\r\n", "\n")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


def normalize_h1(s, title):
    lines = s.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            lines[i] = "# " + title
            return "\n".join(lines)
    return f"# {title}\n\n{s.lstrip()}"


def fix_headings(s):
    """标题前补空行，避免被上一段列表吞掉。"""
    lines = s.split("\n")
    out = []
    for ln in lines:
        if re.match(r"^#{1,6} ", ln) and out and out[-1].strip() and not re.match(r"^#{1,6} ", out[-1]):
            out.append("")
        out.append(ln)
    return "\n".join(out)


def clean_asr_quotes(s):
    """引用块里如果是 ASR 片段，去掉段号和行内时间戳，保留纯文本。"""
    lines = s.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith(">"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith(">"):
                j += 1
            block = lines[i:j]
            is_asr = any(re.match(r"^>\s*(?:\d{3}\s*)?\[\d{1,2}:\d{2}\]", b) for b in block)
            if is_asr:
                for b in block:
                    b = re.sub(r"^>\s*\d{3}\s*\[\d{1,2}:\d{2}\]\s*", "> ", b)
                    b = re.sub(r"^>\s*\[\d{1,2}:\d{2}\]\s*", "> ", b).rstrip()
                    if b.strip() != ">":
                        out.append(b)
            else:
                out.extend(block)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def list_items(block):
    return [re.sub(r"\s+", " ", x).strip() for x in re.findall(r"^\s*[-*]\s+(.*)$", block, re.M)]


def dedupe_core_points(s):
    """学习记录 > 核心观点 与 核心观点速览 逐条相同时删掉，避免重复。"""
    m1 = re.search(r"^## 核心观点速览\s*\n(.*?)(?=^## )", s, re.M | re.S)
    m2 = re.search(r"^### 核心观点\s*\n(.*?)(?=^### |^## )", s, re.M | re.S)
    if not m1 or not m2:
        return s
    a, b = list_items(m1.group(1)), list_items(m2.group(1))
    if a and a == b:
        s = s[:m2.start()] + s[m2.end():]
        s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def _transcript_bounds(s):
    m = re.search(r"^##\s*附：(?:完整)?口播稿[^\n]*\n", s, re.M)
    if not m:
        return None
    rest = s[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    body, tail = (rest[:nxt.start()], rest[nxt.start():]) if nxt else (rest, "")
    return m.start(), m.end(), body, tail


def clean_transcript(s):
    """阅读版：把一行一句的口播稿按约 280 字分段，每段只留开头时间戳。"""
    bounds = _transcript_bounds(s)
    if not bounds:
        return s
    start, end, body, tail = bounds
    segs = []
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        mm = re.match(r"^\[(\d{1,2}):(\d{2})\]\s*(.*)$", ln)
        if mm:
            ts = f"[{int(mm.group(1)):02d}:{mm.group(2)}]"
            txt = mm.group(3).strip()
            if txt:
                segs.append([ts, txt])
        elif segs:
            segs[-1][1] += " " + ln
        else:
            segs.append(["[00:00]", ln])
    if not segs:
        return s[:start] + tail
    paras, cur, cur_ts, cur_len = [], [], None, 0
    for ts, txt in segs:
        if not cur:
            cur_ts = ts
        cur.append(txt)
        cur_len += len(txt) + 1
        if cur_len >= 280:
            paras.append((cur_ts, " ".join(cur)))
            cur, cur_len = [], 0
    if cur:
        paras.append((cur_ts, " ".join(cur)))
    out = ["## 附：原文口播稿", "",
           "> 以下为原始口播稿，按时间分段，保留口语与停顿。仅供查证，可跳过。", "",
           "::: {.transcript}"]
    for ts, txt in paras:
        out.append(f"{ts} {txt}")
        out.append("")
    out.append(":::")
    out.append("")
    return s[:start] + "\n".join(out) + tail


def quote_transcript(s):
    """归档版：保留完整时间戳，一行一句，转成引用块避免 pandoc 表格误判。"""
    bounds = _transcript_bounds(s)
    if not bounds:
        return s
    start, end, body, tail = bounds
    out = ["## 附：原文口播稿（完整时间戳）", "",
           "> 以下保留原始时间戳，仅供查证。", ""]
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        mm = re.match(r"^\[(\d{1,2}):(\d{2})\]\s*(.*)$", ln)
        if mm:
            out.append(f"> **[{int(mm.group(1)):02d}:{mm.group(2)}]** {mm.group(3).strip()}")
        else:
            out.append("> " + ln)
        out.append("")
    return s[:start] + "\n".join(out) + tail


def add_source_link(md, source, label="原视频"):
    if not source:
        return md
    lines = md.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            lines[i + 1:i + 1] = ["", f"> [{label}]({source})"]
            break
    return "\n".join(lines)


def prep(text, title, mode="reading", source=None, label="原视频"):
    fm, body = split_frontmatter(text)
    source = source or fm.get("source")
    body = normalize_h1(body, title)
    body = re.sub(r"^---\s*$", "***", body, flags=re.M)
    body = fix_headings(body)
    body = clean_asr_quotes(body)
    body = dedupe_core_points(body)
    if mode == "reading":
        body = clean_transcript(body)
    else:
        body = quote_transcript(body)
    body = add_source_link(body, source, label)
    return body.strip() + "\n"


def strip_first_h1_if(text, needles):
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            if any(n and n in ln for n in needles):
                del lines[i]
            break
    return "\n".join(lines)


def promote_spread_h2(text):
    if not re.search(r"^# S\d+", text, re.M) and re.search(r"^## S\d+", text, re.M):
        text = re.sub(r"^## (?=S\d+)", "# ", text, flags=re.M)
    return text


def demote_h1(text):
    return re.sub(r"^# (?!#)", "### ", text, flags=re.M)


# ---------------- 自动识别项目结构 ----------------

def detect_parts(project, title, mode):
    parts = []
    p10 = os.path.join(project, "10-画像")
    mains = [f for f in glob.glob(os.path.join(p10, "*思维操作系统*.md"))
             if all(x not in os.path.basename(f) for x in ("核验", "全书", "阅读版"))
             and "_调研" not in f]
    if not mains:
        raise SystemExit(f"找不到画像：{p10}/*思维操作系统*.md")
    main = max(mains, key=os.path.getmtime)
    n_items = len(re.findall(r"^###\s+\d{2}\s*·", read(main), re.M))
    main_title = f"000 · 总纲：{n_items} 条思维方式" if n_items else "000 · 总纲"
    parts.append({
        "kind": "main", "title": "总纲", "chapter_title": main_title,
        "part_page": False, "items": [{"path": main, "title": main_title}],
    })

    eps = []
    for f in glob.glob(os.path.join(project, "00-收集", "*.md")):
        b = os.path.basename(f)
        if b.startswith("_"):
            continue
        m = re.search(r"(\d{3})：(.+)\.md$", b)
        if m:
            eps.append((int(m.group(1)), f, f"{int(m.group(1)):03d} · {m.group(2)}"))
    if eps:
        eps.sort()
        parts.append({
            "kind": "chapters", "title": "第一篇 · 逐集案例",
            "kicker": "PART ONE", "part_page": True,
            "intro": f"本编收录 {len(eps)} 篇逐集案例。顺着读，是一套完整的时间轨迹。",
            "note": "每篇：核心观点 → 分节正文 → 案例速查 → 金句 → 拆解笔记 → 原文。",
            "items": [{"path": f, "title": t} for _, f, t in eps],
        })

    exts = []
    for f in glob.glob(os.path.join(project, "00-收集-外部", "*.md")):
        b = os.path.basename(f)
        if b.startswith("_"):
            continue
        m = re.search(r"外部(\d+)：(.+)\.md$", b)
        if m:
            exts.append((int(m.group(1)), f, f"外部素材 {int(m.group(1)):02d} · {m.group(2)}"))
        else:
            exts.append((len(exts) + 1, f, f"外部素材 {len(exts) + 1:02d} · {os.path.splitext(b)[0]}"))
    if exts:
        exts.sort()
        parts.append({
            "kind": "chapters", "title": "第二篇 · 外部素材",
            "kicker": "PART TWO", "part_page": True,
            "intro": f"本编收录 {len(exts)} 篇外部材料。每篇附来源、URL 与证据强度。",
            "note": "结论强度：✅ 实锤 ｜ ⚖️ 和解 ｜ 🔶 指控 ｜ ❓ 传闻。起诉 ≠ 定罪，和解 ≠ 认罪。",
            "items": [{"path": f, "title": t} for _, f, t in exts],
        })

    spread = glob.glob(os.path.join(project, "20-输出", "*传播案例库*.md"))
    if spread:
        parts.append({
            "kind": "spread", "title": "第三篇 · 传播案例",
            "kicker": "PART THREE", "part_page": True,
            "intro": "本编拆解真正被传播的内容：钩子、结构、故事模板、可复用点。",
            "note": "只收传播证据最硬的内容；平台不公开互动数时，用可核验的代理指标，不估算。",
            "items": [{"path": spread[0], "title": "传播案例"}],
        })

    topics = glob.glob(os.path.join(project, "20-输出", "*选题清单*.md"))
    if topics:
        parts.append({
            "kind": "topics", "title": "附录 F · 选题清单", "part_page": False,
            "items": [{"path": topics[0], "title": "附录 F · 选题清单"}],
        })

    templates = glob.glob(os.path.join(project, "20-输出", "*内容结构模板*.md"))
    if templates:
        parts.append({
            "kind": "topics", "title": "附录 G · 内容结构模板", "part_page": False,
            "items": [{"path": templates[0], "title": "附录 G · 内容结构模板"}],
        })
    return parts


def parts_from_config(config, project):
    parts = []
    for p in config.get("parts", []):
        items = []
        for it in p.get("items", p.get("files", [])):
            if isinstance(it, str):
                path = it if os.path.isabs(it) else os.path.join(project, it)
                items.append({"path": path, "title": os.path.splitext(os.path.basename(path))[0]})
            else:
                path = it["path"]
                if not os.path.isabs(path):
                    path = os.path.join(project, path)
                items.append({"path": path, "title": it.get("title") or os.path.splitext(os.path.basename(path))[0]})
        parts.append({
            "kind": p.get("kind", "chapters"),
            "title": p.get("title", ""),
            "chapter_title": p.get("chapter_title") or p.get("title", ""),
            "kicker": p.get("kicker", ""),
            "intro": p.get("intro", ""),
            "note": p.get("note", ""),
            "part_page": p.get("part_page", p.get("kind", "chapters") not in ("main", "topics")),
            "items": items,
        })
    return parts


# ---------------- 合并 ----------------

def compose_byline(author, editor):
    """按实际填写的人拼署名，避免空 editor 留下「·  整理」。"""
    parts = []
    if author:
        parts.append(f"{author} 口述")
    if editor:
        parts.append(f"{editor} 整理")
    return " · ".join(parts)


def build_markdown(parts, title, subtitle, author, editor, mode, md_mode, md_date):
    pandoc_chunks = []
    reader_chunks = []
    for part in parts:
        kind = part["kind"]
        if part.get("part_page"):
            block = f"# {part['title']}\n\n{part.get('intro', '')}\n"
            pandoc_chunks.append(block)
            reader_chunks.append(block)
        for item in part["items"]:
            text = read(item["path"])
            if kind == "main":
                epub_body = prep(text, part["chapter_title"], mode)
                md_body = prep(text, part["chapter_title"], md_mode)
            elif kind == "spread":
                _, body = split_frontmatter(text)
                body = strip_first_h1_if(body, ["传播案例库", title])
                body = promote_spread_h2(body)
                epub_body = md_body = body
            elif kind == "topics":
                _, body = split_frontmatter(text)
                body = strip_first_h1_if(body, ["选题清单", "内容结构模板"])
                body = demote_h1(body)
                epub_body = md_body = f"# {part['title']}\n\n{body}"
            else:
                epub_body = prep(text, item["title"], mode)
                md_body = prep(text, item["title"], md_mode)
            pandoc_chunks.append(epub_body.strip() + "\n")
            reader_chunks.append(md_body.strip() + "\n")

    byline = compose_byline(author, editor) or "—"
    byline = f"{byline} · {md_date}"
    if subtitle:
        header = f"# {title}\n\n> {subtitle}\n>\n> {byline}\n"
    else:
        header = f"# {title}\n\n> {byline}\n"
    return ("\n\n".join(pandoc_chunks).strip() + "\n",
            header + "\n" + "\n\n".join(reader_chunks).strip() + "\n")


# ---------------- 后处理 ----------------

CASE_HEADERS = {
    ("案例", "一句话故事", "支撑观点", "可复用场景"),
    ("事件", "一句话", "支撑什么", "来源"),
    ("主题", "他说的", "外部可查证的做的", "出处"),
}


def convert_tables(s):
    def repl(m):
        table = m.group(1)
        th = re.search(r"<thead>(.*?)</thead>", table, re.S)
        if not th:
            return m.group(0)
        hs = tuple(strip_tags(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", th.group(1), re.S))
        if hs not in CASE_HEADERS:
            return m.group(0)
        tb = re.search(r"<tbody>(.*?)</tbody>", table, re.S)
        if not tb:
            return m.group(0)
        rows = re.findall(r"<tr>(.*?)</tr>", tb.group(1), re.S)
        out = ['<div class="case-list">']
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) != len(hs):
                continue
            out.append('<div class="case">')
            out.append(f'<p class="case-title">{cells[0].strip()}</p>')
            for h, c in zip(hs[1:], cells[1:]):
                c = c.strip()
                if c:
                    out.append(f'<p class="case-row"><span class="case-label">{esc(h)}</span>{c}</p>')
            out.append("</div>")
        out.append("</div>")
        return "\n".join(out)
    return re.sub(r"<table[^>]*>(.*?)</table>", repl, s, flags=re.S)


def convert_wide_tables(s):
    def repl(m):
        table = m.group(1)
        th = re.search(r"<thead>(.*?)</thead>", table, re.S)
        if not th:
            return m.group(0)
        hs = [strip_tags(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", th.group(1), re.S)]
        tb = re.search(r"<tbody>(.*?)</tbody>", table, re.S)
        if not tb or len(hs) < 2:
            return m.group(0)
        rows = re.findall(r"<tr>(.*?)</tr>", tb.group(1), re.S)
        is_def = len(hs) == 2 and tuple(hs) != ("他讲的", "他做的")
        out = ['<div class="defs">'] if is_def else ['<div class="case-list">']
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) != len(hs):
                continue
            cells = [c.strip() for c in cells]
            if is_def:
                if cells[0] and cells[1]:
                    out.append(f'<p class="def"><span class="def-term">{cells[0]}</span>{cells[1]}</p>')
                continue
            out.append('<div class="case">')
            if len(hs) == 2:
                for h, c in zip(hs, cells):
                    if c:
                        out.append(f'<p class="case-row"><span class="case-label">{esc(h)}</span>{c}</p>')
            else:
                if re.fullmatch(r"\d+", strip_tags(cells[0])) and len(cells) > 1:
                    out.append(f'<p class="case-title">{cells[1]}</p>')
                    pairs = [(hs[0], cells[0])] + list(zip(hs[2:], cells[2:]))
                else:
                    out.append(f'<p class="case-title">{cells[0]}</p>')
                    pairs = list(zip(hs[1:], cells[1:]))
                for h, c in pairs:
                    if c:
                        out.append(f'<p class="case-row"><span class="case-label">{esc(h)}</span>{c}</p>')
            out.append("</div>")
        out.append("</div>")
        return "\n".join(out)
    return re.sub(r"<table[^>]*>(.*?)</table>", repl, s, flags=re.S)


URL_RE = re.compile(r"https?://[^\s<>\"'）】。，；]+")


def wrap_urls(s):
    parts = re.split(r"(<[^>]+>)", s)
    for i, p in enumerate(parts):
        if p and not p.startswith("<"):
            parts[i] = URL_RE.sub(lambda m: f'<span class="url">{m.group(0)}</span>', p)
    return "".join(parts)


def wrap_ts(s):
    def repl(m):
        return re.sub(r"<p>\[(\d{2}:\d{2})\]", r'<p><span class="ts">[\1]</span>', m.group(0))
    return re.sub(r'<div class="transcript">.*?</div>', repl, s, flags=re.S)


def part_page_html(part, pid):
    return f'''<section id="{pid}" class="level1 part-page">
<p class="part-kicker">{esc(part.get("kicker", ""))}</p>
<h1 class="part-title">{esc(part["title"])}</h1>
<p class="part-sub">{esc(part.get("intro", ""))}</p>
<p class="part-note">{esc(part.get("note", ""))}</p>
</section>'''


def replace_first_level1_section(html, replacement):
    """替换第一个 level1 section 的完整区间。

    不能用非贪婪 `.*?</section>`：章节内含 level2 小节时会停在第一处
    内层闭合标签，留下孤立的 `</section>`，导致 XHTML 解析失败。
    """
    m = re.search(r'<section\b[^>]*\bclass="level1"[^>]*>', html)
    if not m:
        return html
    depth = 1
    pos = m.end()
    for tag in re.finditer(r'<section\b[^>]*>|</section\s*>', html[pos:], re.I):
        depth += -1 if tag.group(0).startswith("</") else 1
        if depth == 0:
            end = pos + tag.end()
            return html[:m.start()] + replacement + html[end:]
    return html


def build_inbook_toc(s):
    head_re = re.compile(r'<section id="([^"]+)" class="level([23])">\s*<h([23])[^>]*>(.*?)</h\3>', re.S)
    heads = [(int(m.group(2)), m.group(1), strip_tags(m.group(4))) for m in head_re.finditer(s)]
    groups = []
    for lvl, hid, text in heads:
        if text in ("目录", "全书结构"):
            continue
        if lvl == 2:
            groups.append({"id": hid, "text": text, "children": []})
        elif lvl == 3 and groups:
            groups[-1]["children"].append({"id": hid, "text": text})
    out = ['<div class="toc-list">']
    for g in groups:
        if not g["children"]:
            out.append(f'<div class="toc-group"><p class="toc-cat">'
                       f'<a href="#{esc(g["id"])}">{esc(g["text"])}</a></p></div>')
            continue
        out.append(f'<div class="toc-group"><p class="toc-cat">{esc(g["text"])}</p><ul>')
        for ch in g["children"]:
            out.append(f'<li><a href="#{esc(ch["id"])}">{esc(ch["text"])}</a></li>')
        out.append("</ul></div>")
    out.append("</div>\n<hr />")
    m = re.search(r'(<section id="目录" class="level2">\s*<h2>目录</h2>)(.*?)(</section>)', s, re.S)
    if m:
        s = s[:m.start()] + m.group(1) + "\n" + "\n".join(out) + "\n" + m.group(3) + s[m.end():]
    return s


def title_page_html(title, subtitle, author, editor, edition, start_href):
    sub = f'<p class="book-sub">{esc(subtitle)}</p>' if subtitle else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/stylesheet1.css" />
</head>
<body epub:type="frontmatter">
<section epub:type="titlepage" class="titlepage">
  <p class="book-kicker">CASE BOOK · {date.today().year}</p>
  <h1 class="book-title">{esc(title)}</h1>
  {sub}
  <p class="book-author">{esc(compose_byline(author, editor))}</p>
  <p class="book-edition">{esc(edition)}</p>
  <p class="book-start"><a href="{esc(start_href)}">开始阅读 →</a></p>
</section>
</body>
</html>
'''


def cover_page_html(img_href, title):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>封面</title>
  <style>
  html, body {{ margin:0; padding:0; height:100%; }}
  #cover-image {{ margin:0; padding:0; height:100%; text-align:center; }}
  #cover-image img {{ width:100%; height:100%; object-fit:cover; }}
  </style>
</head>
<body id="cover" epub:type="cover">
<div id="cover-image"><img src="../{esc(img_href)}" alt="《{esc(title)}》封面" /></div>
</body>
</html>
'''


CSS = r'''
:root { color-scheme: light dark; }
@page { margin: 14px; }
html, body { margin: 0; padding: 0; }
body { padding: 0 0.5em; }
body {
  font-family: "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", "STSong", Georgia, serif;
  line-height: 1.75; font-size: 1em; color: inherit; background: transparent;
  text-align: justify; text-justify: inter-ideograph; line-break: strict;
  overflow-wrap: break-word;
}
p { margin: 0 0 0.85em; text-indent: 0; widows: 2; orphans: 2; }
h1, h2, h3, h4, h5, h6 {
  font-family: "PingFang SC", "Noto Sans CJK SC", "Heiti SC", sans-serif;
  font-weight: 600; text-align: left; line-height: 1.35;
  page-break-after: avoid; page-break-inside: avoid;
}
h1 { font-size: 1.85em; margin: 0 0 1.1em; padding-bottom: 0.4em;
     border-bottom: 2px solid rgba(128,128,128,0.25); page-break-before: always; }
h2 { font-size: 1.42em; margin: 2.1em 0 0.7em; }
h3 { font-size: 1.18em; margin: 1.8em 0 0.55em; }
h4 { font-size: 1.06em; margin: 1.5em 0 0.45em; }
h5 { font-size: 1em; margin: 1.2em 0 0.35em; }
h6 { font-size: 0.95em; margin: 1em 0 0.3em; }
strong { font-weight: 600; }
em { font-style: italic; }
blockquote { margin: 1.15em 0; padding: 0.1em 0 0.1em 1em;
  border-left: 3px solid rgba(128,128,128,0.35); font-size: 0.96em; }
blockquote p { margin: 0 0 0.6em; opacity: 0.8; }
blockquote p:last-child { margin-bottom: 0; }
hr { border: 0; height: 1px; background: rgba(128,128,128,0.25); margin: 1.8em 0; }
ul, ol { margin: 0.6em 0 1em 1.4em; padding: 0; }
li { margin: 0.25em 0; }
li > ul, li > ol { margin-top: 0.25em; margin-bottom: 0.25em; }
code { font-family: Menlo, Monaco, Consolas, monospace; font-size: 0.86em;
  background: rgba(128,128,128,0.12); padding: 0.08em 0.28em; border-radius: 3px; }
pre { overflow: auto; }
img { max-width: 100%; height: auto; }
table { width: 100%; border-collapse: collapse; margin: 1.2em 0; font-size: 0.9em; }
th, td { padding: 0.45em 0.5em; border-bottom: 1px solid rgba(128,128,128,0.22);
  vertical-align: top; text-align: left; }
th { font-weight: 600; border-top: 1px solid rgba(128,128,128,0.3); }
a { color: inherit; }
.transcript { margin: 1em 0 2em; }
.transcript p { font-size: 0.94em; line-height: 1.7; opacity: 0.8; margin: 0 0 0.7em; }
.ts { font-family: "PingFang SC", "Noto Sans CJK SC", sans-serif; font-size: 0.78em;
  opacity: 0.5; font-variant-numeric: tabular-nums; margin-right: 0.35em; white-space: nowrap; }
.case-list { margin: 1.2em 0; }
.case { margin: 0 0 1em; padding: 0.8em 0.9em; border: 1px solid rgba(128,128,128,0.22);
  border-radius: 6px; background: rgba(128,128,128,0.06); page-break-inside: avoid; }
.case-title { font-family: "PingFang SC", "Noto Sans CJK SC", sans-serif;
  font-weight: 600; font-size: 1.02em; margin: 0 0 0.45em; }
.case-row { margin: 0 0 0.4em; font-size: 0.95em; }
.case-row:last-child { margin-bottom: 0; }
.case-label { display: inline-block; min-width: 5.2em; margin-right: 0.45em;
  font-family: "PingFang SC", sans-serif; font-size: 0.78em; opacity: 0.55; }
.defs { margin: 1em 0 1.4em; }
.def { margin: 0 0 0.6em; }
.def-term { font-family: "PingFang SC", "Noto Sans CJK SC", sans-serif;
  font-weight: 600; margin-right: 0.55em; }
.toc-list { margin: 1em 0; }
.toc-group { margin: 0 0 1.2em; }
.toc-cat { font-family: "PingFang SC", "Noto Sans CJK SC", sans-serif;
  font-weight: 600; font-size: 1.02em; margin: 0 0 0.35em; }
.toc-list ul { list-style: none; margin: 0; padding: 0; }
.toc-list li { margin: 0.28em 0; font-size: 0.96em; }
.toc-list a { text-decoration: none; }
.url { font-family: Menlo, Monaco, Consolas, monospace; font-size: 0.82em;
  word-break: break-all; opacity: 0.6; }
.titlepage { text-align: center; margin: 16% 1.2em 0; }
.titlepage .book-kicker { font-family: "PingFang SC", sans-serif; font-size: 0.78em;
  letter-spacing: 0.28em; opacity: 0.45; margin-bottom: 2.2em; }
.titlepage .book-title { font-family: "Songti SC", serif; font-size: 2.35em;
  line-height: 1.35; border: 0; margin: 0 0 0.55em; padding: 0;
  page-break-before: avoid; text-align: center; }
.titlepage .book-sub { font-family: "PingFang SC", sans-serif; font-size: 0.95em;
  opacity: 0.62; margin-bottom: 2.2em; }
.titlepage .book-author { font-family: "PingFang SC", sans-serif; font-size: 0.95em;
  margin-bottom: 0.4em; }
.titlepage .book-edition { font-family: "PingFang SC", sans-serif; font-size: 0.78em;
  opacity: 0.45; }
.titlepage .book-start { margin-top: 2.6em; font-family: "PingFang SC", sans-serif;
  font-size: 0.95em; }
.titlepage .book-start a { text-decoration: underline; }
.part-page { margin-top: 26%; }
.part-page .part-kicker { font-family: "PingFang SC", sans-serif; font-size: 0.75em;
  letter-spacing: 0.3em; opacity: 0.45; margin-bottom: 2em; }
.part-page .part-title { font-family: "Songti SC", serif; font-size: 2.4em;
  border: 0; padding: 0; margin: 0 0 0.3em; page-break-before: avoid; }
.part-page .part-sub { font-family: "PingFang SC", sans-serif; font-size: 1em;
  opacity: 0.55; margin-bottom: 2em; }
.part-page .part-note { font-size: 0.88em; line-height: 1.8; opacity: 0.5;
  border-top: 1px solid rgba(128,128,128,0.25); padding-top: 1em; }
'''


def parse_chapters(epubdir):
    chapters = []
    for f in sorted(glob.glob(os.path.join(epubdir, "text", "ch*.xhtml"))):
        s = read(f)
        m = re.search(r'<section id="([^"]*)" class="level1">\s*<h1[^>]*>(.*?)</h1>', s, re.S)
        if not m:
            print(f"!! no h1: {f}")
            continue
        chapters.append({"file": os.path.basename(f), "path": f,
                         "id": m.group(1), "title": strip_tags(m.group(2)), "html": s})
    return chapters


def assign_parts(parts, chapters):
    part_chapters = {i: [] for i in range(len(parts))}
    current = 0
    for ch in chapters[1:]:
        matched = None
        for i, p in enumerate(parts):
            if i == 0:
                continue
            if p.get("part_page") and ch["title"].strip() == p["title"].strip():
                matched = i
                break
        if matched is not None:
            parts[matched]["_chapter"] = ch
            current = matched
            continue
        part_chapters[current].append(ch)
    return part_chapters


def build_nav_tree(parts, chapters, part_chapters, main):
    main_children = []
    head_re = re.compile(r'<section id="([^"]+)" class="level([23])">\s*<h([23])[^>]*>(.*?)</h\3>', re.S)
    heads = [(int(m.group(2)), m.group(1), strip_tags(m.group(4))) for m in head_re.finditer(main["html"])]
    cur = None
    for lvl, hid, text in heads:
        if text == "目录":
            continue
        node = {"label": text, "href": f"text/{main['file']}#{hid}"}
        if lvl == 2:
            node["children"] = []
            main_children.append(node)
            cur = node
        elif lvl == 3 and cur is not None:
            cur["children"].append(node)

    tree = [{"label": "扉页", "href": "text/title_page.xhtml"},
            {"label": main["title"], "href": f"text/{main['file']}#{main['id']}",
             "children": main_children}]
    for i, p in enumerate(parts):
        if i == 0:
            continue
        chs = [c for c in part_chapters[i] if not c["title"].startswith("附录")]
        if p.get("part_page") and p.get("_chapter"):
            pc = p["_chapter"]
            tree.append({"label": p["title"], "href": f"text/{pc['file']}#{pc['id']}",
                         "children": [{"label": c["title"], "href": f"text/{c['file']}#{c['id']}"}
                                      for c in chs]})
        else:
            for c in chs:
                tree.append({"label": c["title"], "href": f"text/{c['file']}#{c['id']}"})
    for i, p in enumerate(parts):
        for c in part_chapters[i]:
            if c["title"].startswith("附录"):
                tree.append({"label": c["title"], "href": f"text/{c['file']}#{c['id']}"})
    return tree


def nav_ol(nodes, indent=1):
    pad = "  " * indent
    out = [f'{pad}<ol class="toc">']
    for n in nodes:
        out.append(f'{pad}  <li><a href="{esc(n["href"])}">{esc(n["label"])}</a>')
        if n.get("children"):
            out.append(nav_ol(n["children"], indent + 2))
        out.append(f'{pad}  </li>')
    out.append(f'{pad}</ol>')
    return "\n".join(out)


def ncx_points(nodes, counter, indent=1):
    pad = "  " * indent
    out = []
    for n in nodes:
        counter[0] += 1
        po = counter[0]
        out.append(f'{pad}<navPoint id="navPoint-{po}" playOrder="{po}">')
        out.append(f'{pad}  <navLabel><text>{esc(n["label"])}</text></navLabel>')
        out.append(f'{pad}  <content src="{esc(n["href"])}"/>')
        if n.get("children"):
            out.append(ncx_points(n["children"], counter, indent + 1))
        out.append(f'{pad}</navPoint>')
    return "\n".join(out)


def build_nav(epubdir, tree, title, uid):
    cover_li = ""
    if os.path.exists(os.path.join(epubdir, "text", "cover.xhtml")):
        cover_li = '    <li><a href="text/cover.xhtml" epub:type="cover">封面</a></li>\n'
    nav = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>目录</title>
  <link rel="stylesheet" type="text/css" href="styles/stylesheet1.css" />
</head>
<body epub:type="frontmatter">
<nav epub:type="toc" role="doc-toc" id="toc">
  <h1 id="toc-title">目录</h1>
{nav_ol(tree)}
</nav>
<nav epub:type="landmarks" id="landmarks" hidden="hidden">
  <ol>
{cover_li}    <li><a href="text/title_page.xhtml" epub:type="titlepage">扉页</a></li>
    <li><a href="#toc" epub:type="toc">目录</a></li>
  </ol>
</nav>
</body>
</html>
'''
    write(os.path.join(epubdir, "nav.xhtml"), nav)
    counter = [0]
    ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <head>
    <meta name="dtb:uid" content="{esc(uid)}" />
    <meta name="dtb:depth" content="3" />
    <meta name="dtb:totalPageCount" content="0" />
    <meta name="dtb:maxPageNumber" content="0" />
  </head>
  <docTitle><text>{esc(title)}</text></docTitle>
  <navMap>
{ncx_points(tree, counter)}
  </navMap>
</ncx>
'''
    write(os.path.join(epubdir, "toc.ncx"), ncx)


def postprocess(work, parts, chapters, title, subtitle, author, editor, edition, cover_img_href):
    epubdir = os.path.join(work, "EPUB")
    part_chapters = assign_parts(parts, chapters)
    main = chapters[0]

    for ch in chapters:
        s = ch["html"]
        kind_part = None
        for p in parts:
            if p.get("_chapter") is ch:
                kind_part = p
                break
        if kind_part:
            s = replace_first_level1_section(s, part_page_html(kind_part, ch["id"]))
        else:
            s = convert_tables(s)
            s = convert_wide_tables(s)
            s = wrap_urls(s)
            s = wrap_ts(s)
        if ch is main:
            s = build_inbook_toc(s)
        s = re.sub(r"<title>.*?</title>", f"<title>{esc(ch['title'])}</title>", s, count=1, flags=re.S)
        if 'name="viewport"' not in s:
            s = s.replace('<meta charset="utf-8" />',
                          '<meta charset="utf-8" />\n  <meta name="viewport" content="width=device-width, initial-scale=1" />', 1)
        ch["html"] = s
        write(ch["path"], s)

    # 目录
    tree = build_nav_tree(parts, chapters, part_chapters, main)

    # 扉页
    start_href = main["file"]
    write(os.path.join(epubdir, "text", "title_page.xhtml"),
          title_page_html(title, subtitle, author, editor, edition, start_href))

    # 封面
    if cover_img_href:
        write(os.path.join(epubdir, "text", "cover.xhtml"),
              cover_page_html(cover_img_href, title))

    # CSS
    write(os.path.join(epubdir, "styles", "stylesheet1.css"), CSS)

    # OPF
    opf_path = os.path.join(epubdir, "content.opf")
    opf = read(opf_path)
    uid = re.search(r'<dc:identifier[^>]*>(.*?)</dc:identifier>', opf)
    uid = uid.group(1) if uid else "urn:uuid:00000000-0000-0000-0000-000000000000"
    desc = f"{subtitle}。每条还原现场：背景、观点、案例、可迁移经验；附原文与可查证来源。"
    if "<dc:description>" not in opf:
        opf = opf.replace("</dc:title>", f"</dc:title>\n    <dc:description>{esc(desc)}</dc:description>", 1)
    if editor and "<dc:contributor" not in opf:
        opf = opf.replace("</dc:creator>",
                          f'</dc:creator>\n    <dc:contributor id="epub-contributor-1">{esc(editor)}（整理）</dc:contributor>\n'
                          f'    <meta refines="#epub-contributor-1" property="role" scheme="marc:relators">edt</meta>', 1)
    if "<dc:subject>" not in opf:
        subs = ("<dc:subject>思维方法</dc:subject>\n"
                "<dc:subject>人物案例</dc:subject>\n"
                f"<dc:subject>{esc(author)}</dc:subject>")
        opf = opf.replace("</dc:description>", f"</dc:description>\n    {subs}", 1)
    opf = re.sub(r'(<item id="cover_xhtml"[^>]*?) properties="svg"', r"\1", opf)
    if 'id="title_page_xhtml"' not in opf:
        opf = opf.replace("</manifest>",
                          '    <item id="title_page_xhtml" href="text/title_page.xhtml" media-type="application/xhtml+xml" />\n  </manifest>', 1)
    spine_m = re.search(r"(<spine[^>]*>)(.*?)(</spine>)", opf, re.S)
    if spine_m:
        body = spine_m.group(2)
        if 'title_page_xhtml' not in body:
            if 'cover_xhtml' in body:
                body = body.replace('<itemref idref="cover_xhtml" />',
                                    '<itemref idref="cover_xhtml" />\n    <itemref idref="title_page_xhtml" />', 1)
            else:
                body = '<itemref idref="title_page_xhtml" />\n' + body
        opf = opf[:spine_m.start()] + spine_m.group(1) + body + spine_m.group(3) + opf[spine_m.end():]
    if 'type="title-page"' not in opf:
        opf = opf.replace('<reference type="cover"',
                          '<reference type="title-page" title="扉页" href="text/title_page.xhtml" />\n    <reference type="cover"', 1)
    write(opf_path, opf)

    # iBooks 字体锁定
    ib = os.path.join(work, "META-INF", "com.apple.ibooks.display-options.xml")
    if os.path.exists(ib):
        s = read(ib).replace('<option name="specified-fonts">true</option>',
                             '<option name="specified-fonts">false</option>')
        write(ib, s)

    build_nav(epubdir, tree, title, uid)


def find_cover_href(work):
    opf_path = os.path.join(work, "EPUB", "content.opf")
    opf = read(opf_path)
    m = re.search(r'<item[^>]*properties="cover-image"[^>]*href="([^"]+)"', opf)
    if not m:
        m = re.search(r'<item[^>]*href="([^"]+)"[^>]*properties="cover-image"', opf)
    return m.group(1) if m else None


def pack(work, final):
    root = os.path.join(work, "EPUB")  # not used
    # mimetype 在 work 根目录（解压后的 epub 根）
    base = work
    # 确认 work 根有 mimetype；pandoc 解压后的结构是 work/EPUB + work/META-INF + work/mimetype
    mt = os.path.join(base, "mimetype")
    if not os.path.exists(mt):
        raise SystemExit("找不到 mimetype，解压结构异常")
    write(mt, "application/epub+zip")
    if os.path.exists(final):
        os.remove(final)
    subprocess.run(["zip", "-X0", final, "mimetype"], cwd=base, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["zip", "-Xr9", final, "META-INF", "EPUB"], cwd=base, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description="Obsidian 项目 → 阅读优化 epub + 合并 md")
    ap.add_argument("--project", default=None, help="项目根目录（topics/<人物>）")
    ap.add_argument("--config", default=None, help="book.json；不给则自动识别项目结构")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--editor", default="")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--cover", default=None)
    ap.add_argument("--no-cover", action="store_true", help="不生成封面")
    ap.add_argument("--out", default=None)
    ap.add_argument("--md-out", default=None)
    ap.add_argument("--mode", choices=["reading", "archive"], default="reading",
                    help="epub 的模式；阅读版默认 reading")
    ap.add_argument("--md-mode", choices=["reading", "archive"], default="archive",
                    help="合并 md 的模式；默认 archive，保留完整时间戳便于检索")
    ap.add_argument("--pandoc", default=None,
                    help="pandoc 路径；默认自动查找（见 tome_common.pandoc_bin）")
    ap.add_argument("--work", default=None)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--no-md", action="store_true")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    config = {}
    if args.config:
        with open(os.path.expanduser(args.config), encoding="utf-8") as f:
            config = json.load(f)

    project = args.project or config.get("project")
    if project:
        project = os.path.abspath(os.path.expanduser(project))
    if not project and not config.get("parts"):
        raise SystemExit("需要 --project 或 --config")
    pandoc = args.pandoc or tc.pandoc_bin()
    if not pandoc:
        raise SystemExit("找不到 pandoc。请安装 pandoc，或在 ~/.config/tome/config.json 里设置 tools.pandoc。")
    if not os.path.exists(pandoc):
        raise SystemExit(f"找不到 pandoc：{pandoc}")

    title = args.title or config.get("title") or (os.path.basename(project) if project else "未命名")
    subtitle = args.subtitle or config.get("subtitle", "")
    author = args.author or config.get("author", "")
    editor = args.editor or config.get("editor", "")
    edition = f"阅读优化版 · {args.date}" if args.mode == "reading" else f"归档版 · {args.date}"
    # 构建过程会切换工作目录，相对路径必须先转成绝对路径
    out = os.path.abspath(os.path.expanduser(
        args.out or config.get("out") or str(tc.output_dir() / f"{title}.epub")))
    md_out = args.md_out or config.get("md_out")
    if md_out:
        md_out = os.path.abspath(os.path.expanduser(md_out))
    cover = args.cover or config.get("cover")
    if cover:
        cover = os.path.abspath(os.path.expanduser(cover))
        if not os.path.exists(cover):
            print(f"⚠️ 封面不存在，忽略：{cover}")
            cover = None

    parts = parts_from_config(config, project) if config.get("parts") else detect_parts(project, title, args.mode)
    if not parts:
        raise SystemExit("没有可构建的章节")

    work = os.path.expanduser(args.work or "/tmp/epub-book-build")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)

    if not cover and not args.no_cover:
        cover = os.path.join(work, "auto-cover.png")
        mk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_cover.py")
        try:
            subprocess.run([sys.executable, mk, "--title", title, "--subtitle", subtitle,
                            "--author", compose_byline(author, editor), "--out", cover],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"自动封面：{cover}")
        except Exception as e:
            print(f"⚠️ 自动封面失败，继续无封面：{e}")
            cover = None

    pandoc_md, reader_md = build_markdown(parts, title, subtitle, author, editor,
                                          args.mode, args.md_mode, args.date)
    merged_path = os.path.join(work, "merged.md")
    write(merged_path, pandoc_md)
    if md_out and not args.no_md:
        write(md_out, reader_md)
        print(f"合并 md：{md_out}")

    temp_epub = os.path.join(work, "base.epub")
    cmd = [pandoc, merged_path, "-f", "markdown-yaml_metadata_block", "-o", temp_epub,
           "--metadata", f"title={title}", "--metadata", f"author={author}",
           "--metadata", f"lang=zh-CN", "--toc", "--toc-depth=3", "--split-level=1"]
    if subtitle:
        cmd += ["--metadata", f"subtitle={subtitle}"]
    if cover:
        cmd += ["--epub-cover-image", cover]
    print("pandoc 构建中…")
    subprocess.run(cmd, check=True)

    # 解压到 work 根：work/EPUB, work/META-INF, work/mimetype
    with zipfile.ZipFile(temp_epub) as z:
        z.extractall(work)
    epubdir = os.path.join(work, "EPUB")
    chapters = parse_chapters(epubdir)
    print(f"章节：{len(chapters)}")
    if not chapters:
        raise SystemExit("pandoc 没有生成章节")

    cover_img_href = find_cover_href(work)
    postprocess(work, parts, chapters, title, subtitle, author, editor, edition, cover_img_href)
    pack(work, out)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"完成：{out}（{size:.1f} MB，{len(chapters)} 章）")

    if not args.no_validate:
        val = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate.py")
        if os.path.exists(val):
            subprocess.run([sys.executable, val, out], check=False)

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
