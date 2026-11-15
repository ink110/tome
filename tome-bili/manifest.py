#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 00-收集/，生成清单和覆盖度报告。

优先读 _meta.json；没有 meta 的老文件，从蒸馏稿的 frontmatter 兜底。
这样即使早期只保存了 .md，也能进入清单。

用法:
  python3 manifest.py --dir "<项目>/00-收集" \
      --out "<项目>/00-收集/_manifest.json" \
      --coverage "<项目>/00-收集/_coverage.md"
"""
import argparse
import glob
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime


def read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


def parse_ep(name):
    m = re.search(r"(\d{3})：", name)
    return int(m.group(1)) if m else None


def parse_topic(name):
    base = re.sub(r"\.(?:md|txt|json)$", "", name)
    base = re.sub(r"_(?:raw|meta)$", "", base)
    m = re.search(r"\d{3}：(.+)$", base)
    return m.group(1).strip() if m else ""


def norm_title(s):
    return re.sub(r"[^\w一-鿿]+", "", (s or "").lower())


def ts_to_date(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return ""


def num(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def bvid_from(s):
    m = re.search(r"(BV[0-9A-Za-z]{10})", s or "")
    return m.group(1) if m else ""


def collect(d, prefix=None):
    md_files = [f for f in glob.glob(os.path.join(d, "*.md"))
                if not os.path.basename(f).startswith("_")]
    meta_files = glob.glob(os.path.join(d, "*_meta.json"))
    if prefix:
        md_files = [f for f in md_files if os.path.basename(f).startswith(prefix)]
        meta_files = [f for f in meta_files if os.path.basename(f).startswith(prefix)]

    meta_by_base = {}
    for fp in meta_files:
        base = os.path.basename(fp).replace("_meta.json", "")
        meta_by_base[base] = fp

    seen = set()
    items = []

    def add(md_path, meta_path):
        if md_path:
            base = os.path.basename(md_path)[:-3]
            fm = parse_frontmatter(read_text(md_path))
            title = fm.get("title", "")
            source = fm.get("source", "")
            tags = fm.get("tags", "")
            md_ok, raw_ok = True, os.path.exists(md_path[:-3] + "_raw.txt")
        else:
            base = os.path.basename(meta_path).replace("_meta.json", "")
            fm, title, source, tags = {}, "", "", ""
            md_ok, raw_ok = False, os.path.exists(meta_path.replace("_meta.json", "_raw.txt"))

        meta = read_json(meta_path) if meta_path else {}
        ep = meta.get("episode") if meta.get("episode") is not None else parse_ep(base)
        try:
            ep = int(ep) if ep is not None else None
        except (TypeError, ValueError):
            ep = None
        st = meta.get("stat") or {}
        bvid = meta.get("bvid") or bvid_from(source)
        if ep is None and not bvid:
            return  # 目录说明、索引等辅助文件，不算正文
        pm = re.search(r"[?&]p=(\d+)", source or meta.get("source", ""))
        page = int(pm.group(1)) if pm else None
        items.append({
            "ep": ep,
            "bvid": bvid,
            "title": meta.get("title") or title or parse_topic(base),
            "topic": parse_topic(base) or meta.get("title", ""),
            "owner": meta.get("owner", ""),
            "date": ts_to_date(meta.get("pubdate")) or fm.get("date", "") or meta.get("date", ""),
            "pubdate": meta.get("pubdate"),
            "duration_sec": num(meta.get("duration_sec")),
            "stat": {
                "view": num(st.get("view")),
                "like": num(st.get("like")),
                "coin": num(st.get("coin")),
                "favorite": num(st.get("favorite")),
                "share": num(st.get("share")),
                "reply": num(st.get("reply")),
                "danmaku": num(st.get("danmaku")),
            },
            "source_url": meta.get("source") or source or (
                f"https://www.bilibili.com/video/{bvid}/" if bvid else ""),
            "page": page,
            "is_multipart": page is not None,
            "stat_scope": "video" if page is not None else "episode",
            "raw": raw_ok,
            "md": md_ok,
            "is_asr": bool(meta.get("asr")) or "ASR" in str(tags),
            "season_id": meta.get("season_id"),
            "collection_title": meta.get("collection_title", ""),
        })
        seen.add(base)

    for md_path in sorted(md_files):
        base = os.path.basename(md_path)[:-3]
        add(md_path, meta_by_base.get(base))

    # 只有 meta、还没有蒸馏稿的
    for base, meta_path in sorted(meta_by_base.items()):
        if base not in seen:
            add(None, meta_path)

    items.sort(key=lambda x: (x["ep"] is None, x["ep"] if x["ep"] is not None else 10**9))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--coverage", default=None)
    ap.add_argument("--prefix", default=None)
    args = ap.parse_args()

    d = os.path.expanduser(args.dir)
    items = collect(d, args.prefix)
    if not items:
        raise SystemExit(f"{d} 下没有可识别的 .md 或 _meta.json")

    nums = [x["ep"] for x in items if x["ep"] is not None]
    missing = []
    if nums:
        missing = [n for n in range(min(nums), max(nums) + 1) if n not in nums]

    groups = defaultdict(list)
    for x in items:
        key = norm_title(x["topic"] or x["title"])
        if key:
            groups[key].append(x)
    dup_map = {}
    for arr in groups.values():
        if len(arr) > 1:
            for x in arr[1:]:
                dup_map[id(x)] = arr[0]["ep"]
    for x in items:
        x["is_duplicate"] = id(x) in dup_map
        x["duplicate_of"] = dup_map.get(id(x))

    out = {
        "generated": date.today().isoformat(),
        "dir": os.path.abspath(d),
        "count": len(items),
        "ep_range": [min(nums), max(nums)] if nums else None,
        "missing": missing,
        "items": items,
    }
    out_path = os.path.expanduser(args.out or os.path.join(d, "_manifest.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"写出 {out_path}（{len(items)} 条，集号 {out['ep_range']}，缺号 {missing or '无'}）")

    if args.coverage:
        cov_path = os.path.expanduser(args.coverage)
        write_coverage(cov_path, items, nums, missing, d)
        print(f"写出 {cov_path}")


def write_coverage(path, items, nums, missing, d):
    views = [x["stat"]["view"] for x in items if x["stat"]["view"]]
    durs = [x["duration_sec"] for x in items if x["duration_sec"]]
    years = Counter(x["date"][:4] for x in items if x["date"])
    no_raw = [x["ep"] for x in items if not x["raw"]]
    no_md = [x["ep"] for x in items if not x["md"]]
    asr = [x["ep"] for x in items if x["is_asr"]]
    no_stat = [x["ep"] for x in items if not x["stat"]["view"]]
    dups = [(x["ep"], x["duplicate_of"]) for x in items if x.get("is_duplicate")]
    multi = [x["ep"] for x in items if x.get("is_multipart")]
    dates = [x["date"] for x in items if x["date"]]

    lines = [
        "# 覆盖度报告",
        "",
        f"> 目录：`{os.path.abspath(d)}`",
        f"> 生成日期：{date.today().isoformat()}",
        "",
        "## 总览",
        "",
        f"- 条目：{len(items)}",
        f"- 集号范围：{min(nums) if nums else '—'}–{max(nums) if nums else '—'}",
        f"- 缺号：{missing or '无'}",
        f"- 日期范围：{min(dates) if dates else '—'} ~ {max(dates) if dates else '—'}",
        f"- 总时长：{sum(durs) / 3600:.1f} 小时" if durs else "- 总时长：—",
        f"- 无原文：{no_raw or '无'}",
        f"- 无蒸馏稿：{no_md or '无'}",
        f"- ASR 转写：{asr or '无'}",
        f"- 多分P（播放统计为整条视频）：{multi or '无'}",
        f"- 无播放数据：{no_stat or '无'}",
        f"- 重复标题：{dups or '无'}",
        "",
        "## 年份分布",
        "",
        "| 年份 | 条数 |",
        "| --- | --- |",
    ]
    for y in sorted(years):
        lines.append(f"| {y} | {years[y]} |")
    if views:
        lines += [
            "",
            "## 播放分布",
            "",
            f"- 总播放：{sum(views):,}",
            f"- 中位数：{int(statistics.median(views)):,}",
            f"- 最高：{max(views):,}",
            f"- 最低：{min(views):,}",
            "",
            "## 播放前 10",
            "",
            "| 集号 | 标题 | 播放 | 点赞 | 收藏 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for x in sorted(items, key=lambda y: y["stat"]["view"], reverse=True)[:10]:
            t = (x["title"] or x["topic"]).replace("|", "／")
            lines.append(f"| {x['ep']} | {t} | {x['stat']['view']:,} | "
                         f"{x['stat']['like']:,} | {x['stat']['favorite']:,} |")
    lines += [
        "",
        "## 怎么用",
        "",
        "- 缺号 ≠ 没发生，只表示这套合集里没有。",
        "- 年份分布断档的地方，就是外部补充的优先级。",
        "- 无蒸馏稿的集，先跑 `--distill-only` 再进入分片调研。",
        "- 无播放数据的集，跑 `enrich.py` 补一次 view 接口的数据。",
        "- 重复标题要人工确认是同一内容还是不同集同题。",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
