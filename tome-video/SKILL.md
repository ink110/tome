---
name: tome-video
description: tome 采集环（视频→图文笔记）。给小红书视频链接、微信视频号 sph 链接或本地视频文件，自动下载 → whisper 口播转写（带时间戳）→ 场景切换抽帧 → 本地 qwen3-vl 逐帧提取画面文字 → 按时间戳把截图和口播对齐 → 组装成 IXDC 风格图文笔记存入 Obsidian。当用户给 xiaohongshu.com/小红书、weixin.qq.com/sph、channels.weixin.qq.com 链接，或说「这个视频转成笔记」「视频转图文」「截图配口播」「视频存进知识库」时使用。
---

# tome-video · 视频 → Obsidian 图文笔记

核心思路：**机器活全在本地脚本里**（下载/转写/抽帧/视觉描述），**组装和提炼由 Claude 做**。产物对齐 IXDC2026 笔记风格：每张关键帧配一句结合口播的图注。

## 输入路由（先看链接类型）

| 输入 | 走法 |
|---|---|
| `xiaohongshu.com` / `xhslink.com` | 全自动：`video_note.py --url <链接>`（yt-dlp 直接下载） |
| `weixin.qq.com/sph/…` 视频号 | **半自动**：视频流拿不到（匿名接口只回封面+元数据）。先 `node sph_meta.js <链接> meta.json` 抓元数据，然后请 ink 投送 mp4（见下），再 `--file 投送的mp4 --sph-meta meta.json` |
| 本地 mp4/mov | `video_note.py --file <路径> --title <标题>` |

**视频号投送方式（告诉 ink 二选一）：**
1. 电脑微信里打开该视频号视频 → 用 res-downloader 类抓包工具保存 mp4（或未装则：手机微信里把视频「保存到相册」再 AirDrop 到 Mac）；
2. 文件放到 `~/Downloads/`，把路径给我即可继续，后续全自动。

## 流水线（一条命令全跑，也可分阶段续跑）

```bash
python3 ~/.claude/skills/tome-video/scripts/video_note.py \
    --url <小红书链接>                # 或 --file <本地视频> [--sph-meta meta.json]
    [--title 标题] [--topic 不用管]    # topic 由 Claude 在组装阶段判断
```

五个阶段：`download → transcribe → frames → vision → align`，产物在工作目录 `~/.cache/tome-video/<slug>-<hash>/work.json`。
断点续跑：`--from-stage vision --workdir <目录>`（同一 workdir，自动读已有状态）。

- **transcribe**：mlx_whisper + large-v3-turbo，中文，带时间戳。19 分钟视频约 3–5 分钟。
- **frames**：ffmpeg 场景切换检测（阈值 0.35，自动降档重试；纯口播视频兜底均匀采样），帧间隔 ≥2s，上限 40 帧（`--max-frames` 可调）。
- **vision**：本地 `qwen3-vl:32b-instruct` 逐帧描述，文字板/幻灯片**逐字提取**，约 10s/帧。
- **align**：每帧取 `[t-8s, t+4s]` 窗口的口播文本，供 Claude 写图注。

## 组装笔记（Claude 做，不写脚本）

读完 `work.json` 后：

1. **判断专题**：按内容主题对照 vault `topics/` 已有专题，复用优先；不明确就用 `自媒体收集`。
2. **筛帧**：40 帧里通常有重复/纯人像过渡帧。按「信息密度」选 10–25 张：文字板、图表、界面演示必留；纯人脸说话帧只留转折点。留下的帧复制到笔记附件目录。
3. **写笔记**，结构（对标 IXDC 风格，短视频精简版）：

```markdown
---
title / source / author / published / created / platform / stats(可选)
tags: [clippings, video]
---
# 标题
> 平台 · 作者 · 发布日期 · [原始链接](url)
> 一句话总结（20–40 字）

## 核心要点
3–5 条，每条一句话。

## 图文全程
![[attachments/<slug>/f0007.jpg]]
**图注：不是描述图片，而是把画面文字和当时口播合成一句有信息增量的话（IXDC 风格）。**

…（按时间序）

## 口播全文
完整转写文本（从 transcript.txt 读）。

## 我的笔记（可选）
若内容与已有专题笔记有关联，列 2–3 条 wikilink。
```

4. **落盘**：
   - 笔记 → `topics/<topic>/30-视频/<标题>.md`
   - 帧图 → `topics/<topic>/30-视频/attachments/<slug>/fNNNN.jpg`（只复制入选帧）
   - 更新 `topics/<topic>/index.md` 的 `<!-- tome-video:start/end -->` 区块（没有就建）。
5. **汇报**：笔记路径、入选帧数、口播字数、耗时。若视频号是手动投送，提醒 ink 原始 mp4 还在 Downloads，可自行清理。

## 质量要求

- 图注**必须有信息增量**：画面是「PARA 四分类图」口播在讲适用场景，图注就写「PARA 把资料分成项目/领域/资源/存档四类，适合目标导向的人」，不要写「图中展示了 PARA 方法」。
- 口播全文逐字保留（whisper 原文，不改写），提炼只放在核心要点。
- 视频号的 `stats`（点赞/收藏/转发）写进 frontmatter——这是选题素材数据，别丢。

## 依赖

| 依赖 | 用途 | 必需 |
| --- | --- | --- |
| `mlx_whisper` + `whisper-large-v3-turbo` | 中文口播转写 | 是 |
| `ffmpeg` | 抽帧、探测时长 | 是 |
| `yt-dlp` | 下载小红书等站点视频 | 是（视频号投送可不用） |
| Ollama + `qwen3-vl:32b-instruct` | 逐帧画面描述 / 文字提取 | 是 |
| Node + `puppeteer-core` | 视频号元数据（`scripts/` 下 `npm install`） | 仅视频号 |

脚本里的工具路径是绝对路径，换机器时要改；完整清单见 [references/pipeline.md](references/pipeline.md)。

## 边界（诚实）

- **视频号视频流无法匿名下载**（2026-11 实测：`get_feed_info` 只回封面，页面桌面端只给「扫码观看」）。只能抓元数据，视频靠手动投送。
- 小红书需链接里带 `xsec_token`（分享链接都带）；过期的 token 会 403，让 ink 重新分享。
- **小红书图文笔记（无视频）不在本技能范围**：yt-dlp 会报 `No video formats found`。这类内容需要「页面取图 → 视觉模型 OCR → 图文笔记」的单独流程。
- whisper 对背景音乐重的视频转写质量下降；纯 BGM 无人声的视频会在 transcribe 阶段退出。
- qwen3-vl 逐字提取偶有错字，图注以口播为准、画面文字为辅。
- yt-dlp 目前装在 `~/.whisper-venv`（py3.9，2025.10.14 是最后兼容版）；将来系统 python 升级后建议重装到新版环境。

## 参考（需要时加载）

- [references/pipeline.md](references/pipeline.md) — 脚本参数全集、work.json 结构、各阶段耗时实测、常见报错
- [references/sph-capture.md](references/sph-capture.md) — 视频号为什么下不了、sph_meta.js 原理、手动投送的两种方式
