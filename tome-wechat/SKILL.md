---
name: tome-wechat
description: 抓取微信公众号**专辑 / 博主文章列表 / 批量**（文字 + 图片/GIF/视频）生成 Markdown 笔记，媒体存项目 assets/（可选进 Eagle）；可用 Claude、在线 API 或本地模型生成阅读卡片、标签与去重报告。当用户给 appmsgalbum 专辑链接，或说「抓这个专辑/合集」「批量抓这个博主」「账号列表」「AI 摘要」「阅读卡片」「提炼」「打标签」「去重」时使用。**单篇文章请优先用 tome-clip。**
---

# tome-wechat · 微信文章 / 专辑 → Markdown

两个模式：单篇；专辑 / 账号列表批量。批量永远**先枚举、判断专题、必要时问用户，再抓**。

单篇剪藏优先走 `tome-clip`（自动识别公众号 / 小红书 / 视频号）；本技能保留单篇脚本，兼容直接调用和批量流程复用。

配置（媒体存哪、用哪个模型）由 `~/.config/tome/config.json` 决定，见 `tome/references/setup.md`。

## 单篇

```bash
python3 ~/.claude/skills/tome-wechat/wechat_article.py --url <文章链接> \
    [--topic <专题名>] [--dry-run]
```

- 默认专题 `素材收集`；笔记写 `<专题>/00-收集/`，并更新该专题 `index.md`。
- 媒体默认存 `<专题>/assets/`；启用 Eagle 时额外上传一份并保留 `eagle://` 溯源。
- `--dry-run`：只解析预览，不下载 / 上传 / 写文件。

### 图片消息（无正文纯图帖）

单篇 exit 1「js_content 为空」但浏览器能打开 → 大概率是图片消息（纯图卡片）。处理：**解析 `picture_page_info_list` 取原图 → 下载进 assets → 视觉模型逐字 OCR → 图在上文字在下组装笔记**。完整流程见 [references/image-post.md](references/image-post.md)，不要当成「已删除」直接放弃。

### 文章内视频 → 图文笔记

`wechat_article.py` 已经会解析腾讯视频 iframe 的 `videoPageInfos`，取最高清 mp4 下载到 `assets/`（带 `Referer`），笔记里以 `![[assets/xxx.mp4]]` 引用。**抓取只到「拿到 mp4」，转写和图文笔记交给 `tome-clip`**：

```bash
python3 ~/.claude/skills/tome-clip/scripts/video_note.py \
  --file "<专题>/assets/<视频文件>.mp4" \
  --title "<文章标题>" --source-url "<文章链接>"
```

跑完后按 `tome-clip` 的组装规则产出 10–25 张关键帧 + 图注 + 口播全文，落到 `topics/<topic>/30-视频/`。注意：

- **不要再拿文章 URL 重下一次**：本地 mp4 就是最高清流，重复请求可能 403。
- 只在用户明确要「视频转笔记」时跑；转写 + 视觉描述耗时较长，批量抓取时不要自动触发。
- 长讲座 / 圆桌录像不用 `tome-clip`，用 `obsidian-lecture-notes` 的 `video_prepare.py`（按幻灯片稳定帧和 OCR 优化）。

## 专辑 / 账号列表

```bash
# 1) 先枚举，看内容（不抓、不写）
python3 ~/.claude/skills/tome-wechat/wechat_article.py \
    --album-url <专辑链接> [--album-url ...] [--list 文件] --list-only --json

# 2) 确认专题名后正式抓
python3 ~/.claude/skills/tome-wechat/wechat_article.py \
    --album-url <专辑链接> [--album-url ...] --topic <专题名>
```

- `--album-url` 可重复；`--list FILE` 每行一个文章或专辑链接（`#` 注释）。
- 批量模式 `--topic` **必填**，强制先做内容判断；单篇默认 `素材收集`。
- 默认按 frontmatter `source` 的 `mid:idx` 跳过已存在，`--force` 才覆盖；中断后重跑自动续。
- 逐篇串行，单篇失败不中断，结束汇总失败清单。
- 常用参数：`--limit N`（冒烟）、`--start N`、`--only-album <id>`、`--interval 1.5`、`--dry-run`。

## 必须先问用户的两种情况

1. **给的是专辑链接**：先 `--list-only --json`，看专辑名 / 作者 / 篇数 / 全部标题与发布时间 → 分析主题 → 对照已有专题。明确匹配就复用；主题清晰且无匹配可建议新名字；有歧义用 AskUserQuestion 让用户选。确认后才带 `--topic` 抓。
2. **给的是博主账号列表**：先 `--list <文件> --list-only --json`。列表里没有专辑链接时，可加 `--discover-albums`（抽样 30 篇正文找 `album_id`，只读）。**只要发现专辑，先列出专辑名 / 篇数 / 链接，问用户要抓哪些，不要自动开跑。**

