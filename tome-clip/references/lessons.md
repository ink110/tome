# 单篇采集踩坑与决策记录

记录 2026-11 在公众号 / 小红书 / 视频号采集上踩过的坑。新问题先补这里，再改代码；
SKILL.md 只保留规则，具体原因和排查过程放这里。

## 1. 链接与访问

### 小红书必须有 xsec_token

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `noteDetailMap` 为空 / 只有登录二维码 | 服务端只对移动端 UA 渲染笔记数据；桌面 UA 和 Googlebot UA 都落登录墙 | App 打开笔记 → 分享 → 复制链接；脚本固定 iPhone UA |
| 页面 200 但没有任何笔记字段 | 链接缺少 `xsec_token`，或 token 过期 | 重新分享；确认链接里有 `xsec_token=` |
| 短链 `xhslink.com` 能打开 | 短链会 302 到带 token 的长链 | 脚本跟随跳转即可 |

长期引用不要只存带 token 的分享链接：token 会过期。`note.json` / `work.json` 同时给
`source_url`（带 token，短期可匿名打开）和 `canonical_url`（`/explore/<note_id>`，长期可溯源）。
笔记 frontmatter 用 `source` 存 canonical，`share_url` 存分享链接。

### 小红书图文 vs 视频

- `yt-dlp` 报 `No video formats found` → 这是**图文笔记**，不是链接失效。走 `xhs_note.py`。
- SSR 里 `type == "video"` 或存在 `video` 字段 → 视频笔记，走 `video_note.py`。
- Live Photo 的 `stream` 里可能有短视频，只取静态图，不做视频转写。

### 公众号文章里的视频

腾讯视频播放器只给 `<iframe data-mpvid="wxv_...">`，真实 mp4 在全文 JS 的
`var videoPageInfos` 里，一支视频多个清晰度，取 `video_quality_level` 最高那档。
下载必须带 `Referer: https://mp.weixin.qq.com/`。

抓取阶段只负责把 mp4 下到本地素材库（Obsidian 模式 `eagle-cache/`，folder 模式 `assets/`）；转写和图文笔记交给视频流水线。
**不要再用文章 URL 重下一次**：本地 mp4 就是最高清流，重复请求可能 403。

### 视频号只能拿元数据

匿名 `get_feed_info` 只回标题、作者、互动数和封面，不回视频流；桌面页只提示「扫码观看」。
`scripts/sph_meta.js` 用 headless Chrome 截获这个接口；mp4 需要手动投送：

1. 电脑微信播放 → res-downloader 抓包保存；或
2. 手机微信「保存到相册」→ AirDrop 到 Mac。

把 mp4 路径给 Claude，后续 `video_note.py --file ... --sph-meta meta.json` 全自动。

## 2. 转写与 OCR

### whisper 原文没有标点

`mlx_whisper` 的 segment 是连续短句，拼起来是一整块无标点文本。不要直接塞进笔记。
`video_note.py` 会额外写 `transcript_paragraphs.txt`：按主题词/转折词切自然段，
目标每段 <260 字；笔记再按段粘贴，并给每种方法加一个小标题。

### 常见 ASR 错词

| ASR | 正确 |
| --- | --- |
| Iris 领域 | Areas 领域 |
| 实禁制编号 | 十进制编号 |
| Archairs | Archives |
| 眼尽 / 闪念 | 闪念 |
| 面相对象 | 面向对象 |
| B肉 / 避露 | B-roll |
| 刘比歇夫 | 柳比歇夫 |
| Vidmy | readme |
| GSON | JSON |
| 打案管 | 档案馆 |
| 信造比 | 信噪比 |
| 视像 | 事项 |
| 长清卡片 | 长青卡片 |

规则：**口播原文保留 ASR 原样**，在段首说明「未校对」；图注、核心要点、表格里用正确术语，
以画面文字为准。不要为了通顺改写原文。

### OCR

