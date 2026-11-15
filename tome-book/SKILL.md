---
name: tome-book
description: tome 成书环。把一个 Obsidian 项目合并成一本可进微信读书的 epub，同时产出内容一致的合并 md。当用户说「做成书」「导出 epub」「打包成书」「合并成一本」「阅读优化」「生成阅读版」「转成电子书」「总纲+逐集合成书」时使用；也用于 tome-portrait 项目的最后一步成书。
---

# tome-book · 成书

一个内容源，两种格式：

- **Obsidian md**：全文合并，给检索、摘录、写作用。
- **EPUB**：阅读优化版，给微信读书通读。

两边内容一致，只是排版和口播稿处理不同：md 默认保留完整时间戳（检索原话），epub 默认分段降噪（通读）。正文、案例、结论、金句完全相同。

## 最快的一条命令

人物画像项目已经有固定目录，直接用包装脚本：

```bash
python3 ~/.claude/skills/tome-portrait/scripts/make_book.py \
  --project "<项目目录>" \
  --subtitle "一个连续创业者的决策、叙事与自我证明" \
  --cover "/path/cover.png"
```

产出：

- `<输出目录>/<书名>（阅读版）.epub`
- `<项目>/20-输出/<书名>（全书）.md`

不给 `--cover` 时，先找 `<项目>/assets/cover.png`；再没有就自动生成一张 1200×1800 封面并保存到那里，下次直接复用。完全不要封面加 `--no-cover`。

### 想单独生成/换风格

```bash
python3 ~/.claude/skills/tome-book/scripts/make_cover.py \
  --title "<人物>·思维操作系统 2.0" \
  --subtitle "<一句话副标题>" \
  --author "<人物> 口述" \
  --out "<项目>/assets/cover.png"
```

1200×1800，米色底、宋体大标题、红色短线。要换风格就改 `assets/cover.html`，不要改生成器。

## 直接调用构建器

非人物画像项目，或要精细控制：

```bash
python3 ~/.claude/skills/tome-book/scripts/build_book.py \
  --project "<项目根>" \
  --title "<书名>" \
  --subtitle "<一句话副标题>" \
  --author "<人物>" --editor "<整理者>" \
  --cover "<封面路径>" \
  --out "<输出目录>/书.epub" \
  --md-out "<项目>/20-输出/书（全书）.md"
```

| 参数 | 说明 |
| --- | --- |
| `--mode reading` | epub 默认。口播稿分段、弱化时间戳 |
| `--mode archive` | epub 归档。保留完整时间戳 |
| `--md-mode archive` | md 默认。保留完整时间戳，方便检索原话 |
| `--md-mode reading` | md 也用分段后的口播稿 |
| `--config book.json` | 手动指定篇章结构，不走自动识别 |
| `--no-cover` | 不生成封面（校验会报缺封面，除非你就不要） |
| `--work` / `--keep-work` | 指定/保留中间目录，排查问题时用 |
| `--no-md` | 只出 epub |
| `--no-validate` | 跳过自动校验 |

## 自动识别的目录

```
<项目>/
├── 10-画像/<人物>·思维操作系统*.md   → 000 · 总纲
├── 00-收集/*NNN：*.md                → 第一篇 · 逐集案例
├── 00-收集-外部/*外部NNN：*.md       → 第二篇 · 外部素材
├── 20-输出/*传播案例库*.md           → 第三篇 · 传播案例（可选）
├── 20-输出/*选题清单*.md             → 附录 F · 选题清单（可选）
└── 20-输出/*内容结构模板*.md         → 附录 G · 内容结构模板（可选）
```

集号、外部编号用 `re.search`，文件名前面可以有任意前缀。缺哪一部分就跳过哪一部分，不会硬凑。所有以「附录」开头的章节都会提升为顶层目录项，按原顺序排列。

**微信文章不是这套命名**：文件名是文章标题，正文含 Eagle 图片和裸 `<`。直接 `--project` 会漏掉全部文章；先按 `references/wechat-book.md` staging 成编号稿，再用 `--config` 构建。

阅读版做了哪些排版处理、常见报错怎么排查，见 [references/reading-rules.md](references/reading-rules.md)。

## 校验

构建完会自动跑：

```bash
python3 ~/.claude/skills/tome-book/scripts/validate.py "/path/book.epub"
```

检查：mimetype、container、OPF、manifest 文件齐全、spine、XML 可解析、nav/NCX 链接、书内锚点、每章 title、残留表格、封面/扉页/篇章页。

看到「全部通过」再交付。想单独复查也可以直接跑这条。

## 依赖

- pandoc 3.x：自动探测（配置 `tools.pandoc` → `~/tools/obsidian-epub/pandoc` → Homebrew → PATH），也可以用 `--pandoc` 指定
- `zip`（macOS 自带）
- Python 3.9+
- Google Chrome：只在自动生成封面时需要，也可以用 `--cover` 指定现成图片

跑 `python3 ~/.claude/skills/tome/scripts/doctor.py` 会逐项检查。

## Where to go next

| 场景 | 读 |
| --- | --- |
| 调整排版、表格、口播稿规则 | `references/reading-rules.md` |
| 微信文章批量成书（staging、转义、config） | `references/wechat-book.md` |
| 只想快速把一篇 md 转 epub | `build_epub.py`（旧版单文件构建器） |
| 人物画像全流程 | `tome-portrait` skill |
| 传播案例/选题怎么写 | `tome-spread` skill |
