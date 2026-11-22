# 详细流程与判断标准

## 0. 输入盘点

典型输入：

```text
~/Desktop/某大会/
  ├── 10月30日 上午场.m4a
  ├── 10月30日 下午场.m4a
  └── IMG_001.jpg ... IMG_074.jpg
```

也可能是每场一个音频。先记录：

- 音频数量、时长、文件名（是否含场次/讲者）
- 图片数量、命名规律（连续编号 / 时间戳 / 相机名）
- 是否有现成 SRT、讲者名单、议程

**分场依据优先级**：文件名 > 议程 > 音频静音段 > SRT 时间轴 > OCR 归属。

一段长音频里有多场时，先按静音或主持人串场词切分，不要假设「一个文件 = 一场」。

## 1. 转写

```bash
python3 scripts/transcribe.py "<音频或目录>" "<work>/asr" zh
```

`transcribe.py` 按配置选择 provider：本地 `mlx-whisper` 或在线 OpenAI 兼容接口。
本地内部参数与旧版一致（抑制循环）：

```bash
mlx_whisper <audio> \
  --model <asr.local.model> \
  --output-dir <out> --output-name <name> --output-format json \
  --task transcribe --word-timestamps True \
  --condition-on-previous-text False \
  --compression-ratio-threshold 1.8 \
  --hallucination-silence-threshold 2 \
  --no-speech-threshold 0.6
```

参数意图：

| 参数 | 作用 |
| --- | --- |
| `--condition-on-previous-text False` | 降低 Whisper 陷入重复循环的概率 |
| `--compression-ratio-threshold 1.8` | 更早判定解码失败（默认 2.4 太宽松） |
| `--hallucination-silence-threshold 2` | 跳过长静音里的幻觉（需 word timestamps） |
| `--no-speech-threshold 0.6` | 静音判定 |

**语言选择**：

- 纯中文场：`zh`
- 纯英文场：`en`
- 中英混合/不确定：`auto`，再人工判断
- 同传场：如果中文轨是「同传叠原声」导致转写全是循环和碎片，**转英文原声轨**，按英文整理后译为中文。IXDC 的 Don Norman 圆桌就是这样救回来的。

## 2. SRT 转可读文本

```bash
python3 scripts/srt2txt.py --src <asr> --out <txt> --gap 1.5
```

按字幕间静音 >1.5 秒分段，合并同一段内的碎片行。

## 3. 照片 OCR

```bash
python3 scripts/ocr_images.py \
  --src "<图片目录>" --out "<work>/ocr" \
  --topics "场次A,场次B,场次C"
```

- 图片先缩到长边 1280px，视觉 token 降一个量级。
- 视觉 provider 可选：本地 Ollama（默认 `qwen3-vl:32b-instruct`）、在线 OpenAI 兼容视觉接口，或 `claude`（Claude Code 在会话中读图）。
- 只有本地 Ollama 需要每 20 张重启一次规避长跑卡死；脚本默认就会这样做。**如果同时有别的本地模型任务，先加 `--restart-every 0`**，否则会打断它们。
- 视频素材：先用 `ffmpeg` 抽关键帧，再对帧做 OCR；不要把整段视频直接塞给视觉模型。
- 输出三行：`OCR:`、`主题:`、`归属:`。

**归属判断**：先用 OCR 标题里的关键词匹配场次；匹配不上再看图片序号是否连续、讲者是否出现在画面。仍不确定就放总览，不要硬塞。

## 4. 转写清洗

### 4.1 必做的五件事

1. **去循环**：同一短语重复几十次（如 73 次「我们下一个」、642 次「想想想」），整段删除，只保留一次有信息量的表述。
2. **去重复句**：同一句在相邻段落出现两次，删后一次；但讲者刻意强调的金句可保留一次，不要机械去重。
3. **改同音错**：用幻灯片 OCR、讲者背景、产品名交叉校验。
   - 例：「Dino 位」→「金刚位」；「持有本地的那个人」→「屏幕里的那个人」；「会信念」→「会相信」。
4. **补逻辑**：口语里的指代（这个、那个、它）补成明确名词；断掉的因果链补一句过渡，但不要添加讲者没说的观点。
5. **删无信息碎片**：咳嗽、笑声、调试设备、重复的口头禅。无法还原的整段，用「（此处录音不清）」或直接删，不要编。

### 4.2 英文翻译

**翻译句子，不翻译术语。**

| 处理 | 例子 |
| --- | --- |
| 译成中文 | `When building gets faster, where is the design value?` → 「当构建越来越快，设计价值在哪里？」 |
| 保留原文 | AI、UX、UI、CLI、GUI、MCP、API、IDE、PRD、Token、Skill、Agent、ArkUI、HTML、ChatGPT、Siri |
| 保留品牌/产品名 | Pixso、Ardot、Workbuddy、Don Norman、Lenny's Newsletter、Apple |
| 首次出现可加中文解释 | `Behavior Design Spec（行为设计规范）`、`MCP（模型上下文协议）` |