- qwen3-vl 提示词要求：逐字转写、保留标题/列表/表格层级、不概括、看不清用 `[?]`。
- 模型偶尔把结果包在 ``` 围栏里，写文件前剥掉。
- 图文笔记没有口播，信息全在图上；重要数字交付时提醒对图核，不要脑补。

## 3. 抽帧：稳定帧 > 均匀采样

### 问题

按时均匀采样会抓到幻灯片**淡入 / 动画中间态**：文字发灰、版面没稳定、图注和画面对不上。
2026-11 的一条 19 分钟视频里，均匀采样 12 帧，其中 2 帧是过渡帧，用户一眼就看出来了。

### 方案

1. 1fps 取灰度小图，只哈希画面上方 68%（避开右下角人脸）；
2. dHash 检测换页，阈值 `HASH_THRESH=90`（528 bit）；
3. 换页后等画面连续两帧稳定（`HASH_STABLE=16`，最多等 6 个采样），再取该帧；
4. 检测失败或不足 4 帧才退回均匀采样；
5. 上限 40 个候选，交给视觉模型/Claude 再筛。

### 候选帧仍可能有空白 / 过渡

dHash 只能保证「画面稳定」，不能保证「有信息」。稳定帧里仍可能夹着空白页或动画停顿。
组装时必须再筛：

- 按 `vl` 里的幻灯片标题分组，**同一标题只留最清晰的一张**；
- 标题与口播主题不一致的帧不要用；
- 空白、纯人脸、无文字过渡帧丢掉；
- 每种方法优先留「定义 / 示例」帧，不要全用「适合谁」页。

### 帧重命名 bug

均匀采样兜底曾从 `f0000.jpg` 开始编号，后面统一重命名成 `f0001...` 时互相覆盖，
最终只剩 1 帧。现在从 `f0001` 开始并两阶段重命名（先 `_tmp`，再 `fNNNN`）。

## 4. 图注：必须和图对上

图注不是方法总览，是**这张图**的说明。顺序：

1. 先写画面里实际出现的元素：标题、四层文件夹、表格字段、按钮、示例卡片；
2. 再补该帧 `speech` 的上下文；
3. 不写图里没有的东西。

自检两次：

- 遮住图注，只看图：能认出它讲的是不是这件事？
- 遮住图，只读图注：能在图里找到对应元素？

反例：图是「第6种 实体法 适合谁」的适合人群页，图注却写实体法的定义和属性字段——这就是不合格。
应该换成「实体法 人脉卡片」帧，或者把图注改成「这张图列出的适合人群是产品经理、创业团队负责人、猎头…」。

## 5. 口播排版

一篇 19 分钟视频的口播约 5,500 字。整段粘贴阅读体验极差。规则：

- 按主题词（首先 / 第二个是 / 接下来 / 最后是）和子主题词（优势 / 劣势 / 对 AI / 适合谁 / 解决办法）切段；
- 长段再按「但是 / 所以 / 而且 / 因为 / 如果 / 当 / 那么」切，目标每段 <260 字；
- 每段一个子主题，约 100–240 字；
- 在每種方法前加 `**PARA**`、`**十进制编号法**` 等小标题；
- 原始 ASR 放在 `<details>` 折叠区，正文只放分段版；
- 不要为了排版改词。

## 6. 落盘与溯源

- 所有爬取内容（视频 / 图文 / 文章）的笔记统一 → `topics/<topic>/00-收集/<标题>.md`
- 视频帧 → `eagle-cache/videos/<slug>/fNNNN.jpg`
- 图文原图 → `eagle-cache/articles/<slug>/00X.jpg`
- 不要新建 `30-视频` / `30-图文` 目录；`00-收集` 里按添加顺序编号。
- 索引更新 `topics/<topic>/index.md` 的 `<!-- tome-clip:start/end -->` 区块
- frontmatter：`title / source(canonical) / share_url / author / published / platform / stats / tags`
- `stats` 是发布时的快照，会变；保留抓取时的值即可，不要假装实时
- 旧附件目录不要自动删；换图时新建 `-v2` 目录，确认后再清理旧目录

## 7. 架构决策：单篇采集只留一个入口

**已执行（2026-11）**：`tome-video` 重命名为 `tome-clip`，做单篇采集的薄路由；
`tome-wechat` 保留批量 / 专辑 / 账号列表 / AI 阅读卡片 / 去重；
`lecture-notes` 保留长讲座 / 圆桌；`tome-portrait/spread/book` 保留成书链路。

理由：

- 用户日常动作是「看到一篇 → 存进 Obsidian」，不是「先判断这是公众号还是小红书」。
- 按平台拆技能会让 `tome-wechat`、旧的 `tome-video`、新技能抢同一个链接，触发词互相冲突。
- 旧的 `tome-video` 已经支持小红书图文，名字本身就不准了。
- 不要在第四个技能里复制转写/抽帧/OCR 引擎；`tome-clip` 只做 URL 路由和统一笔记模板，
  重活调用现有脚本。

统一入口：`python3 ~/.claude/skills/tome-clip/scripts/clip.py --url <链接>`。

路由：

| 输入 | 走法 |
| --- | --- |
| `mp.weixin.qq.com/s/...` | 调 `tome-wechat/wechat_article.py --url` |
| `xiaohongshu.com` 图文 | `xhs_note.py --url` |
| `xiaohongshu.com` 视频 | `xhs_note.py --url`（自动转 `video_note.py`） |
| `weixin.qq.com/sph/...` | `sph_meta.js` + 手动 mp4 + `video_note.py --file` |
| 本地 mp4/mov | `video_note.py --file` |
