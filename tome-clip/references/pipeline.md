# tome-clip 流水线参考

> 踩坑总表见 [lessons.md](lessons.md)。

脚本：`scripts/video_note.py`。只做机器活（下载 / 转写 / 抽帧 / 视觉描述 / 对齐），
不写 Obsidian 笔记；笔记由 Claude 读 `work.json` 后组装（见 SKILL.md）。

## 依赖与路径

脚本里用的是绝对路径，换机器时要改：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `WHISPER_PY` | `~/.whisper-venv/bin/python3` | 跑 yt-dlp 的 Python |
| `MLX_WHISPER` | `~/.local/bin/mlx_whisper` | 语音转写（Apple Silicon） |
| `WHISPER_MODEL` | `~/.models/whisper-large-v3-turbo`，不存在则 `~/models/...` | 模型目录 |
| `FFMPEG` | `~/.local/bin/ffmpeg` | 抽帧 / 探测时长 |
| `OLLAMA` | `http://localhost:11434` | 本地视觉模型 |
| `VL_MODEL` | `qwen3-vl:32b-instruct` | 逐帧描述 |

依赖安装：

```bash
# Python 侧（一次性）
python3 -m venv ~/.whisper-venv
~/.whisper-venv/bin/pip install yt-dlp
pip install mlx-whisper            # 按 mlx-whisper 官方说明
ollama pull qwen3-vl:32b-instruct

# 视频号元数据（可选，仅 sph_meta.js 需要）
cd ~/.claude/skills/tome-clip/scripts && npm install
```

## 参数

```bash
python3 video_note.py --url <链接>            # 小红书等 yt-dlp 支持的站点
python3 video_note.py --file <本地视频>        # 视频号投送 / 本地文件
  [--sph-meta meta.json]                      # 视频号元数据
  [--title 标题] [--author 作者] [--source-url 原链接]
  [--workdir 目录] [--from-stage 阶段] [--max-frames 40]
```

阶段顺序：`download → transcribe → frames → vision → align`。
`--from-stage` 只能从某阶段往后跑，不能只跑中间一段；重跑前会读同目录的 `work.json`。

## work.json 结构

```json
{
  "video": "/path/to/source.mp4",
  "meta": {
    "title": "标题",
    "author": "作者",
    "description": "简介",
    "source_url": "原始链接",
    "platform": "xiaohongshu / 微信视频号 / local",
    "duration": 1132.5,
    "webpage_url": "…",
    "stats": {"收藏": "…", "点赞": "…", "转发": "…", "评论": "…"}
  },
  "segments": [{"start": 0.0, "end": 3.2, "text": "…"}],
  "frames": [
    {
      "file": "/path/to/frames/f0001.jpg",
      "t": 12.5,
      "vl": "视觉模型对画面的逐字描述",
      "speech": "该帧前后 [t-8s, t+4s] 的口播文本"
    }
  ]
}
```

另有 `transcript.txt`（整段口播纯文本）、`transcript_paragraphs.txt`（按主题词/转折词切好的自然段）
和 `frames/`（已按时间重命名为 `f0001.jpg` 起）。

## 各阶段行为

- **download**：`yt-dlp --write-info-json`。小红书链接必须带 `xsec_token`（App 里「分享 → 复制链接」会带）；过期 token 返回 403。
- **transcribe**：`mlx_whisper --language zh --output-format json`，保留原始分段时间戳；结果为空（纯 BGM 无人声）直接退出。同时按主题词/转折词切自然段，写 `transcript_paragraphs.txt`。
- **frames**：不再按时均匀采样。先 1fps 取灰度小图，只哈希画面上方 68%（避开右下角人脸），用 dHash 检测换页；等画面连续两帧稳定后才取该帧，避免淡入/动画中间态。换页阈值 `HASH_THRESH=90`、稳定阈值 `HASH_STABLE=16`。检测失败或不足 4 帧才退回均匀采样。最终上限 40 帧（`--max-frames`）。
- **vision**：逐帧调 Ollama，要求幻灯片 / 图表 / 界面文字**逐字提取**，单帧超时 300s；失败不中断，`vl` 写成错误说明。
- **align**：每帧取 `[t-8s, t+4s]` 窗口内的口播，供 Claude 写有信息增量的图注。

## Eagle 上传（可选）

筛帧 / 取图完成后，把实际入笔记的图片传到 Eagle：

```bash
# 视频：只传入选帧
python3 ~/.claude/skills/tome-clip/scripts/eagle_upload.py \
  --workdir <workdir> --files f0007.jpg,f0012.jpg

# 图文：默认上传 note.json 的全部 local_images
python3 ~/.claude/skills/tome-clip/scripts/eagle_upload.py --workdir <workdir>
```

产物 `eagle_uploads.json` 记录 `file → item_id → eagle://item/...`；笔记里在每张图下加
`[在 Eagle 查看](eagle://item/<ID>)`，`attachments/` 继续作为本地预览副本。配置与
`tome-wechat` 共用 `~/.config/tome/config.json` 的 `media.eagle`，Eagle 需运行在 41595。

## 耗时参考

- 19 分钟视频转写约 30–60 秒（M 系列芯片，large-v3-turbo）。
- 稳定帧检测（1fps + dHash）约 1–2 分钟；40 帧视觉描述约 6–7 分钟。
- 端到端主要耗时在 vision；transcribe 和 frames 都很快。

## 常见报错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `No video formats found` | 链接是小红书**图文笔记**，没有视频 | 改用 `xhs_note.py --url <链接>`（取图 + OCR） |
| 小红书 403 / 下载失败 | `xsec_token` 过期 | 在 App 里重新分享，拿新链接 |
| `视频号链接拿不到视频流` | 匿名接口只返回元数据 | 先 `node sph_meta.js`，再手动投送 mp4 + `--sph-meta` |
| `转写结果为空` | 纯 BGM / 无人声 | 换有口播的视频，或改用抽帧 + OCR |
| `mlx_whisper 报错` | 路径或模型不存在 | 按上表检查 `MLX_WHISPER` / `WHISPER_MODEL` |
| 视觉描述全是 `(视觉描述失败)` | Ollama 没启动 / 模型没拉 | `ollama serve`、`ollama list` |
| 帧太少或重复 | 稳定帧检测阈值不合适 | 调 `HASH_THRESH` / `HASH_STABLE`，或降低 `SAMPLE_SEC` |
| 帧是空白 / 淡入中间态 | 稳定帧检测把动画停顿当成稳定 | 提高 `HASH_STABLE` 严格度；组装时按 `vl` 描述丢弃无文字帧 |
| 图注和图对不上 | 用了方法总览代替具体画面 | 先用 OCR/VL 读出该帧标题，再按画面元素写图注；对不上就换帧 |
