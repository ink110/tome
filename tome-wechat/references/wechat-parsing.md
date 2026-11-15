# 微信文章解析踩坑

> 记录 `wechat_article.py` 解析微信 HTML 时踩过的坑与解法。改脚本前先读这里。

## 结构要点

- 正文在 `<div id="js_content">`；标题 `og:title` / `<h1 class="rich_media_title">`；作者 `meta[name=author]` / `<a id="js_name">`。
- 正文文本常是 `<section>` 下的**裸文本 + `<span>` 碎片**（不是 `<p>`）。所以转换器里裸文本必须「流动」不另起段，只有块级元素（标题 / 图片 / `<hr>` / 列表）才前后加空行。
- 章节分隔常用 `<hr>`（转 `---`）。

## 图片

- 真实地址在 `data-src`（懒加载），`src` 常是 1×1 占位；`data-src=""` 或 `data:` 跳过。
- 追踪像素：`width="1" height="1"` 的 `<img>` 跳过。
- GIF 判据：URL 含 `wx_fmt=gif` 或路径 `.gif`。
- **URL 里的 `&amp;` 是 HTML 实体，必须 `html.unescape` 解码**再下载。
- 下载需 `Referer: https://mp.weixin.qq.com/`，否则 403。

## 视频 / 音频

- 直接 `<video src=…mp4>` 可抓。
- 绝大多数公众号视频是腾讯视频播放器 `<iframe data-src="…/mp/readtemplate?…&action=mpvideo&vid=wxv_…" data-mpvid="wxv_…">`，只给 vid，不给 mp4。
  - 真实 mp4 在全文 JS 的 `var videoPageInfos = [{video_id: 'wxv_…', video_page_info: {mp_video_trans_info: [{format_id, url, video_quality_level, …}]}}]` 里，一支视频多个清晰度，取 `video_quality_level` 最高那档。
  - mp4 url 形如 `url: ('http://mpvideo.qpic.cn/…mp4?dis_k=…\\x26amp;dis_t=…&auth_info=…')`——注意**括号包裹** + `\\x26amp;` 两步解码（先 `replace("\\x26","&")` 再 `html.unescape`）。
  - 下载需 `Referer: https://mp.weixin.qq.com/`，否则 **403**（UA 单独不够）。
  - 识别 vid：`data-mpvid` 优先，兜底 `[?&]vid=(wxv_[A-Za-z0-9_]+)` 匹配 data-src。
  - 非腾讯视频的第三方 iframe（无 vid）仍走占位链接。
- 音频 `<mpvoice>` 转 `audio`。

## 发布时间兜底

- 优先级：`article:published_time` meta → `og:release_date` → `<em id="publish_time">` → `var createTime`。
- 实测：部分文章 `article:published_time` meta 为空，靠 `var createTime` 拿到「2026-10-01 16:48」。

## 反爬判断（关键）

- **不要**靠关键词「验证 / 环境异常」——页面 JS 里常含这些词，会误判。
- 正确判断：`js_content` 是否存在且非空 + `og:title` 有值。任一缺失才算失败。

## 互动数据（拿不到，别估算）

- 文章 HTML 里阅读 / 在看 / 转发位置只有 JS 占位，静态抓取拿不到数字。
- `getappmsgext` 能返回文章元数据，但 `appmsgstat` 为空；换 UA / Referer / cookie 也不稳定。
- 搜狗微信搜索不展示阅读量。
- 结论：**不要写「10 万+」「估算阅读」**。传播复盘改用站内回引、站外转载、引用跨度、独家材料四项代理指标，见 `tome-spread/references/selection.md`。

## 成书转义

- 正文可能出现 `<滑动查看下一张图片>`；pandoc 会当成 HTML 标签，生成 mismatched tag。
- staging 顺序：先把该短语换成 `（滑动查看下一张图片）`，再把所有裸 `<` 换成 `&lt;`。
- 顺序反了会在合并 md 里出现字面量 `&lt;`；重跑 staging + 构建即可。
- 完整 staging 流程见 `tome-book/references/wechat-book.md`。

## 已知边界

- 全页约 3.5MB：用「定位 `js_content` + div 括号配对」截取内层再解析，避免 `<script>`/`<style>` 里的假 `data-src`。
- 单篇重复导入同一篇会覆盖 `eagle-cache/` 副本 + 重复入 Eagle（单篇不去重）；专辑批量模式会按 `source` 的 `mid:idx` 跳过。
- 专辑枚举 / 分页 / 去重 / 索引细节见 [album-batch.md](album-batch.md)。

## 关键经验（已踩坑）

- 微信图片 / 视频**下载都要带 `Referer: https://mp.weixin.qq.com/`**，缺了直接 403（光 UA 不够）。
- 腾讯视频播放器 `<iframe>` 不是死路：`data-mpvid` / `vid=wxv_…` 只是 vid，真实 mp4 在全文 JS `var videoPageInfos` 里，按 `video_quality_level` 取最高清档。
- 抽正文只解析 `<div id="js_content">`（div 括号配对截取），别全文正则——`<script>` 里有假 `data-src` 会污染结果。
- 成书时正文的 `<滑动查看下一张图片>` 和裸 `<` 会被 pandoc 当成 HTML 标签，导致 XML mismatched tag；staging 时先换中文括号，再统一转 `&lt;`。
