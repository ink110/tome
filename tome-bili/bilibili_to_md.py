#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili_to_md.py — 一键自动化：B站口播爬取 → 模型结构化 → 写入 Obsidian

用法:
  python3 bilibili_to_md.py <BV号或链接> --out <Obsidian目录> [--model <模型名>] [--page N]

示例:
  python3 bilibili_to_md.py "https://www.bilibili.com/video/BV1xxxxxxxxx/?p=2" \
      --out "<项目目录>/00-收集"

前置条件:
  1. 模型档位已配置（claude / openai / ollama，见 tome/lib/tome_common.py）
  2. cookie 文件路径由配置决定（默认 ~/.bilibili-cookies.txt）
  3. 同目录有 bilibili_fetch.py 和 格式化提示词.md

流程: 爬取字幕 → 调用模型生成结构化 Markdown → 直接写入 Obsidian，全程无需手动复制粘贴。
"""
import sys, os, json, re, urllib.request
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bilibili_fetch as bf


def load_prompt():
    with open(os.path.join(HERE, "格式化提示词.md"), encoding="utf-8") as f:
        return f.read()


def call_ollama(model, system, user, timeout=900):
    return tc.chat(system, user, model=model, timeout=timeout,
                   temperature=0.3, stream=False, num_ctx=32768)


def crawl(bvid, page):
    cookie = bf.load_cookie_header()
    if not cookie:
        sys.exit(1)
    nav = bf.get_nav(cookie)
    if not nav:
        sys.exit(1)
    wbi = nav.get("wbi_img", {})
    img_key = (wbi.get("img_url", "") or "").split("/")[-1].split(".")[0]
    sub_key = (wbi.get("sub_url", "") or "").split("/")[-1].split(".")[0]
    view = bf.get_view(bvid, cookie)
    if not view:
        sys.exit(1)

    title = view.get("title", "")
    owner = view.get("owner", {}).get("name", "")
    duration = view.get("duration", 0)
    desc = view.get("desc", "").strip()
    cid = view.get("cid")
    pages = view.get("pages", [])
    if pages:
        idx = min(max(page, 1), len(pages)) - 1
        pg = pages[idx]
        cid = pg.get("cid", cid)
        part = pg.get("part", "")
        if part:
            title = part.replace("：：", "：")
        if pg.get("duration"):
            duration = pg.get("duration")

    sub_url = bf.get_subtitle_url(bvid, cid, img_key, sub_key, cookie)
    if not sub_url:
        sys.stderr.write("[提示] 该视频无 AI 字幕，需走 ASR 转写。\n")
        sys.exit(2)
    lines = bf.download_subtitle(sub_url, cookie)

    season = view.get("ugc_season") or {}
    meta = {"bvid": bvid, "title": title, "owner": owner,
            "duration_sec": duration, "desc": desc,
            "pubdate": view.get("pubdate"), "cid": cid,
            "stat": view.get("stat", {}),
            "season_id": season.get("id"),
            "collection_title": season.get("title")}
    return meta, lines


def build_user_message(meta, lines):
    sec = meta.get("duration_sec", 0)
    mm, ss = divmod(sec, 60)
    hh, mm = divmod(mm, 60)
    dur = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
    msg = (
        f"## 视频元信息\n标题：{meta['title']}\nUP主：{meta['owner']}\n"
        f"时长：{dur}\n简介：{meta.get('desc') or '（无）'}\n\n"
        f"## 原始口播稿（带时间戳）\n" + "\n".join(lines)
    )
    return msg


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:60]


def main():
    argv = sys.argv[1:]
    out_dir, model, page = None, None, None
    if "--out" in argv:
        i = argv.index("--out"); out_dir = os.path.expanduser(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    if "--model" in argv:
        i = argv.index("--model"); model = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
    if "--page" in argv:
        i = argv.index("--page"); page = int(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    if not argv or not out_dir:
        sys.stderr.write("用法: python3 bilibili_to_md.py <BV号或链接> --out <Obsidian目录> [--model <模型名>]\n")
        sys.exit(1)

    raw = argv[0].strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    bvid = m.group(1) if m else raw
    if page is None:
        mp = re.search(r"[?&]p=(\d+)", raw)
        if mp:
            page = int(mp.group(1))
    if page is None:
        page = 1

    print(f"=== 1. 爬取 {bvid} (p={page}) ===")
    meta, lines = crawl(bvid, page)
    print(f"    标题: {meta['title']}")
    print(f"    字幕: {len(lines)} 条")

    print(f"=== 2. 模型 {tc.model_name(model)} 结构化（可能需要几分钟）===")
    system = load_prompt()
    user = build_user_message(meta, lines)
    md = call_ollama(model, system, user)

    print("=== 3. 写入 Obsidian ===")
    os.makedirs(out_dir, exist_ok=True)
    fname = safe_name(meta["title"]) + ".md"
    path = os.path.join(out_dir, fname)
    frontmatter = (
        "---\n"
        f"title: {meta['title']}\n"
        f"up: {meta['owner']}\n"
        f"source: https://www.bilibili.com/video/{bvid}\n"
        f"date: {date.today().isoformat()}\n"
        "tags: [B站口播]\n"
        "---\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(frontmatter + md)
    print(f"✅ 已保存: {path}")


if __name__ == "__main__":
    try:
        main()
    except tc.ClaudeMode as exc:
        print(str(exc))
        sys.exit(5)
