---
name: tome-clip
description: tome 采集环（单篇剪藏 → Obsidian 图文笔记）。给一篇公众号文章、小红书图文/视频、微信视频号链接或本地视频：自动识别来源并路由——公众号文章抓正文 + 媒体，小红书图文取原图 + qwen3-vl 逐字 OCR，视频做口播转写 + 稳定帧 + 视觉描述，最后整理成统一的 Obsidian 图文笔记。当用户说「存这一篇」「剪藏」「爬这篇文章」「这个视频转成笔记」「图文笔记存进知识库」「小红书 / 公众号存进 Obsidian」时使用。
---

# tome-clip · 单篇采集 → Obsidian 图文笔记

核心思路：**一个入口，按链接自动分流**。`clip.py` 只做 URL 路由；重活调用现有脚本
（`tome-wechat/wechat_article.py`、`xhs_note.py`、`sph_fetch.py`、`video_note.py`）；筛选、图注和笔记组装由 Claude 做。

```bash
# 单篇统一入口
python3 ~/.claude/skills/tome-clip/scripts/clip.py --url <链接> [--topic 专题] [--dry-run]
python3 ~/.claude/skills/tome-clip/scripts/clip.py --file <本地视频> [--title 标题]

# 只看路由，不执行
python3 ~/.claude/skills/tome-clip/scripts/clip.py --url <链接> --dry-run
```

## 输入路由

| 输入 | 走法 |
|---|---|
| `mp.weixin.qq.com/s/...` 普通文章 | `tome-wechat/wechat_article.py --url`：抓正文 + 图片/GIF/视频，直接写 Obsidian 笔记 |
| 公众号图片消息（`js_content` 为空） | 按 `tome-wechat/references/image-post.md` 的图片 OCR 流程 |
| `xiaohongshu.com` 图文笔记 | `xhs_note.py --url`：移动端 SSR 取元数据 → 原图 → qwen3-vl 逐字 OCR → `note.json` |
| `xiaohongshu.com` 视频笔记 | `xhs_note.py --url` 自动判断并转交 `video_note.py`；也可直接 `video_note.py --url` |
| `xhslink.com` 短链 | 同上，脚本自动跟随跳转；**必须带 `xsec_token`** |
| `weixin.qq.com/sph/…` 视频号 | `sph_fetch.py --url`：抓元数据 → 同源原片（YouTube，标题/时长/日期校验）→ 下载 → 自动跑 `video_note.py`；找不到时打印 Yuanbao / 微信 PC / 手动投送三条兜底 |
| 本地 mp4/mov | `video_note.py --file <路径> --title <标题>` |

小红书链接没有 `xsec_token` 会拿不到数据。让 ink 在 App 里「分享 → 复制链接」，把整段分享内容发过来即可（短链也可以）。

公众号文章由 `wechat_article.py` 直接写笔记和媒体，抓完检查标题、正文、图片/视频引用后汇报即可，不走下面的图文/视频组装流程。

**视频号获取优先级（先跑 `sph_fetch.py`，失败再人工）：**

1. **同源原片（零配置）**：用标题在 YouTube 搜索，核对标题 / 时长 / 发布日期后 `yt-dlp` 下载。
   `--youtube-url` 可登记人工确认的原片，写入 `~/.cache/tome-clip/sph-sources.json`，以后复用。
2. **腾讯元宝官方链路（一次扫码，之后全自动）**：`sph_yuanbao.py --url <sph链接>`。
   首次弹出 Chrome 扫码登录腾讯元宝；之后同一命令自动解析、下载、跑视频流水线。
   依赖 `download-wechat-channels`（默认在 `~/tools/download-wechat-channels`，
   可用 `WECHAT_CHANNELS_SKILL` 指定路径）。
3. **微信 PC + 本地代理（兜底）**：`ltaoo/wx_channels_download` 或
   `ChantillyAn/wechat-video-skill`，电脑微信播放一次，从浏览历史下载并解密。
4. **手动投送**：电脑微信保存 / 手机保存后传回 / 录屏，然后 `sph_fetch.py --file <mp4>`。

注意：匿名分享页只返回封面和元数据，Chrome 和 Eagle 插件都拿不到视频流；不要在浏览器抓包这条路上浪费时间。