不要全局把「AI」替换成「人工智能」、把「UI」替换成「界面」——会把标题、产品名和技术概念译坏（例如 `ArkUI` 变成 `Ark界面`）。

### 4.3 分段与断句

- 只在 `。！？` 断句，**不要**在 `；` 断，否则句子太碎。
- 引号/括号内不断句：`「设计是什么？是画图的吗？」` 保持一行。
- 每段 2–4 句，约 100–160 字；对话稿每个发言回合可独立成段。
- 段间空一行；不要每句都硬换行，否则手机阅读会很碎。

```bash
python3 scripts/format_transcript.py format <file>
python3 scripts/format_transcript.py check <file>
```

`check` 必须满足：`duplicate_sentences=0`、`english_sentences=0`、`bad_line_starts=0`。

## 5. 写笔记

### 5.1 信息结构

每场笔记必须能脱离录音独立阅读：

1. **一句话总结**：这场分享的核心主张，不是主题复述。
2. **图文正文**：按演讲顺序分节，每节先给判断，再把图片嵌在对应段落后；图注写「它证明了什么」。
3. **金句**：1–3 句，保留讲者原意。

口播在第 4 步已经清洗分段，直接用来写图文正文；笔记里不另设「口播全文 / 整理稿」。原始转写留在 `$WORK/txt`，需要追溯时另存 `_raw/<专题>/<笔记名>.txt`，不要放进笔记。

### 5.2 标题与文件名

```text
NN-<主题>-<讲者或机构>.md
```

- `NN` 两位序号，和总览表格一致。
- 主题用中文，讲者/机构用最容易被检索的写法。
- 总览固定 `00-<专题>总览.md`。

### 5.3 图片

默认是**图文长文**：图片按演讲顺序插在对应段落后，不设末尾幻灯片库。

先在正文里用占位符标出位置：

```markdown
<!-- IMG: IMG_010.jpg | 核心框架：理解用户 → 产品判断 → 写入行为 → 验证 -->
```

再写 `images.json`（总览仍可用 `__IMAGES__` 做主视觉区）：

```json
{
  "00-某大会总览.md": [
    {"file": "IMG_001.jpg", "caption": "大会主视觉"}
  ],
  "01-主题-讲者.md": [
    {"file": "IMG_010.jpg", "caption": "核心框架：理解用户 → 产品判断 → 写入行为 → 验证"}
  ]
}
```

```bash
python3 scripts/fill_images.py --map images.json --vault <笔记目录> --src <图片目录>
```

规则：

- 一张图只放在它支持的段落后；图注写「它证明了什么」，不写「现场照片」。
- 同一张原图只出现一次；跨笔记重复时，选最相关的一篇。
- 附件名 `eagle-cache/lectures/<专题>/<笔记前缀>-<序号>.jpg`，不要保留原文件名里的空格和特殊字符（会破坏 wiki 链接）。
- 分不出的图放总览，不硬塞进某一节。

## 6. 校验

```bash
python3 scripts/validate_notes.py "<笔记目录>"
```

检查项：

- 正文重复句为 0
- 正文英文句子为 0（专有名词/缩写不算）
- 行首没有孤立的 `」”）》】`
- 如使用 `<details>`（长表格/附录），开闭必须配对
- 所有 `[[...]]` / `![[...]]` 目标存在
- 没有残留 `__IMAGES__`

## 7. 交付报告

给用户：

- 输出目录
- 笔记数量、场次列表
- 图片数量、附件总数
- 转写/OCR 的已知不确定处（哪几段听不清、哪张图归属存疑）
- 下一步建议（是否需要补讲者信息、是否要导出 epub/公众号）

## 8. 导出 PDF

需要把某篇笔记发给别人或存档时：

```bash
python3 scripts/export_pdf.py "<笔记.md>" \
  --out ~/Desktop/<文件名>.pdf --open
```

- 保留 Obsidian 的标题层级、引用块、列表、图片和折叠稿结构。
- 图片自动压缩到长边 1400px 并内嵌，避免 PDF 过大。
- 折叠稿在 PDF 里展开成一节，不会丢内容。
- 依赖（可选）：Python `markdown` 包、Google Chrome；没有 `sips` 时会自动退回 ffmpeg 缩放。`doctor.py` 会检查并给安装建议。
- 导出前先跑 `validate_notes.py`；校验不过的笔记不要直接发。

## 9. 视频输入

整段视频（mp4/mov）转笔记时，先跑 `video_prepare.py`，再按 `references/video.md` 处理：

- 每 2 秒采样，只哈希画面上部 760px，用 512 位 dHash + 稳定帧聚类选关键帧。
- OCR 用全帧 1920px + `--strict`，不要裁底部。
- `dedupe_slides.py` 按 OCR 标题去重，代码截图默认跳过。
- 只把 `slides.json` 和选中的 20–30 张图读进在线上下文，避免 token 爆炸。
- 转写默认 turbo；实测 large-v3 不比 turbo 好。
- 圆桌/panel 用 `video_prepare.py --panel --frame-at 2`，只留 1 张现场图；重点做发言人备注和观点提炼，不做逐页 OCR。
