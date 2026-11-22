---
name: lecture-notes
description: 把讲座、大会、会议的录音、现场照片或整段视频整理成 Obsidian 图文笔记。当用户提供音频（m4a/mp3/wav）、现场照片、视频（mp4/mov）、圆桌/panel 录像，或说「整理讲座笔记」「大会录音转笔记」「会议录音归档」「视频转笔记」「圆桌整理」「按 IXDC 那套流程记录」「把这场分享记到 Obsidian」时使用。产出总览 + 每场一篇图文长文（口播融进正文、图片按内容内嵌、图注说明证明了什么）、金句；圆桌只保留 1 张现场图。 单条短视频 / 在线链接 / 图文笔记 → 用 tome-clip；本技能专注长讲座、多场会议、圆桌和纯音频 / 照片。
---

# Obsidian 讲座笔记

把「录音 + 图片」或「一段视频」变成可检索、可复读的 Obsidian 专题笔记。

**输入**：音频/图片文件夹，或一段视频。
**输出**：`setup.py` 配置的 `output_dir` 下的 `00-总览.md`、每场一篇图文长文；图片统一放媒体根（`media.local_root`）的 `lectures/<专题>/`，链接前缀用 `media.link_prefix`。默认建议 `<Obsidian库>/notes/<专题>/` + `<Obsidian库>/eagle-cache/lectures/<专题>/`。

只有音频没有图片：跳过 OCR，正文照常写，不插入图片。

## 首次运行（必须）

1. 先跑依赖自检：

```bash
python3 ~/.claude/skills/lecture-notes/scripts/doctor.py
```

缺什么按输出里的安装建议处理。ASR 和视觉模型都支持本地或在线二选一，不必两套都装。

2. 配置保存位置和模型：

```bash
python3 ~/.claude/skills/lecture-notes/scripts/setup.py
```

- **首次必须确认笔记保存目录**。如果用户没有指定，主动问「笔记保存到哪个目录？」，给出默认建议；用户确认后，把最终绝对路径复述一遍。
- `output_dir` 未配置时，不要开始转写或写笔记，也不要静默写到任何个人路径。
- ASR / 视觉 provider 可选 `auto`、本地（mlx-whisper / Ollama）或在线（OpenAI 兼容接口）。

## 先定三件事（有缺省，不要反复问）

| 项 | 缺省 |
| --- | --- |
| 专题名 | 源文件夹名，去掉日期和杂乱后缀 |
| 日期 | 文件修改日期；没有就用今天 |
| 输出目录 | 由 `setup.py` 的 `output_dir` 决定；首次必须让用户确认绝对路径 |

用户给了别的库或目录，以用户为准。专题名、讲者名、场次主题拿不准时，先按文件名和 OCR 给最合理的一版，最后在报告里说明，不要中途反复确认。

## 视频输入（mp4/mov）

先按「流程 1」建好 `$SKILL` 和 `$WORK`，再跑：

```bash
python3 "$SKILL/scripts/video_prepare.py" "<视频>" --out "$WORK/video"
python3 "$SKILL/scripts/ocr_images.py" --src "$WORK/video/keyframes" \
  --out "$WORK/video/ocr" --strict --max-side 1920 --restart-every 0
python3 "$SKILL/scripts/dedupe_slides.py" \
  --src "$WORK/video/ocr" --out "$WORK/video/slides.json"
```

- 音频走第 2 步，默认 turbo；OCR 用全帧 1920px，不裁底部。
- 只把 `slides.json` + 选中的 20–30 张图读进上下文，不回读全部 OCR。
- 选帧阈值、去重和 token 预算见 `references/video.md`。

### 圆桌 / Panel 视频

多人固定座位、没有逐页幻灯片时，用 `--panel --frame-at 2` 只留 1 张现场图；不做逐页 OCR，重点做转写和发言人归属，确认不了的身份写「XX 公司 / 行业 / 主持人」。细节见 `references/video.md`。

## 流程

### 1. 盘点与建工作区

```bash
SKILL=~/.claude/skills/lecture-notes
if [ -n "${CLAUDE_JOB_DIR:-}" ]; then
  WORK="$CLAUDE_JOB_DIR/tmp/lecture-notes"
else
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/lecture-notes.XXXXXX")
fi
mkdir -p "$WORK"/{asr,txt,ocr}
```

先列清音频数量、时长、图片数量、文件名规律。文件名里常带场次、讲者或序号，是分场和图片归属的第一线索。

### 2. 转写

```bash
python3 "$SKILL/scripts/transcribe.py" "<音频或文件夹>" "$WORK/asr" zh
```

- 中文场用 `zh`；英文场用 `en`；不确定用 `auto`。
- provider 默认读配置：本地 `mlx-whisper` 或在线 OpenAI 兼容接口；可用 `--provider local|openai` 临时覆盖。
- 中文同传场如果转写大量循环、乱码，**改用英文原声轨重转**，再译为中文。不要硬修垃圾转写。
- 一段长录音包含多场时，先按文件名/静音段/SRT 时间轴切场，再逐场整理。

```bash
python3 "$SKILL/scripts/srt2txt.py" --src "$WORK/asr" --out "$WORK/txt"
```

