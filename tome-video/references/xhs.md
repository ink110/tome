# 小红书图文 / 视频采集参考

脚本：`scripts/xhs_note.py`（图文 + 视频入口）和 `scripts/video_note.py`（视频流水线）。

## 链接与 xsec_token

小红书笔记页的数据不在服务端 HTML 里，前端通过带签名的接口加载。
分享链接必须带 `xsec_token`，否则页面只返回空壳和登录墙。

获取方式：App 打开笔记 → 右上角「分享」→「复制链接」。复制结果里通常带：

```
https://www.xiaohongshu.com/explore/<note_id>?xsec_token=<token>&xsec_source=app_share
```

短链 `http://xhslink.com/a/...` 也可以，脚本会跟随跳转。token 有时效，
过期后 `__INITIAL_STATE__` 里 `noteDetailMap` 为空，重新分享即可。

## 为什么必须用移动端 UA

2026-11 实测同一链接：

| UA | 结果 |
| --- | --- |
| iPhone Safari | 200，`__INITIAL_STATE__` 含完整 `noteData`（标题/正文/图片/互动数） |
| 桌面 Chrome / Googlebot | 200，只有首页壳和登录二维码 |
| 微信内置浏览器 | 200，1KB 空页 |

`xhs_note.py` 固定发 iPhone UA + `Referer: https://www.xiaohongshu.com/`。

## 页面数据路径

移动端（首选）：

```
window.__INITIAL_STATE__.noteData.data.noteData
```

桌面端（兜底，通常为空）：

```
window.__INITIAL_STATE__.note.noteDetailMap[<note_id>].note
```

字段映射：

| note.json | 来源 |
| --- | --- |
| `note_id` | `noteId`，缺失时从 URL 提取 |
| `type` | `type`（`video` / `normal`） |
| `source_url` | 实际请求的分享链接（带 `xsec_token`，会过期） |
| `canonical_url` | `https://www.xiaohongshu.com/explore/<note_id>`，长期可溯源 |
| `title` / `desc` | 同名 |
| `author` / `author_id` | `user.nickName` / `user.userId` |
| `published` | `time`（毫秒时间戳，东八区） |
| `stats` | `interactInfo.likedCount / collectedCount / commentCount / shareCount` |
| `tags` | `tagList[].name` |
| `images` | `imageList[].url`，保持原顺序 |
| `has_video` | `video` 是否存在 |

## 图文笔记流水线

```bash
python3 xhs_note.py --url '<带 xsec_token 的链接>'
```

1. `fetch`：iPhone UA 抓页面，解析 `__INITIAL_STATE__`。
2. `images`：按 `imageList` 顺序下载到 `images/001.jpg…`。图片 URL 形如
   `http://sns-webpic-qc.xhscdn.com/...!h5_1080jpg`，直接 GET 即可，需带 Referer。
3. `ocr`：逐张调 Ollama `qwen3-vl:32b-instruct`，提示词要求逐字转写、保留层级、
   不概括、看不清用 `[?]`。结果存 `ocr/001.txt…`，同时写回 `note.json.ocr`。
4. 产物：`~/.cache/tome-video/<slug>-<hash>/note.json`。

组装笔记时：frontmatter 的 `source` 用 `canonical_url`，`share_url` 存
`source_url`（带 token，短期可匿名打开）。这样 token 过期后笔记仍能定位到原帖。

`--no-ocr` 只取图；`--meta-only` 只解析元数据；`--max-images N` 限制下载张数。

## 视频笔记

`xhs_note.py` 解析到 `type == video` 时，打印元数据后自动调用 `video_note.py`，
参数为同一个 URL / workdir / 标题 / 作者。也可以直接用：

```bash
python3 video_note.py --url '<链接>' --title '<标题>' --author '<作者>'
```

yt-dlp 对小红书视频笔记可用；图文笔记会报 `No video formats found`，
所以不要用 yt-dlp 处理图文。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `没拿到笔记数据` | 缺 token / token 过期 / 笔记删除 | App 重新分享，确认链接含 `xsec_token=` |
| 页面只有登录二维码 | 用了桌面 UA 或 Googlebot UA | `xhs_note.py` 已固定 iPhone UA，不要改 |
| 图片下载 403 | 缺 Referer | 脚本已带；自写脚本时补 `Referer: https://www.xiaohongshu.com/` |
| OCR 很慢 | qwen3-vl 32b 逐张推理 | 正常约 10–20s/张；可先 `--no-ocr` 取图 |
| OCR 有错字 | 视觉模型限制 | 原文逐字保留，重要数字对图核；不要自行改正 |
| Live Photo 只有一张 | 只取静态图 | 预期行为 |
