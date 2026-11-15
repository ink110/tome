# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

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
