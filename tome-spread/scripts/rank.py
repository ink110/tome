#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按传播数据给合集内容排名，产出候选案例清单。

用法:
  python3 rank.py --manifest "/path/_manifest.json" --top 20 \
      --out "/path/20-输出/_候选传播案例.md"

说明:
  - 只在同一合集内比较；跨合集请分开跑。
  - 分数只是排序工具，不是「内容好坏」的结论。
  - 最终案例要按 references/selection.md 做 4 选 3 的定性筛选。
  - 平台不公开互动数据（微信等）时脚本会直接退出；改用代理指标。
"""
import argparse
import json
import math
import os
import statistics
from datetime import date, datetime


def num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def fmt_int(x):
    return f"{int(x):,}" if x else "0"


def pct(x):
    return f"{x * 100:.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--top", type=int, default=20, help="输出前 N 条；0=全部")
    ap.add_argument("--out", default=None, help="输出 Markdown 路径")
    ap.add_argument("--json", default=None, help="同时输出候选 JSON")
    ap.add_argument("--min-view", type=int, default=0)
    ap.add_argument("--since", default=None, help="只看 YYYY-MM-DD 之后发布的内容")
    ap.add_argument("--include-missing", action="store_true", help="包含没有播放数据的条目")
    ap.add_argument("--include-multipart", action="store_true",
                    help="包含多分P；注意 view 只给整条视频的统计，不是单P")
    args = ap.parse_args()

    with open(os.path.expanduser(args.manifest), encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", data if isinstance(data, list) else [])
    if not items:
        raise SystemExit("manifest 里没有 items")
    if not any(isinstance(it.get("stat"), dict) and it["stat"] for it in items):
        raise SystemExit(
            "manifest 没有 stat：该平台不公开阅读/播放/互动数据（如微信公众号）。"
            "不要估算，改用代理指标，见 references/selection.md 第八节。")

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()

    rows = []
    for it in items:
        if it.get("is_multipart") and not args.include_multipart:
            continue
        st = it.get("stat") or {}
        view = num(st.get("view"))
        if view < args.min_view:
            continue
        if not view and not args.include_missing:
            continue
        d = it.get("date") or ""
        if since and d:
            try:
                if datetime.strptime(d[:10], "%Y-%m-%d").date() < since:
                    continue
            except ValueError:
                pass
        like, coin, fav = num(st.get("like")), num(st.get("coin")), num(st.get("favorite"))
        share, reply = num(st.get("share")), num(st.get("reply"))
        interaction = (like + coin + fav + share + reply) / view if view else 0
        fav_rate = fav / view if view else 0
        coin_rate = coin / view if view else 0
        rows.append({
            "ep": it.get("ep"),
            "bvid": it.get("bvid", ""),
            "title": it.get("title", ""),
            "date": d[:10] if d else "",
            "duration_sec": it.get("duration_sec", 0),
            "view": view,
            "like": like,
            "coin": coin,
            "favorite": fav,
            "share": share,
            "reply": reply,
            "interaction_rate": interaction,
            "favorite_rate": fav_rate,
            "coin_rate": coin_rate,
            "source_url": it.get("source_url") or (
                f"https://www.bilibili.com/video/{it.get('bvid', '')}/" if it.get("bvid") else ""),
            "is_duplicate": bool(it.get("is_duplicate")),
        })

    if not rows:
        raise SystemExit("没有可排名的条目（检查 stat.view / --include-missing）")

    def zs(key):
        vals = [r[key] for r in rows]
        mean = statistics.mean(vals)
        sd = statistics.pstdev(vals)
        if sd == 0:
            return {id(r): 0.0 for r in rows}
        return {id(r): (r[key] - mean) / sd for r in rows}

    zv, zi, zf, zc = zs("view"), zs("interaction_rate"), zs("favorite_rate"), zs("coin_rate")
    for r in rows:
        r["score"] = (0.40 * zv[id(r)] + 0.30 * zi[id(r)]
                      + 0.20 * zf[id(r)] + 0.10 * zc[id(r)])

    rows.sort(key=lambda r: r["score"], reverse=True)
    top = rows if args.top == 0 else rows[:args.top]

    med_view = statistics.median(r["view"] for r in rows)
    med_int = statistics.median(r["interaction_rate"] for r in rows)
    p80 = sorted(r["view"] for r in rows)[max(0, int(len(rows) * 0.8) - 1)]

    lines = [
        "# 候选传播案例",
        "",
        f"> 数据截至：{date.today().isoformat()}",
        f"> 样本：{len(rows)} 条｜播放中位数：{fmt_int(med_view)}｜"
        f"互动率中位数：{pct(med_int)}｜播放前 20% 门槛：{fmt_int(p80)}",
        "",
        "| 排名 | 集号 | 标题 | 日期 | 播放 | 互动率 | 收藏率 | 投币率 | 分数 | 链接 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(top, 1):
        title = r["title"].replace("|", "／")
        lines.append(
            f"| {i} | {r['ep']} | {title} | {r['date']} | {fmt_int(r['view'])} | "
            f"{pct(r['interaction_rate'])} | {pct(r['favorite_rate'])} | "
            f"{pct(r['coin_rate'])} | {r['score']:.2f} | [BV]({r['source_url']}) |"
        )

    lines += [
        "",
        "## 筛选提示",
        "",
        "- 先看播放分位 ≥ 80% 的条目；再看互动率是否 ≥ 中位数 1.5 倍。",
        "- 站外证据（媒体报道 / 二创 / 跨平台引用）是「影响力大」最硬的证据。",
        "- 最终只留 8–12 条；宁少勿多。",
        "- 数据是快照，输出时保留「数据截至」日期。",
        "- 多分P默认排除：view 只给整条视频的统计，不能拆到单P；要比较请用独立 BV 的合集。",
    ]
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                                   "_候选传播案例.md")
    os.makedirs(os.path.dirname(os.path.expanduser(out)) or ".", exist_ok=True)
    with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"写出 {out}（{len(top)} 条）")

    if args.json:
        with open(os.path.expanduser(args.json), "w", encoding="utf-8") as f:
            json.dump({"generated": date.today().isoformat(), "items": top},
                      f, ensure_ascii=False, indent=2)
        print(f"写出 {args.json}")


if __name__ == "__main__":
    main()
