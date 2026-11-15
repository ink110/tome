#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_crawl.py — 批量爬取 B站多P系列口播稿 → 结构化 Markdown 写入 Obsidian

相比 bilibili_to_md.py 的差异（针对长文/多P批量）：
1. 用 stream=True 流式调用模型（避免长文生成超时）
2. 原文不叫模型复述：模型只生成到「学习记录」，逐句原文由程序直接追加
3. 按集数序号 + 主题命名（零填充，保证 Obsidian 升序排序）
4. 串行 + 断点续爬（已存在文件自动跳过）

用法:
  python3 batch_crawl.py BV1xxxxxxxxx --out <目录> [--prefix <系列名>]
      [--start 1] [--end 80] [--skip 1,2,3,4,6] [--model <模型名>]
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
from bilibili_to_md import crawl, build_user_message
SYSTEM_PROMPT = """你是内容加工助手。我给你视频元信息和原始口播稿，你把它们整理成结构化 Markdown 文档。

输出结构（严格遵循，从 # 标题 到 ## 学习记录 为止，到此结束）：
# {标题}
> 一句话摘要

## 核心观点速览        ← 30秒抓住要点（3-5条）
## 分节正文            ← 按主题切块，案例必须保留完整叙事细节
## 案例速查表          ← 表格：案例 | 一句话故事 | 支撑观点 | 可复用场景
## 金句摘录
## 学习记录            ← 6类：核心观点/论证结构/案例/表达手法/争议点/思维地图

关键原则：
- 案例保留叙事细节（场景/过程/因果链/故事感），不要压缩成结论式 bullet。
- 去口语赘词（嗯/哈/其实/就是说/是吧），但保留原意和论证完整。
- AI 字幕的人名、品牌名做常识性校正（同音字、错别字）。
- 结构化文章是「转译」，不是「压缩」：核心观点、案例、金句、论证一个都不能丢。

符号规范（强制，为手机端阅读设计）：
- 重点/关键概念只用【】，一处最多一个，不连排。这是唯一的强调符。
- 逐字引语一律用「」，引语内外不再套其他符号。
- 禁止使用『』。禁止出现（【】）这种括号套强调符的嵌套。
- 禁止在正文里用 **加粗**；** 只允许用在表格表头。
- 标题与小节标题独占一行，内容另起一行。

输出要求：
- 只输出 Markdown，不要额外解释，直接以「# 」开头。
- 不要输出「附：完整口播稿」章节，原文会由程序自动追加。
"""


def call_ollama_stream(model, system, user, timeout=900):
    return tc.chat(system, user, model=model, timeout=timeout,
                   temperature=0.3, stream=True, num_ctx=32768)


def strip_transcript_section(md):
    # 去掉模型可能生成的「附：完整口播稿」章节，避免与程序追加的原文重复
    return re.split(r"\n---\s*\n##\s*附：完整口播稿", md)[0].rstrip()


def build_filename(index, title):
    # title 形如「<系列名>005：某主题」
    # 取第一个「：」之后的部分作为主题（主题内可能还含「：」，如「番外：xxx」）
    if "：" in title:
        topic = title.split("：", 1)[1].strip()
    else:
        topic = title
    topic = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", topic).strip(" _")
    topic = topic[:80] or f"第{index}集"
    return f"{PREFIX}{index:03d}：{topic}.md"


def main():
    global PREFIX
    argv = sys.argv[1:]

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            val = argv[i + 1]
            argv[:] = argv[:i] + argv[i + 2:]
            return val
        return default

    out_dir = opt("--out")
    model = opt("--model")
    PREFIX = opt("--prefix") or ""
    start = int(opt("--start") or 1)
    end = int(opt("--end") or 80)
    skip = set()
    for s in (opt("--skip") or "").split(","):
        s = s.strip()
        if s.isdigit():
            skip.add(int(s))

    if not argv or not out_dir:
        print("用法: batch_crawl.py <BV> --out <目录> [--start N] [--end N] [--skip a,b,c]")
        sys.exit(1)
    raw = argv[0].strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    bvid = m.group(1) if m else raw

    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    done = failed = skipped = 0
    for page in range(start, end + 1):
        try:
            meta, lines = crawl(bvid, page)
        except SystemExit as e:
            print(f"[skip p={page}] 爬取失败（exit {e.code}），继续下一集", flush=True)
            failed += 1
            continue
        except Exception as e:
            print(f"[skip p={page}] 异常 {e}，继续下一集", flush=True)
            failed += 1
            continue

        title = meta["title"]
        fname = build_filename(page, title)
        path = os.path.join(out_dir, fname)

        if page in skip or os.path.exists(path):
            print(f"[skip p={page}] 已存在或跳过: {fname}", flush=True)
            skipped += 1
            continue

        print(f"[p={page}] 爬取成功，{len(lines)} 条字幕 → 结构化中…", flush=True)
        try:
            user = build_user_message(meta, lines)
            md = strip_transcript_section(call_ollama_stream(model, SYSTEM_PROMPT, user))
            if len(md.strip()) < 200:
                raise RuntimeError("模型输出过短，疑似异常")
        except (tc.ClaudeMode, tc.ModelError):
            raise
        except Exception as e:
            print(f"[p={page}] 模型生成失败: {e}，继续下一集", flush=True)
            failed += 1
            continue

        frontmatter = (
            "---\n"
            f"title: {title}\n"
            f"up: {meta['owner']}\n"
            f"source: https://www.bilibili.com/video/{bvid}/?p={page}\n"
            f"date: {date.today().isoformat()}\n"
            "tags: [B站口播]\n"
            "---\n\n"
        )
        transcript = "\n".join(lines)
        content = frontmatter + md + "\n\n---\n## 附：完整口播稿（带时间戳）\n" + transcript + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[p={page}] ✅ 已保存: {fname}", flush=True)
        done += 1

    print(f"\n完成：成功 {done}，失败 {failed}，跳过 {skipped}（共 {end - start + 1} 集）", flush=True)


if __name__ == "__main__":
    try:
        main()
    except tc.ClaudeMode as exc:
        print(str(exc))
        sys.exit(5)
    except tc.ModelError as exc:
        print(f"[模型错误] {exc}")
        sys.exit(2)
