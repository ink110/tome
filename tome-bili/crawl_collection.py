#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl_collection.py — 批量爬取 B站「合集」里每一集的口播稿 → 结构化 Markdown

与 batch_crawl.py 的区别：
  batch_crawl.py       爬「同一个视频的多个分P」（URL 里的 ?p=N）
  crawl_collection.py  爬「合集里的 N 个独立视频」（每个视频 1 个 P，各有自己的 BV）

合集形态：同一合集内可能有多个独立视频，标题带集号（可能有跳号），
每个视频各自一个 BV。

用法:
  # 给任意一个属于该合集的 BV，脚本自动找出整个合集
  python3 crawl_collection.py BV1xxxxxxxxx --out "<项目目录>/00-收集" --start-ep 81 --end-ep 145

  # 或先只看清单不爬
  python3 crawl_collection.py BV1xxxxxxxxx --list

  # 只爬某些集
  python3 crawl_collection.py BV1xxxxxxxxx --out <目录> --only 90,91,95

特性:
  - 断点续爬：目标目录已存在同编号文件就跳过，中断后重跑即可
  - 单集失败不影响整批（catch SystemExit / Exception）
  - 模型流式调用，避免长口播稿生成超时
  - 原文不叫模型复述：模型只生成到「学习记录」，逐句原文由程序直接追加

