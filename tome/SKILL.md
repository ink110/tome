---
name: tome
description: 公开资料整理成书的中枢技能（CORE）。当用户说「把这些公开资料整理成书」「把这个人做成书」「从 B站 / 公众号采集到成书」「公开资料成书」「tome」时使用。tome 先判断阶段，再调度 tome-bili / tome-wechat / tome-video / tome-portrait / tome-spread / tome-book；不跳过采集与核验直接写书。
---

# tome · 公开资料成书中枢

**CORE / 中枢**：tome 是入口和调度器，不直接采集、不直接画像、不直接排版。先判断用户在哪一步，再只加载一个子技能。

## 链路

```text
公开资料
  ├─ B站          → tome-bili   ─┐
  ├─ 微信         → tome-wechat ─┤
  └─ 小红书/视频号 → tome-video  ─┘
                                  ↓
                     tome-portrait  人物解构 / 核验 / 画像
                           ↓
                     tome-spread    传播复盘（可选）
                           ↓
                     tome-book      md + epub 成书
```

## 首次使用

先配置，再干活。没配置过就先跑这两条，不要直接开始采集：

```bash
python3 ~/.claude/skills/tome/scripts/setup.py    # 问 3 个问题：项目放哪、用不用 Obsidian、用哪个模型
python3 ~/.claude/skills/tome/scripts/doctor.py   # 检查 pandoc / Chrome / cookie / 模型
```

- 模型三档：`claude`（默认，Claude Code 直接做，无需 Key）、`openai`（兼容 DeepSeek/Kimi/GLM）、`ollama`（本地）。
- 信息源两档：`folder`（普通文件夹）或 `obsidian`（库根 + `topics/` + 双链）。
- 细节见 [references/setup.md](references/setup.md)。切换档位只改配置，不用改命令。

如果用户已经有整理好的专题目录，可以从 `tome-portrait` 或 `tome-book` 直接接上，不必重跑采集。

## 先判断阶段

| 用户意图 | 先加载 | 不要加载 |
| --- | --- | --- |
| 给 B站链接 / 要字幕 / 要播放数据 | `tome-bili` | 其余 |
| 给公众号文章 / 专辑 / 博主列表 | `tome-wechat` | 其余 |
| 给小红书图文/视频、视频号链接 | `tome-video` | 其余 |
| 要解构一个人 / 做画像 / 做核验 | `tome-portrait` | 采集类（资料已在项目里） |
| 要传播复盘 / 找选题 / 拆爆款 | `tome-spread` | 采集类 |
| 已有 md，要合并成书 / 出 epub | `tome-book` | 采集、画像 |
| 从零做一本人物书 | `tome-portrait`（它会按步骤调用其他环） | 一次加载全部 |

完整人物书顺序：`tome-bili` / `tome-wechat` / `tome-video` → `tome-portrait` → `tome-spread` → `tome-book`。G1/G2/G3 三个人工闸门在 `tome-portrait` 里，中枢不重复问。

## 目录与交接物

```text
<信息源>/<专题>/
├── 00-收集/             一手语料 + _manifest.json
├── 00-收集-外部/        外部材料
├── 10-画像/             画像 + 核验表 + _调研/
├── 20-输出/             传播案例库 / 选题清单 / 内容结构模板 / 全书
├── assets/              媒体与封面
└── progress.md          断点续跑
```

`<信息源>` 在 folder 模式是工作目录，在 obsidian 模式是库根下的 `topics/`。

两种信息源模式的目录结构完全相同，只有根目录和链接写法（`[[双链]]` vs 普通链接）不同。

跨技能只认这些交接物：

- `_manifest.json`：排序用 `(published, id)`，链接用 `source_url`。
- `progress.md`：每完成一步勾选；中断后先读它，从第一个未勾选项继续。
- 画像：`10-画像/<人物>·思维操作系统2.0.md` + `<人物>·核验表.md`。
- 书稿：`20-输出/*传播案例库*.md`、`*选题清单*.md`、`*内容结构模板*.md`。

## 常用入口

```bash
# 新人物项目（--root 不填就用配置里的信息源根目录）
python3 ~/.claude/skills/tome-portrait/scripts/new_project.py --person "<人物>"

# B站采集（先 raw-only）
python3 ~/.claude/skills/tome-bili/crawl_collection.py "<BV>" --out "<项目>/00-收集" --raw-only

# 微信采集（先枚举）
python3 ~/.claude/skills/tome-wechat/wechat_article.py --album-url "<专辑链接>" --list-only --json

# 传播复盘（有互动数据才用 rank）
python3 ~/.claude/skills/tome-spread/scripts/rank.py --manifest "<项目>/00-收集/_manifest.json"

# 成书
python3 ~/.claude/skills/tome-book/scripts/build_book.py --project "<项目>" ...
```

具体参数不要从 hub 抄；加载对应技能，读它的 SKILL.md。

## 硬规矩

1. **先枚举再抓**：公众号专辑 / 账号列表先 `--list-only`，确认专题；发现专辑先问用户要抓哪些。
2. **模型按配置走**：默认 `claude` 档位由 Claude Code 直接处理，不调用外部模型；切到 `openai` / `ollama` 后脚本才批量跑。不要在命令里硬写模型名。
3. **不估算数据**：微信不公开阅读 / 在看 / 转发数；传播复盘用代理指标，不写「10 万+」。
4. **链接只取 manifest**：文章链接用 `source_url`；不要用「下一篇」或自己拼 `chksm`。
5. **原话不改**：引语逐字；概括标「转述」；起诉 ≠ 定罪。
6. **先读 progress**：已有项目先读 `progress.md`，从第一个未勾选项继续，不重爬、不重写。
7. **最小加载**：一次只加载当前阶段需要的子技能；单个项目内技能总数 ≤ 10。

## 什么时候读哪个子技能

| 场景 | 加载 |
| --- | --- |
| 第一次用、配模型 / 配信息源 / 拿 cookie | `tome/references/setup.md` |
| B站字幕 / 合集 / 播放数据 | `tome-bili` |
| 公众号单篇 / 专辑 / 账号列表 / 阅读卡片 | `tome-wechat` |
| 小红书图文 / 小红书视频 / 视频号 / 本地视频转图文笔记 | `tome-video` |
| 人物画像、核验表、G1/G2/G3、成书流程 | `tome-portrait` |
| 无互动数据时的传播筛选 | `tome-spread/references/selection.md` 第八节 |
| 微信语料成书 staging / 转义 / `--config` | `tome-book/references/wechat-book.md` |
| 书的结构、章节模板、验收 | `tome-portrait/references/book.md` + `tome-book` |
