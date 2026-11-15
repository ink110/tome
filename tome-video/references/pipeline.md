# tome-video 流水线参考

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
cd ~/.claude/skills/tome-video/scripts && npm install
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

另有 `transcript.txt`（整段口播纯文本）和 `frames/`（已按时间重命名为 `f0001.jpg` 起）。

## 各阶段行为

- **download**：`yt-dlp --write-info-json`。小红书链接必须带 `xsec_token`（App 里「分享 → 复制链接」会带）；过期 token 返回 403。
- **transcribe**：`mlx_whisper --language zh --output-format json`，保留原始分段时间戳；结果为空（纯 BGM 无人声）直接退出。
- **frames**：ffmpeg `select='gt(scene,阈值)'`，阈值 0.35 起步，帧数 <8 时依次降到 0.25、0.15；帧间隔 <2s 的丢弃。场景切换太少（纯口播）时改为均匀采样 `min(12, max(4, 时长//30))` 帧。最终上限 40 帧（`--max-frames`）。
- **vision**：逐帧调 Ollama，要求幻灯片 / 图表 / 界面文字**逐字提取**，单帧超时 300s；失败不中断，`vl` 写成错误说明。
- **align**：每帧取 `[t-8s, t+4s]` 窗口内的口播，供 Claude 写有信息增量的图注。

## 耗时参考

- 19 分钟视频转写约 3–5 分钟（M 系列芯片，large-v3-turbo）。
- 视觉描述约 10 秒 / 帧，40 帧约 6–7 分钟。
- 端到端主要耗时在 transcribe 和 vision；两者都可断点续跑。

## 常见报错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `No video formats found` | 链接是小红书**图文笔记**，没有视频 | 改用 `xhs_note.py --url <链接>`（取图 + OCR） |
| 小红书 403 / 下载失败 | `xsec_token` 过期 | 在 App 里重新分享，拿新链接 |
| `视频号链接拿不到视频流` | 匿名接口只返回元数据 | 先 `node sph_meta.js`，再手动投送 mp4 + `--sph-meta` |
| `转写结果为空` | 纯 BGM / 无人声 | 换有口播的视频，或改用抽帧 + OCR |
| `mlx_whisper 报错` | 路径或模型不存在 | 按上表检查 `MLX_WHISPER` / `WHISPER_MODEL` |
| 视觉描述全是 `(视觉描述失败)` | Ollama 没启动 / 模型没拉 | `ollama serve`、`ollama list` |
| 帧太少或重复 | 场景检测阈值不合适 | 调 `SCENE_THRESH`，或直接用均匀采样兜底 |
