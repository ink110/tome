# ASR 转写（无字幕时）

部分视频没有 AI 字幕（`wbi/v2` 的 `subtitle.subtitles` 为空数组）。此时用本地 faster-whisper 转写。

1. **环境（可能不存在，先检查）**：venv 在 `~/.whisper-venv`，模型在 `~/.whisper-models/small`（small + int8，中文够用）。
   机器重装/清理后这两个可能都没了（2026-10 就遇到「文档写已搭好，实际 venv+模型都被删」）。先验证：
   ```bash
   ~/.whisper-venv/bin/python -c "import faster_whisper, opencc; print('OK')" && ls ~/.whisper-models/small/model.bin
   ```
   缺失时重建（约 10 分钟，全本地）：
   ```bash
   python3 -m venv ~/.whisper-venv
   ~/.whisper-venv/bin/pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
   ~/.whisper-venv/bin/pip install faster-whisper opencc -q -i https://pypi.tuna.tsinghua.edu.cn/simple
   mkdir -p ~/.whisper-models/small && cd ~/.whisper-models/small
   BASE="https://hf-mirror.com/Systran/faster-whisper-small/resolve/main"
   for f in config.json tokenizer.json vocabulary.txt model.bin; do curl -sL -o "$f" "$BASE/$f"; done
   ```
   - pip 装系统 Python 会 PermissionError，必须用 venv。
   - 模型 4 个文件（preprocessor_config.json 在 HF 上不存在，无需下载）；model.bin 约 483MB。
   - huggingface.co 被墙：PyPI 用清华镜像、模型用 hf-mirror.com。
   - **不需要 ffmpeg**：faster-whisper 自带 PyAV 解码 `.m4s`。
2. **下载音频**：用 `/x/player/wbi/playurl` 接口（**不是 wbi/v2，后者无 dash**），参数 `fnval=16`，从 `data.dash.audio` 选最高 bandwidth 的 `baseUrl` 下载（带 Referer，存 `.m4s`）。
3. **转写 + 繁转简**（small 模型会输出繁体，必须转简）：
   ```python
   from faster_whisper import WhisperModel
   from opencc import OpenCC
   cc = OpenCC('t2s')
   m = WhisperModel(os.path.expanduser('~/.whisper-models/small'), device='cpu', compute_type='int8')
   segs, info = m.transcribe('audio.m4s', language='zh', beam_size=5, vad_filter=True)
   lines = []
   for s in segs:
       mm, ss = int(s.start // 60), int(s.start % 60)
       lines.append(f"[{mm:02d}:{ss:02d}] {cc.convert(s.text.strip())}")
   ```
   每个 seg 有 `start`/`end`/`text`，拼成 `[mm:ss] 文本`（时间戳用 `[mm:ss]`，与字幕格式一致，别用 `.1f` 秒格式）。
4. 转写文本再走「生成 Markdown 文档」步骤（见 `flow.md`）。注意 whisper 对人名/专有名词有识别误差，结构化文章里做常识性校正。

## 批量补全：先转写，再统一蒸馏

无字幕集很多时，不要每集都让 `asr_episode.py` 立刻调模型；先用 `--raw-only` 只落原文和元信息：

```bash
~/.whisper-venv/bin/python ~/.claude/skills/tome-bili/asr_episode.py "<该集BV>" \
  --out "<项目>/00-收集" --ep 63 --prefix "<前缀>" --raw-only
```

全部转写完成后，再统一跑一次：

```bash
python3 ~/.claude/skills/tome-bili/crawl_collection.py "<合集BV>" \
  --out "<项目>/00-收集" --prefix "<前缀>" --distill-only
```

这样 ASR 和本地模型不抢资源，失败重跑也只需补 `_raw.txt` 缺失的集。
