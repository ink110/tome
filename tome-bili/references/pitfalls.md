# 踩坑记录

遇到报错时对照这里排查。

- **无 cookie / 只有游客 cookie → 接口一律 412**；只有 SESSDATA、缺 DedeUserID/sid 等 → -101。必须用插件导出完整 cookie。
- **subtitle_url 截断（丢了 auth_key）→ 403**。务必取完整 URL。
- yt-dlp 访问网页 HTML 被风控 412，绕过网页直接走 API 即可。
- **`player/v2` 会返回错乱字幕**（别的视频内容 / 全是「♪音乐♪」），必须用 `wbi/v2`（签名算法见 `flow.md`）。拿到字幕务必校验：条数与时长匹配、首条内容与标题相关，不对就重取。
- Python 请求注意：系统 Python 3.9.6 默认 TLS 证书校验正常，**无需关闭校验**（关闭校验会触发安全策略拦截）。cookie 行要 `strip()` 去换行。
- 部分视频**没有 AI 字幕**（`wbi/v2` 的 `subtitle.subtitles` 为空数组，`asr_language` 也空）。此时标记为「无字幕，待 ASR 转写」，走 `asr.md`。
- **`call_ollama` 会超时**：`bilibili_to_md.py` 用 `stream=False` + `timeout=900`，让模型复述整篇口播稿 → 长文生成超 15 分钟就 `socket.timeout`，多分P并发还互相拖慢。正确做法：① 用 `stream=True` 流式调用（token 逐条到达持续重置超时，不断连）；② 多分P**串行**跑；③ **原文别让模型复述**——让模型只生成到「学习记录」，保存时把逐句原文直接追加到「附：完整口播稿」（更快 + 逐字忠实，模型复述长文易截断/出错）。
- **ASR 环境可能不存在**：重装/清理后 venv 和模型可能都没了，跑前先检查，缺失按 `asr.md` 第 1 步重建。
- **无 ffmpeg/brew 也能 ASR**：faster-whisper 用 PyAV 解码，不依赖 ffmpeg。int8 转写时的 `RuntimeWarning: divide by zero / overflow`（静音帧 mel_spec）无害，可忽略。
- **huggingface.co 被墙**：pip 装包用 `-i https://pypi.tuna.tsinghua.edu.cn/simple`，whisper 模型从 `hf-mirror.com` 下。
