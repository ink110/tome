# 微信视频号：为什么下不了视频，以及怎么配合手动投送

## 结论

截至 2026-11，视频号的分享页（`weixin.qq.com/sph/...`、`channels.weixin.qq.com/...`）
在未登录的桌面浏览器里**只给封面和元数据**，页面主体提示「扫码观看」。
匿名接口 `get_feed_info` 返回 `feedInfo` / `authorInfo`，但**不返回视频流地址**。
所以本技能对视频号是半自动：元数据自动抓，mp4 由人投送。

不要尝试绕过登录或逆向视频流；成本高且容易失效。手动投送一次只要十几秒。

## 第一步：抓元数据

```bash
cd ~/.claude/skills/tome-video/scripts
npm install                       # 首次，安装 puppeteer-core
node sph_meta.js "<sph 分享链接>" meta.json
```

原理：用 `puppeteer-core` 启动本机 Chrome（headless），打开分享页，
监听页面自己发出的响应，截获 URL 含 `get_feed_info` 的 JSON。

输出的 `meta.json`：

```json
{
  "title": "…",
  "author": "…",
  "description": "…",
  "source_url": "…",
  "platform": "微信视频号",
  "published": "2026-11-09",
  "stats": {"收藏": "…", "点赞": "…", "转发": "…", "评论": "…"},
  "cover_url": "…"
}
```

`stats` 是选题时的重要数据，组装笔记时要写进 frontmatter。

## 第二步：投送 mp4（二选一）

1. **桌面微信 + 抓包**：在电脑微信里打开该视频，用 res-downloader 一类工具
   保存 mp4；文件通常落在 `~/Downloads/`。
2. **手机微信**：视频页「保存到相册」→ AirDrop 到 Mac。

告诉 Claude 文件路径即可，后续全自动。

## 第三步：跑流水线

```bash
python3 ~/.claude/skills/tome-video/scripts/video_note.py \
  --file ~/Downloads/xxx.mp4 \
  --sph-meta meta.json
```

`--sph-meta` 会覆盖 `meta` 里的标题、作者、来源、互动数，并把 `platform` 标成「微信视频号」。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `没抓到 get_feed_info` | 链接过期 / 页面结构变了 / 没等到接口返回 | 在 App 里重新分享；确认网络能打开分享页；重跑一次 |
| `Cannot find module 'puppeteer-core'` | 没 `npm install` | 在 `scripts/` 下安装 |
| Chrome 启动失败 | 本机 Chrome 不在默认路径 | 改 `sph_meta.js` 里的 `executablePath` |
| 页面一直不返回 | headless 被风控 | 改成 `headless: false` 手动过一次验证 |

## 边界

- 元数据抓取依赖页面现有接口；微信改版后可能失效，届时以「手动投送 + `--title/--author`」兜底。
- 视频号没有 `xsec_token` 概念，但分享链接同样会过期。
- 本流程不下载封面；`cover_url` 只写进元数据，需要时再单独下载。
