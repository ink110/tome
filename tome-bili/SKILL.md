---
name: tome-bili
description: tome 采集环（B站）。从 B站提取口播稿、字幕和传播数据，支持单视频、多分P、合集批量，产出「结构化文章 + 原文 + 元信息清单」。当用户给 B站链接/BV 号，或说「提取口播稿」「转文字稿」「转成 MD」「爬字幕」「下载字幕」「爬合集」「批量提取」「抓播放/点赞/收藏」时使用。也用于 tome 公开资料成书链路的第一阶段采集。
---

# tome-bili · B站采集

把 B站视频的口播、字幕和传播数据抓下来，变成可检索的 Markdown 和可计算的清单。

**三种形态，先分清：**

| 形态 | 用什么 | 例子 |
| --- | --- | --- |
| 单个视频 | `bilibili_to_md.py` | 一个 BV 号 |
| 同一个视频的多个分P | `batch_crawl.py` | 链接里的 `?p=N` |
| 合集里的 N 个独立视频 | `crawl_collection.py` | 人物演讲合集、系列课 |

## 快速上手

### 单个视频

```bash
python3 ~/.claude/skills/tome-bili/bilibili_to_md.py \
  "https://www.bilibili.com/video/BVxxx/?p=2" \
  --out "<项目目录>/00-收集"
```

### 合集：先落原文，再蒸馏（推荐）

```bash
# 1) 全量抓字幕 + 元信息，不调模型（快）
python3 ~/.claude/skills/tome-bili/crawl_collection.py "<合集里任意 BV>" \
  --out "<项目>/00-收集" --prefix "<前缀>" --raw-only

# 2) 生成清单 + 覆盖度
python3 ~/.claude/skills/tome-bili/manifest.py \
  --dir "<项目>/00-收集" \
  --out "<项目>/00-收集/_manifest.json" \
  --coverage "<项目>/00-收集/_coverage.md"

# 3) 蒸馏（模型档位决定这一步怎么跑）
#    claude 档位：Claude 自己读 _raw.txt 逐集生成结构化稿，不用跑命令
#    openai / ollama 档位：脚本批量蒸馏
python3 ~/.claude/skills/tome-bili/crawl_collection.py "<BV>" \
  --out "<项目>/00-收集" --prefix "<前缀>" \
  --distill-only --start-ep 1 --end-ep 20
```

### 多分P

```bash
python3 ~/.claude/skills/tome-bili/batch_crawl.py BVxxxx \
  --out "<项目>/00-收集" --prefix "<前缀>" --start 1 --end 20
```

### 老项目补播放数据

```bash
python3 ~/.claude/skills/tome-bili/enrich.py --dir "<项目>/00-收集" --sleep 0.5
# 然后重跑 manifest.py
```

已有 `_meta.json` 但 `stat` 为空的，或只有 `.md` 的老文件，都可以用这条补齐。需要有效 cookie。

参数速查、蒸馏稿结构见 [references/flow.md](references/flow.md)。

## 前置检查

```bash
# cookie 有效才继续（isLogin: True）
curl -s -b ~/.bilibili-cookies.txt -A "Mozilla/5.0" \
  "https://api.bilibili.com/x/web-interface/nav"
```

- cookie 文件路径由配置决定（默认 `~/.bilibili-cookies.txt`），Netscape 格式。
- 必须用「Get cookies.txt LOCALLY」导出**完整** cookie：SESSDATA、DedeUserID、sid、bili_ticket 都要；只有 SESSDATA 会返回 -101。
- 失效时按 `references/flow.md` 重新获取；不要把 cookie 写进代码或笔记正文。

## 关键原则

- **案例保留叙事细节**（场景 / 过程 / 因果链 / 故事感），不要压缩成结论式 bullet。
- 去口语赘词（嗯 / 哈 / 其实 / 就是说 / 是吧），但保留原意和论证完整。
- AI 字幕的人名、品牌名做常识性校正（同音字、错别字）。
- 结构化文章是「转译」，不是「压缩」：核心观点、案例、金句、论证一个都不能丢。
- 原文逐字追加，不让模型复述；模型只生成到「学习记录」。

## 模型档位

由 `~/.config/tome/config.json` 的 `models.provider` 决定，配置见 `tome/references/setup.md`：

- `claude`（默认）：脚本只落 `_raw.txt`，结构化由 Claude Code 逐集完成，不调用外部模型。
- `openai`：脚本调用任意 OpenAI 兼容接口批量蒸馏，适合几十集以上。
- `ollama`：本地批量蒸馏，内容不出本机。

长视频用流式调用；多集串行，避免并发拖慢。命令里不用硬写模型名，`--model` 只在临时覆盖时用。

## 平台范围

目前只实现 B站。清单格式是平台中立的（`platform` + `source_url` + `stat`），以后加 YouTube 可以用 `yt-dlp` 适配；视频号、小红书、X 的抓取维护成本高，等真有需求再做，不要现在堆一堆脆弱的爬虫。

## Where to go next

| 场景 | 读 |
| --- | --- |
| 脚本坏了、要手写/改底层逻辑 | `references/flow.md`（提取流程 + wbi 签名 + cookie） |
| 视频无 AI 字幕 | `references/asr.md`（faster-whisper 转写） |
| 遇到报错 / 踩坑 | `references/pitfalls.md` |