### 3. 照片 / 视频帧 OCR

```bash
python3 "$SKILL/scripts/ocr_images.py" \
  --src "<图片文件夹>" --out "$WORK/ocr" \
  --topics "话题A,话题B,话题C"
```

每张图输出「OCR + 主题 + 归属」。用 OCR 标题和文件名把照片分到各场；分不出的先放总览，不要硬塞。**同一张图只能在一篇笔记里出现一次。** 视频帧用 `--strict`，只输出文字。

provider 默认读配置：`ollama`（本地视觉模型）/ `openai`（在线视觉接口）/ `claude`（脚本只列出图片，由 Claude Code 在会话中逐张读图并写 `ocr/<图片名>.txt`）。

### 4. 清洗转写

按 `references/pipeline.md` 的校对标准处理：

- 去掉 Whisper 循环、重复句、口头语。
- 用幻灯片 OCR 校正同音错词、产品名、人名。
- 英文句子译成中文；品牌、产品名、通用技术缩写保留原文。
- 按语义分段：每段 2–4 句、约 100–160 字；只在 `。！？` 断句，引号/括号内不断。

```bash
python3 "$SKILL/scripts/format_transcript.py" format "$WORK/txt/<场次>.txt"
python3 "$SKILL/scripts/format_transcript.py" check  "$WORK/txt/<场次>.txt"
```

### 5. 写笔记

用 `references/note-template.md` 的结构：标题、讲者信息、一句话总结、图文正文、金句。

- **默认写图文长文，不做末尾图库。** 正文按演讲顺序分节，每节先给判断，再把对应图片嵌在相关段落之后；图注写它证明了什么，不写「现场照片」。
- **口播融进正文，不另设「口播全文 / 整理稿」章节。** 转写在第 4 步清洗分段后，直接用来写正文；笔记本身就是完整文章，读者不需要再看一份逐字稿。
- 总览 `00-<专题>总览.md`：场次地图（wiki 双链）、跨场洞察、主视觉。
- 每场 `NN-<主题>-<讲者/机构>.md`：观点要能独立读懂，不依赖录音；视频场每节加 `[hh:mm:ss]`，图片 20–30 张，全部内嵌。
- 在正文里用 `<!-- IMG: <文件名> | <图注> -->` 标出图片位置，再统一落盘：

```bash
python3 "$SKILL/scripts/fill_images.py" \
  --map "$WORK/images.json" --vault "<笔记目录>" --src "<图片文件夹>" \
  --media-dir "<Obsidian库>/eagle-cache/lectures/<专题>" \
  --link-prefix "eagle-cache/lectures/<专题>"
```

图片命名：`eagle-cache/lectures/<专题>/<笔记前缀>-<序号>.jpg`。图注说明这张图证明了什么；同一张图只在一篇笔记里出现一次。

### 6. 校验与交付

```bash
python3 "$SKILL/scripts/validate_notes.py" "<笔记目录>"
```

必须全部通过：正文无重复句、无英文句子、没有行首孤立标点；如使用 `<details>` 必须配对；双链和附件都存在；没有残留 `__IMAGES__` 或 `<!-- IMG: ... -->` 占位。

最后给用户一份短报告：专题目录、笔记数量、场次列表、转写/OCR 的已知不确定处。

PDF 导出是可选项，不在默认流程；需要时再看 `references/pipeline.md` 的「导出 PDF」。

## 依赖

`doctor.py` 会逐项检查并给安装建议：

- **必需**：Python 3.9+、`ffmpeg` / `ffprobe`；ASR 使用本地 `mlx-whisper` + 模型，或在线 OpenAI 兼容接口；视觉使用本地 Ollama + 视觉模型，或在线视觉接口。
- **可选**：Chrome + Python `markdown`（PDF 导出）、`sips`（macOS 图片缩放；缺失时自动退回 ffmpeg）。

## 关键原则

- **图文长文优先**：图片是论证的一部分，放在对应段落里；不要把所有图堆到文末。
- **一篇笔记就是一篇完整文章**：口播、图片、判断在同一个叙事里；不另附逐字稿或「口播全文」。原始转写只留在 `$WORK`，需要追溯时另存 `_raw/`，不要塞回笔记。
- **幻灯片是事实校准源**：人名、产品名、数字优先信 OCR，其次信上下文。
- **不编造**：听不清的用「无法可靠还原」标注或删掉，不要补一个像真的结论。
- **翻译的是句子，不是术语**：AI、UX、UI、MCP、Token、ArkUI 这类保留原文，否则会把标题和产品名译坏。
- **原始文件不动**：只读源目录；所有中间产物放 `$WORK`。
- **先跑通再美化**：转写、分场、图片归属正确后，再打磨文风。
- **视频省 token**：本地生成 `slides.json`，在线只读它和选中的图；不回读全部 OCR/contact sheet。

## Where to go next

| 场景 | 读 |
| --- | --- |
| 具体命令、分场、双语、翻译规则 | `references/pipeline.md` |
| 视频选帧、OCR、去重、token 预算 | `references/video.md` |
| 总览/单场笔记模板 | `references/note-template.md` |
| 踩过的坑和排查顺序 | `references/pitfalls.md` |