```bash
# 视频号：自动找同源原片并跑完整视频流水线
python3 ~/.claude/skills/tome-clip/scripts/sph_fetch.py --url <sph链接> \
    [--proxy http://127.0.0.1:7890]

# 视频号：腾讯元宝官方链路（首次扫码，之后全自动）
python3 ~/.claude/skills/tome-clip/scripts/sph_yuanbao.py --url <sph链接>

# 登记人工确认的原片映射（以后同一链接自动复用）
python3 .../sph_fetch.py --url <sph链接> --youtube-url <YouTube链接>

# 用 Yuanbao / 微信 PC 下载到 ~/Downloads 的最新 mp4
python3 .../sph_fetch.py --url <sph链接> --from-downloads

# 手动投送
python3 .../sph_fetch.py --url <sph链接> --file <mp4>
```

## 流水线（一条命令全跑，也可分阶段续跑）

```bash
# 小红书：自动判断图文 / 视频
python3 ~/.claude/skills/tome-clip/scripts/xhs_note.py --url <链接> \
    [--no-ocr] [--meta-only] [--max-images 20]

# 视频（xhs_note.py 会自动转交；也可单独跑）
python3 ~/.claude/skills/tome-clip/scripts/video_note.py \
    --url <小红书链接>                # 或 --file <本地视频> [--sph-meta meta.json]
    [--title 标题] [--topic 不用管]    # topic 由 Claude 在组装阶段判断
```

**图文笔记**：`fetch → images → ocr`，产物在 `~/.cache/tome-clip/<slug>-<hash>/`：
`note.json`（元数据 + 图片路径 + OCR 结果）、`images/001.jpg…`、`ocr/001.txt…`。
OCR 用本地 `qwen3-vl:32b-instruct` 逐字转写，约 10–20s/张；`--no-ocr` 只取图。

**视频笔记**：`download → transcribe → frames → vision → align`，产物是 `work.json`。
断点续跑：`--from-stage vision --workdir <目录>`（同一 workdir，自动读已有状态）。

- **transcribe**：mlx_whisper + large-v3-turbo，中文，带时间戳。19 分钟视频约 30–60 秒。
- **frames**：1fps 采样 + dHash 稳定帧检测，等幻灯片淡入/动画结束、连续两帧稳定后才取帧；检测失败或不足 4 帧才退回均匀采样。上限 40 帧（`--max-frames` 可调）。
- **transcribe 额外产物**：`transcript.txt`（原始拼接）和 `transcript_paragraphs.txt`（按主题词/转折词切好的自然段）。写笔记时用后者，不要整段粘贴。
- **vision**：本地 `qwen3-vl:32b-instruct` 逐帧描述，文字板/幻灯片**逐字提取**，约 10s/帧。
- **align**：每帧取 `[t-8s, t+4s]` 窗口的口播文本，供 Claude 写图注。

## 组装笔记（Claude 做，不写脚本）

> 公众号文章由 `wechat_article.py` 直接写笔记，跳过本节。本节适用于小红书图文/视频、视频号和本地视频。

先判断素材包类型：有 `work.json` → 视频笔记；有 `note.json` 且 `type != video` → 图文笔记。

1. **判断专题**：按内容主题对照 vault `topics/` 已有专题，复用优先；不明确就用 `自媒体收集`。

### 视频笔记

2. **筛帧（重点）**：先看每帧的 `vl` 描述和口播 `speech`，**只有画面内容与该时间窗口口播能对上、且文字可读的帧才留**。空白帧、淡入/动画中间态、纯人脸过渡帧、与图注主题不一致的帧一律丢掉；同一张幻灯片只留一张。按「信息密度」选 10–25 张。留下的帧复制到笔记附件目录。
   - 每张图注只写**这张图里实际出现的内容**，再用该帧 `speech` 补一句上下文；不要用方法的总体介绍代替画面。图注写完后反向检查：遮住图注，只看图，能认出它讲的是不是同一件事；遮住图，只读图注，能在图里找到对应元素。
   - 候选帧多时，按 `vl` 里的幻灯片标题分组，**同一标题只留最清晰的一张**；标题与口播主题不一致的帧不要用。
3. **写笔记**：

```markdown
---
title / source(canonical) / share_url(可选，带 token) / author / published / created / platform / stats(可选)
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
从 `transcript_paragraphs.txt` 读，按自然段粘贴；每段一个子主题（约 100–300 字）。不要把整段 5,000 字一次性塞进来。ASR 术语错误较多时，可在段首加一句「ASR 原文，未校对，术语以图注/要点为准」。

## 我的笔记（可选）
若内容与已有专题笔记有关联，列 2–3 条 wikilink。
```

### 图文笔记