专题判断标准：按**内容主题**归并，不按博主或平台机械命名；一个专题要能容纳后续同类内容（包括视频转写）。

## AI 提炼（三档模型）

抓取完成后为每篇生成「阅读卡片」，插到正文之前。档位见 `tome/references/setup.md`。

```bash
# claude 档位（默认）：导出任务 → Claude 写结果 → 回写
python3 ~/.claude/skills/tome-wechat/wechat_distill.py --topic <专题名> --plan
python3 ~/.claude/skills/tome-wechat/wechat_distill.py --topic <专题名> --apply _distill-plan.json

# openai / ollama 档位：直接批量跑（自动跳过已提炼；中断可续）
python3 ~/.claude/skills/tome-wechat/wechat_distill.py --topic <专题名>
... --topic <专题名> --limit 1 --no-dedup
... --topic <专题名> --force
... --topic <专题名> --dedup-only
```

最终笔记结构（原文不动，放在最后）：

```text
> 一句话摘要
## 核心观点速览（3–5 条）
## 案例速查表
## 金句摘录（原文逐字）
## 学习记录（6 类：核心观点 / 论证结构 / 案例 / 表达手法 / 争议点 / 思维地图）
## 标签
---
## 正文（原文）
```

- 生成结果用 `<!-- wechat-ai:start/end -->` 包裹；`--force` 只替换该区块。
- 标签存 frontmatter `ai_tags`，不覆盖原 `tags: [clippings]`；正文标签不带 `#`，不污染标签面板。
- 去重先做标题/正文相似度候选，再逐对判断「内容重复 / 同主题系列 / 主题相关」；结果写 `00-收集/_dedup.md`，**只标记、不删除**。
- 汇总报告写 `00-收集/_ai-report.md`，`index.md` 自动增加「AI 提炼」入口。
- 改笔记前自动备份到 `~/.local/state/tome/backups/`（可用配置 `backups.dir` 改）。

## 批量后处理（自动）

每批抓取成功结束后自动跑 `post_batch_dedupe.py`：

- **媒体目录**：所有 `assets/`（含旧的 `eagle-cache/`）内按内容哈希去重，引用改到保留副本，重复文件移入备份目录（改前自动备份笔记）。
- **Eagle**（仅启用时）：只统计重复，并尝试打开「查找 > 扫描重复的文件」窗口；**绝不自动合并或删除**。
- 手动补跑：`python3 ~/.claude/skills/tome-wechat/post_batch_dedupe.py [--dry-run]`；抓取时加 `--no-post-dedupe` 跳过。

批量产物（文件名、frontmatter、索引区块）见 [references/album-batch.md](references/album-batch.md)。

## 边界（诚实）

- **无登录只能抓专辑内文章**，拿不到博主全量历史（`profile_ext?action=getmsg` 返回 `no session`）；全量请用户提供导出列表走 `--list`。
- **单篇**：验证页 / 已删除 / 需登录 → exit 1（先查 `picture_page_info_list` 排除图片消息）；正文解析为空 → exit 3，不写任何文件。收到 exit 3 必须停下转达用户，不要自行换参重试。
- **批量**：exit 0 全成功 / 全跳过，1 枚举失败，2 Eagle 预检失败，3 部分失败或媒体失败，4 全失败，130 Ctrl-C。
- 媒体量大：200+ 篇会产生数千张图、占 0.5–1 GB，串行 1–2 小时。
- **微信不公开阅读 / 在看 / 转发数**：不要估算，用 `tome-spread/references/selection.md` 的代理指标。

踩坑清单见 [references/wechat-parsing.md](references/wechat-parsing.md)。

## 输出约定

- 笔记 frontmatter：`title` / `source` / `author` / `published` / `created` / `description` / `tags: clippings`。
- 提炼后额外增加 `ai_distilled` / `ai_model` / `ai_summary` / `ai_topic_key` / `ai_content_type` / `ai_tags`，不改原字段。
- 媒体真源在 `assets/`，开着 Eagle 时另有副本；删除原件会断 `eagle://` 溯源。

## 参考

- [references/ai-distill.md](references/ai-distill.md) — 阅读卡片结构、模型参数、标签规范、去重算法、备份与恢复
- [references/album-batch.md](references/album-batch.md) — 专辑 JSON 接口、分页、去重、索引模板、账号列表边界、退出码
- [references/wechat-parsing.md](references/wechat-parsing.md) — HTML 解析踩坑（懒加载 / 追踪像素 / 视频 iframe / 发布时间兜底 / 反爬判断）
- [references/image-post.md](references/image-post.md) — 图片消息识别、cgiDataNew 结构、OCR 流程
- [references/eagle-api.md](references/eagle-api.md) — Eagle API 端点与返回结构（可选）
