# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

### 新增

- `tome-video` 技能：小红书视频 / 微信视频号 / 本地视频 → 口播转写 + 场景抽帧 + 本地视觉描述 + 时间对齐，输出可组装成 Obsidian 图文笔记的素材包。
- 小红书图文笔记：`xhs_note.py` 用移动端 SSR 解析元数据、按原顺序取图、qwen3-vl 逐字 OCR；视频笔记自动转交 `video_note.py`。
- 小红书笔记同时保留 canonical 链接和带 `xsec_token` 的分享链接，token 过期后仍可溯源。
- 视频号元数据抓取（`sph_meta.js`）：标题、作者、发布时间、点赞/收藏/转发/评论。
- 补齐 `tome-video` 的 `references/xhs.md`、`references/pipeline.md`、`references/sph-capture.md`。
- `install.sh` 和 README 登记 `tome-video`；`tome` 中枢增加小红书 / 视频号 / 本地视频的采集路由。
- `tome-wechat` 增加「文章内视频 → tome-video」的交接说明，视频转写不再标记为未实现。

### 修复

- 口播型视频走均匀采样兜底时，帧文件从 `f0000` 开始编号，后续重命名互相覆盖，最终只剩 1 帧。改为从 `f0001` 开始并两阶段重命名。

### 已知边界

- 视频号视频流无法匿名下载，mp4 需手动投送。
- 小红书**图文笔记**（无视频）尚未支持。

## [1.0.2] - 2026-11-15

### 变更

- `install.sh` 默认改为逐技能软链（Claude Code 官方支持），`git pull` 后开新会话即生效；需要独立副本时加 `--copy`。
- 安装脚本增加「在技能目录内运行」的保护，避免把仓库复制到自己身上。

### 修复

- 安装脚本中变量紧邻中文标点时被解析成未定义变量的问题。

## [1.0.1] - 2026-11-15

### 修复

- `build_book.py` / `make_cover.py` 的输出路径改为绝对路径。之前传相对路径（如 `--out ./book.epub`）会因为构建过程切换工作目录而写错位置。
- 媒体去重适配项目内 `assets/`：只在同一个媒体目录内合并，引用改写保留原前缀，不再假设旧的 `eagle-cache/` 结构。

## [1.0.0] - 2026-11-15

首个公开版本。

### 新增

- 三个模型档位：`claude`（默认，Claude Code 直接处理）、`openai`（任意 OpenAI 兼容接口）、`ollama`（本地）。
- 两种信息源模式：普通文件夹、Obsidian 库。目录结构一致，只切换根目录与链接写法。
- `tome/scripts/setup.py` 首次配置向导、`tome/scripts/doctor.py` 环境预检。
- `examples/mini-demo/`：可离线跑通的三讲成书示例。
- 公众号提炼在 `claude` 档位下的任务导出 / 回写（`wechat_distill.py --plan` / `--apply`）。
- Eagle 改为可选；不启用时媒体存到项目 `assets/`。

### 变更

- 移除了所有硬编码的个人路径、模型名、真实 BV 号与示例人物。
- 备份目录从技能目录内改到 `~/.local/state/tome/backups/`（可用配置修改）。
- pandoc、Chrome 改为自动探测，仍可用 `--pandoc` / 配置覆盖。

### 移除

- 两个一次性脚本 `legacy_build.py`、`legacy_post.py`。
- 仓库内不包含任何抓取到的文章、字幕或媒体。

### 安全

- API Key 只从环境变量读取。
- 抓取内容、cookie 与本地备份全部在 `.gitignore` 里。
