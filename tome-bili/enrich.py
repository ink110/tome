#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已有的 00-收集/ 补传播数据（播放/点赞/投币/收藏/评论/转发）。

用途：老项目只存了 .md、没有 _meta.json，或 meta 里没有 stat。
按 frontmatter 的 source / bvid 逐条请求 view 接口，写回 _meta.json。
然后重跑 manifest.py 即可。

用法:
  python3 enrich.py --dir "<项目>/00-收集" --sleep 0.5
  python3 enrich.py --dir "..." --force      # 已有数据也重抓
"""
import argparse
import glob
import json
import os
import re
import sys
import time
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
import bilibili_fetch as bf  # noqa: E402


def read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def bvid_from(s):
    m = re.search(r"(BV[0-9A-Za-z]{10})", s or "")
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=0, help="最多补 N 条；0=全部")
    ap.add_argument("--force", action="store_true", help="已有 stat 也重抓")
    ap.add_argument("--include-multipart", action="store_true",
                    help="多分P也补；注意 view 返回的是整条视频的统计，不是单P")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    d = os.path.expanduser(args.dir)
    md_files = [f for f in sorted(glob.glob(os.path.join(d, "*.md")))
                if not os.path.basename(f).startswith("_")]
    if args.prefix:
        md_files = [f for f in md_files if os.path.basename(f).startswith(args.prefix)]
    if not md_files:
        raise SystemExit(f"{d} 下没有 .md")

    cookie = bf.load_cookie_header()
    if not cookie:
        sys.exit(1)

    done = skipped = failed = 0
    for i, md in enumerate(md_files, 1):
        fm = parse_frontmatter(read_text(md))
        bvid = bvid_from(fm.get("source", ""))
        if not bvid:
            print(f"[{i}/{len(md_files)}] {os.path.basename(md)} 没有 bvid，跳过")
            skipped += 1
            continue
        pm = re.search(r"[?&]p=(\d+)", fm.get("source", ""))
        page = int(pm.group(1)) if pm else None
        if page is not None and not args.include_multipart:
            print(f"[{i}/{len(md_files)}] {os.path.basename(md)} 多分P，跳过"
                  f"（加 --include-multipart 可补整条视频统计）")
            skipped += 1
            continue
        base = md[:-3]
        meta_path = base + "_meta.json"
        meta = {}
        if os.path.exists(meta_path):
            try:
                meta = json.load(open(meta_path, encoding="utf-8"))
            except Exception:
                meta = {}
        if meta.get("stat", {}).get("view") and not args.force:
            skipped += 1
            continue
        if args.limit and done >= args.limit:
            break

        if args.dry_run:
            print(f"[dry] {os.path.basename(md)} -> {bvid}")
            done += 1
            continue

        view = bf.get_view(bvid, cookie)
        if not view:
            print(f"[{i}/{len(md_files)}] {bvid} 取不到 view，停止（检查 cookie）")
            failed += 1
            break
        season = view.get("ugc_season") or {}
        meta.update({
            "bvid": bvid,
            "title": meta.get("title") or fm.get("title") or view.get("title", ""),
            "owner": meta.get("owner") or view.get("owner", {}).get("name", ""),
            "pubdate": view.get("pubdate"),
            "duration_sec": view.get("duration", 0),
            "stat": view.get("stat", {}),
            "cid": view.get("cid"),
            "season_id": season.get("id"),
            "collection_title": season.get("title", ""),
            "source": fm.get("source") or f"https://www.bilibili.com/video/{bvid}/",
            "page": page,
            "is_multipart": page is not None,
            "stat_scope": "video" if page is not None else "episode",
        })
        if "episode" not in meta:
            m = re.search(r"(\d{3})：", os.path.basename(md))
            if m:
                meta["episode"] = int(m.group(1))
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        st = meta["stat"]
        print(f"[{i}/{len(md_files)}] {bvid} ✅ 播放 {st.get('view', 0):,} "
              f"｜点赞 {st.get('like', 0):,}")
        done += 1
        time.sleep(max(0, args.sleep))

    print(f"\n完成：补 {done} 条，跳过 {skipped} 条，失败 {failed} 条")
    print("下一步：重跑 manifest.py 生成 _manifest.json 和 _coverage.md。")


if __name__ == "__main__":
    main()