2. **保留全部图片**：按 `note.json.images` 顺序，通常 5–18 张，不要筛掉信息卡；Live Photo 只用静态图。
3. **写图注**：先读该图 `ocr/00X.txt`，压成一句有信息增量的话；OCR 原文放在图注下，逐字保留层级，看不清的 `[?]` 不要改写。
4. **写笔记**：

```markdown
---
title / source(canonical) / share_url(可选，带 token) / author / published / created / platform / stats / tags
tags: [clippings, xhs, image]
---
# 标题
> 小红书 · 作者 · 发布日期 · [原始链接](url)
> 一句话总结（20–40 字，来自文案 + 图片内容）

## 核心要点
3–5 条，每条一句话。

## 图文全程
![[attachments/<slug>/001.jpg]]
**图注：一句有信息增量的话。**

> OCR 原文（逐字，保留标题/列表/表格层级）

…（按原图顺序）

## 原文文案
`note.json.desc` 原文，保留换行和 emoji。

## 我的笔记（可选）
若内容与已有专题笔记有关联，列 2–3 条 wikilink。
```

### 落盘与汇报

5. **落盘**：
   - 视频笔记 → `topics/<topic>/30-视频/<标题>.md`；帧图 → `topics/<topic>/30-视频/attachments/<slug>/fNNNN.jpg`（只复制入选帧）
   - 图文笔记 → `topics/<topic>/30-图文/<标题>.md`；原图 → `topics/<topic>/30-图文/attachments/<slug>/00X.jpg`
   - 更新 `topics/<topic>/index.md` 的 `<!-- tome-clip:start/end -->` 区块（没有就建，视频/图文分小节）。
6. **汇报**：笔记路径、图片/帧数、OCR 字数或口播字数、耗时。若视频号是手动投送，提醒 ink 原始 mp4 还在 Downloads，可自行清理。

## 质量要求

- 图注**必须和图对得上**：先描述这张图实际出现的元素（标题、四层文件夹、表格字段、按钮），再补该帧口播的上下文；不要把方法总览写成图注。自检：只看图能不能复述图注，只看图注能不能在图里找到对应元素。
- 图注**必须有信息增量**：画面是「PARA 四分类图」口播在讲适用场景，图注就写「PARA 把资料分成项目/领域/资源/存档四类，适合目标导向的人」，不要写「图中展示了 PARA 方法」。
- 口播全文逐字保留（whisper 原文，不改写），OCR 原文逐字保留（不改写、不补写），提炼只放在核心要点。
- 图文笔记按原图顺序，不要漏图；标题里的数字、榜单、对比项要对图核一遍。
- 视频号 / 小红书的 `stats`（点赞/收藏/评论/转发）写进 frontmatter——这是选题素材数据，别丢。
- `source` 存不带 `xsec_token` 的 canonical 链接（长期可溯源），带 token 的分享链接另存 `share_url`；token 过期不影响笔记本身。

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
- 小红书链接必须带 `xsec_token`（App 分享链接都带）；过期后页面 `noteDetailMap` 为空，表现为「没拿到笔记数据」，让 ink 重新分享。
- 小红书服务端只对**移动端 UA** 渲染笔记数据；桌面 UA 会落到登录墙。`xhs_note.py` 已固定用 iPhone UA。
- 图文笔记没有口播，信息全在图里；OCR 错字用 `[?]` 标出，不要脑补。重要数字交付时提醒 ink 对图核。
- Live Photo 只取静态图，不处理内嵌短视频。
- whisper 对背景音乐重的视频转写质量下降；纯 BGM 无人声的视频会在 transcribe 阶段退出。
- qwen3-vl 逐字提取偶有错字；视频图注以口播为准、画面文字为辅。
- yt-dlp 目前装在 `~/.whisper-venv`（py3.9，2025.10.14 是最后兼容版）；将来系统 python 升级后建议重装到新版环境。

## 参考（需要时加载）

- [references/lessons.md](references/lessons.md) — 踩坑与决策记录：xsec_token、图文/视频判断、稳定帧、图注对齐、口播分段、为什么单篇采集只留一个入口
- [references/xhs.md](references/xhs.md) — 小红书 xsec_token、移动端 SSR 字段、note.json 结构、图文 OCR 与排错
- [references/pipeline.md](references/pipeline.md) — 视频脚本参数全集、work.json 结构、各阶段耗时实测、常见报错
- [references/sph-capture.md](references/sph-capture.md) — 视频号为什么下不了、sph_meta.js 原理、手动投送的两种方式
