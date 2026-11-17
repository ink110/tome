# 微信视频号：获取 mp4 的四条路径

## 结论

截至 2026-11，视频号分享页（`weixin.qq.com/sph/...`、`channels.weixin.qq.com/...`）
在未登录的桌面浏览器里**只给封面和元数据**，页面主体提示「扫码观看」。
匿名接口 `get_feed_info` 返回 `feedInfo` / `authorInfo`，但**不返回视频流地址**。

因此：

- **Chrome / Eagle 插件拿不到视频**：页面没有 `<video>`，也没有可嗅探的媒体 URL。
  这不是插件用法问题，是链路上没有媒体。
- 获取 mp4 必须走「登录态」或「微信客户端」。按可靠性排序有四条路。

## 路径 A：同源原片（零配置，先试这条）

很多视频号内容会同步发到 YouTube / B站 / 小红书。`sph_fetch.py` 会：

1. 抓元数据（标题、作者、发布日期）。
2. 用标题在 YouTube 搜索，核对标题相似度、时长、发布日期。
3. 匹配度 ≥ 80 才下载，避免下错片。

```bash
python3 ~/.claude/skills/tome-clip/scripts/sph_fetch.py --url "<sph链接>" \
  --proxy http://127.0.0.1:7890
```

搜索不可靠时（原片没进前 5、被短转载淹没），人工确认一次原片后登记映射：

```bash
python3 ~/.claude/skills/tome-clip/scripts/sph_fetch.py --url "<sph链接>" \
  --youtube-url "https://www.youtube.com/watch?v=xxxx"
```

映射写入 `~/.cache/tome-clip/sph-sources.json`，以后同一链接自动复用。

> 实测坑：YouTube 搜索对中文标题的召回很不稳定。原片可能搜不到，反而搜到
> 几十秒的搬运版。所以「匹配度不足就放弃」是刻意设计，不要为了自动化放宽阈值。

## 路径 B：腾讯元宝官方链路（一次扫码，之后全自动）

`tome-clip` 已封装成一条命令：

```bash
python3 ~/.claude/skills/tome-clip/scripts/sph_yuanbao.py --url "<sph链接>"
```

首次会弹出 Chrome 扫码登录元宝；登录态保存在 `~/.cache/tome-clip/yuanbao-profile`，
之后同一命令自动解析、下载、跑视频流水线。下面是底层步骤，排障时再用。

开源项目 `download-wechat-channels`（MIT）：

- 浏览器打开 `https://yuanbao.tencent.com/`，本人扫码登录一次。
- 在页面上下文里调用腾讯官方解析接口，拿到 `playable_url` + `wx_export_id`。
- 再用官方 `channels.weixin.qq.com` 链路下载 MP4；不装证书、不改系统代理、
  不启动常驻服务、不操控微信客户端。

使用：

```bash
# 1) 准备一次性临时文件（输出 share_url / profile_file / browser_url）
python3 channels.py --json prepare "<sph链接>"

# 2) 在已登录元宝的浏览器里执行 bridge，把官方解析结果写入 profile_file
#    参考该项目的 scripts/yuanbao_bridge.mjs

# 3) 下载
python3 channels.py --json download "<sph链接>" \
  --official-profile "<profile_file>" --quality auto
```

下载默认落在 `~/Downloads/视频号`，然后交给 tome-clip：

```bash
python3 ~/.claude/skills/tome-clip/scripts/sph_fetch.py --url "<sph链接>" --from-downloads
```

边界：依赖腾讯元宝的私有接口，改版可能失效；只处理本人有权保存的内容。

## 路径 C：微信 PC + 本地代理（兼容性最强）

开源后端 `ltaoo/wx_channels_download`（Go，10k+ stars，2026 仍活跃）：

- 首次以管理员身份运行，自动安装根证书并启动本地代理。
- 电脑微信播放视频号视频后，播放器旁会出现下载按钮；可切换清晰度。
- 视频流是加密的，下载后需要 ISAAC-64 解密（工具内置）。
- 封装成 Agent Skill 的版本：`ChantillyAn/wechat-video-skill`，提供
  `download.py --list` / `--browse <index>`，并会快照/恢复系统代理。

共同前提：**必须在电脑微信里播放一次**；手机微信的流量捕获不到。

## 路径 D：手动投送（永远可用）

1. 电脑微信打开视频，检查播放器「···」里是否有「保存视频」。
2. 手机微信视频页「保存到相册」→ 微信文件传输助手发回电脑（华为没有 AirDrop）。
3. 都不行就录屏。

然后：

```bash
python3 ~/.claude/skills/tome-clip/scripts/sph_fetch.py --url "<sph链接>" --file <mp4>
```

## 元数据抓取

```bash
cd ~/.claude/skills/tome-clip/scripts
npm install                       # 首次，安装 puppeteer-core
node sph_meta.js "<sph 分享链接>" meta.json
```

原理：用 `puppeteer-core` 启动本机 Chrome（headless），打开分享页，
监听页面自己发出的响应，截获 URL 含 `get_feed_info` 的 JSON。

输出字段：

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

`stats` 是选题素材数据，组装笔记时要写进 frontmatter。

## 跑视频流水线

`sph_fetch.py` 和 `sph_yuanbao.py` 默认会自动跑；也可以手动交给 `video_note.py`：

```bash
python3 ~/.claude/skills/tome-clip/scripts/video_note.py \
  --file <mp4> --sph-meta meta.json --workdir <目录>
```

产物是 `work.json`：111 段口播、稳定帧、逐帧视觉描述、图注对齐。Claude 负责筛帧和写笔记。

## 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `没抓到 get_feed_info` | 链接过期 / 页面结构变了 | 在 App 里重新分享；确认网络能打开分享页；重跑 |
| `Cannot find module 'puppeteer-core'` | 没 `npm install` | 在 `scripts/` 下安装 |
| Chrome 启动失败 | 本机 Chrome 不在默认路径 | 改 `sph_meta.js` 里的 `executablePath` |
| 页面一直不返回 | headless 被风控 | 改成 `headless: false` 手动过一次验证 |
| YouTube 搜索匹配度不足 | 中文标题召回差 / 只有搬运版 | 人工确认原片后用 `--youtube-url` 登记 |
| 元宝解析 401 | 登录态过期 | 重新扫码登录 |

## 边界

- 元数据抓取依赖页面现有接口；微信改版后可能失效，届时以「手动投送 + `--title/--author`」兜底。
- 视频号没有 `xsec_token` 概念，但分享链接同样会过期。
- 本流程不下载封面；`cover_url` 只写进元数据，需要时再单独下载。
- 所有路径都只应用于本人有权保存的内容，尊重版权与平台规则。