依赖: 见 SKILL.md（模型档位已配置 + B站 cookie，路径由配置决定，默认 ~/.bilibili-cookies.txt）
"""
import argparse
import glob
import os
import re
import sys
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

import bilibili_fetch as bf                      # noqa: E402
from bilibili_to_md import crawl, build_user_message   # noqa: E402
from batch_crawl import (                        # noqa: E402
    SYSTEM_PROMPT,
    call_ollama_stream,
    strip_transcript_section,
)

DEFAULT_PREFIX = ""


def fetch_episodes(bvid):
    """给定合集里任意一个 BV，返回 [(集号, bvid, 标题), ...]（按集号升序）。"""
    cookie = bf.load_cookie_header()
    if not cookie:
        sys.exit("[错误] 读不到 cookie，见 SKILL.md")
    view = bf.get_view(bvid, cookie)
    if not view:
        sys.exit("[错误] 拿不到视频信息（cookie 可能已失效）")

    season = view.get("ugc_season")
    if not season:
        sys.exit("[错误] 该视频不属于任何合集。合集型请用本脚本，多P型请用 batch_crawl.py")

    print(f"合集：{season.get('title')}（id={season.get('id')}，共 {season.get('ep_count')} 个视频）")
    eps = []
    for sec in season.get("sections", []):
        for e in sec.get("episodes", []):
            title = e.get("title", "")
            m = re.search(r"第\s*(\d+)\s*集", title)
            if not m:
                continue
            eps.append((int(m.group(1)), e["bvid"], title))
    eps.sort()
    return eps


def clean_topic(title, ep):
    """「（完整版）<系列名>第81集——主题」→「主题」"""
    t = re.sub(r"^[（(]\s*完整版\s*[)）]\s*", "", title).strip()
    m = re.search(r"第\s*\d+\s*集\s*[—\-–－]*\s*(.*)$", t)
    topic = (m.group(1) if m else t).strip(" 　—－-")
    topic = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", topic).strip(" _")
    return topic[:80] or f"第{ep}集"


def already_done(out_dir, prefix, ep):
    return bool(glob.glob(os.path.join(out_dir, f"{prefix}{ep:03d}：*.md")))


def raw_paths(out_dir, prefix, ep):
    """返回该集已落盘的 (_raw.txt, _meta.json)，没有则 (None, None)。"""
    r = glob.glob(os.path.join(out_dir, f"{prefix}{ep:03d}：*_raw.txt"))
    j = glob.glob(os.path.join(out_dir, f"{prefix}{ep:03d}：*_meta.json"))
    return (r[0] if r else None), (j[0] if j else None)


def write_raw(out_dir, prefix, ep, meta, lines):
    """只落原文 + 元信息，不调模型。文件名与项目约定一致。"""
    topic = clean_topic(meta.get("title") or "", ep)
    base = os.path.join(out_dir, f"{prefix}{ep:03d}：{topic}")
    with open(base + "_raw.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    import json as _json
    with open(base + "_meta.json", "w", encoding="utf-8") as f:
        _json.dump({**meta, "episode": ep}, f, ensure_ascii=False, indent=2)
    return base


def distill_episode(out_dir, prefix, ep, model, meta, lines):
    """调本地模型把 (meta, lines) 写成结构化 md（不含原文），返回文件名；失败抛异常。"""
    user = build_user_message(meta, lines)
    md = strip_transcript_section(call_ollama_stream(model, SYSTEM_PROMPT, user))
    if len(md.strip()) < 200:
        raise RuntimeError("模型输出过短，疑似异常")
    topic = clean_topic(meta.get("title") or "", ep)
    fname = f"{prefix}{ep:03d}：{topic}.md"
    frontmatter = (
        "---\n"
        f"title: {prefix}{ep:03d}：{topic}\n"
        f"up: {meta.get('owner','')}\n"
        f"source: https://www.bilibili.com/video/{meta.get('bvid','')}/\n"
        f"episode: {ep}\n"
        f"date: {date.today().isoformat()}\n"
        "tags: [B站口播]\n"
        "---\n\n"
    )
    content = (
        frontmatter + md
        + "\n\n---\n## 附：完整口播稿（带时间戳）\n"
        + "\n".join(lines) + "\n"
    )
    with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
        f.write(content)
    return fname


def main():
    p = argparse.ArgumentParser(description="爬取 B站合集里的每一集口播稿 → 结构化 MD")
    p.add_argument("bvid", help="合集里任意一个视频的 BV 号")
    p.add_argument("--out", default=None, help="输出目录（必须给，除非 --list）")
    p.add_argument("--prefix", default=DEFAULT_PREFIX, help="文件名前缀")
    p.add_argument("--start-ep", type=int, default=None, help="起始集号（含）")
    p.add_argument("--end-ep", type=int, default=None, help="结束集号（含）")
    p.add_argument("--only", default=None, help="只爬这些集号，逗号分隔，如 90,91,95")
    p.add_argument("--model", default=None, help="模型名（留空用配置）")
    p.add_argument("--list", action="store_true", help="只打印清单，不爬")
    p.add_argument("--raw-only", action="store_true",
                   help="只抓原文（_raw.txt + _meta.json），不调本地模型。快，几分钟跑完几十集")
    p.add_argument("--distill-only", action="store_true",
                   help="只对已落盘的 _raw.txt 补跑模型蒸馏，不重新爬（配合 --raw-only 使用）")
    args = p.parse_args()

    if tc.provider() == "claude" and not args.raw_only and not args.list:
        print(tc.CLAUDE_HINT)
        print("先用 --raw-only 落原文，再由 Claude 逐集生成结构化稿；"
              "需要脚本批量蒸馏就把 models.provider 切成 openai 或 ollama。")
        sys.exit(5)

    m = re.search(r"(BV[0-9A-Za-z]{10})", args.bvid)
    bvid = m.group(1) if m else args.bvid

    eps = fetch_episodes(bvid)
    print(f"解析出 {len(eps)} 集，编号 {eps[0][0]}–{eps[-1][0]}\n")

    nums = [e[0] for e in eps]
    gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
    if gaps:
        print(f"⚠️ 合集内缺号：{gaps}（这些集不在合集里）\n")

    if args.list:
        for ep, bv, title in eps:
            print(f"  {ep:>3}  {bv}  {clean_topic(title, ep)}")
        return

    if not args.out:
        sys.exit("[错误] 需要 --out 指定输出目录")

    only = set()
    if args.only:
        only = {int(x) for x in re.findall(r"\d+", args.only)}

    out_dir = os.path.expanduser(args.out)
    os.makedirs(out_dir, exist_ok=True)

    todo = []
    for ep, bv, title in eps:
        if only and ep not in only:
            continue
        if args.start_ep is not None and ep < args.start_ep:
            continue
        if args.end_ep is not None and ep > args.end_ep:
            continue
        todo.append((ep, bv, title))

    print(f"本次计划处理 {len(todo)} 集\n")

    done = skipped = failed = 0
    for i, (ep, bv, title) in enumerate(todo, 1):
        tag = f"[{i}/{len(todo)}] ep{ep:03d}"
        raw_file, meta_file = raw_paths(out_dir, args.prefix, ep)

        if args.distill_only:
            # 只补蒸馏：读已落盘的原文
            if not raw_file or not meta_file:
                print(f"{tag} 没有 _raw.txt，跳过", flush=True)
                skipped += 1
                continue
            if already_done(out_dir, args.prefix, ep):
                print(f"{tag} 已有成品 md，跳过", flush=True)
                skipped += 1
                continue
            import json as _json
            meta = _json.load(open(meta_file, encoding="utf-8"))
            lines = [l for l in open(raw_file, encoding="utf-8").read().split("\n") if l.strip()]
            print(f"{tag} 蒸馏中（{len(lines)} 条）…", flush=True)
            try:
                fname = distill_episode(out_dir, args.prefix, ep, args.model, meta, lines)
            except tc.ClaudeMode as exc:
                print(str(exc))
                sys.exit(5)
            except tc.ModelError as exc:
                print(f"[模型错误] {exc}")
                sys.exit(2)
            except Exception as e:
                print(f"{tag} 失败: {e}，继续", flush=True)
                failed += 1
                continue
            print(f"{tag} ✅ {fname}", flush=True)
            done += 1
            continue

        if already_done(out_dir, args.prefix, ep):
            print(f"{tag} 已存在，跳过", flush=True)
            skipped += 1
            continue

        # 原文已在盘上就直接复用（raw-only 之后跑全量时省一次网络请求）
        if raw_file and meta_file:
            import json as _json
            meta = _json.load(open(meta_file, encoding="utf-8"))
            lines = [l for l in open(raw_file, encoding="utf-8").read().split("\n") if l.strip()]
            print(f"{tag} 复用已落盘原文（{len(lines)} 条）", flush=True)
        else:
            try:
                meta, lines = crawl(bv, 1)
            except SystemExit as e:
                print(f"{tag} 爬取失败（exit {e.code}），继续", flush=True)
                failed += 1
                continue
            except Exception as e:
                print(f"{tag} 异常 {e}，继续", flush=True)
                failed += 1
                continue
            print(f"{tag} 爬成功（{len(lines)} 条字幕）", flush=True)
            try:
                write_raw(out_dir, args.prefix, ep, meta, lines)
            except Exception as e:
                print(f"{tag} 原文落盘失败: {e}，继续", flush=True)

        if args.raw_only:
            done += 1
            continue

        print(f"{tag} 模型结构化中…", flush=True)
        try:
            fname = distill_episode(out_dir, args.prefix, ep, args.model, meta, lines)
        except tc.ClaudeMode as exc:
            print(str(exc))
            sys.exit(5)
        except tc.ModelError as exc:
            print(f"[模型错误] {exc}")
            sys.exit(2)
        except Exception as e:
            print(f"{tag} 模型生成失败: {e}，继续", flush=True)
            failed += 1
            continue
        print(f"{tag} ✅ {fname}", flush=True)
        done += 1

    print(f"\n完成：成功 {done}，失败 {failed}，跳过 {skipped}（计划 {len(todo)} 集）", flush=True)
    if failed:
        print("失败的重跑一次即可（已成功的会自动跳过）。", flush=True)


if __name__ == "__main__":
    main()
