# mini-demo · 三讲成一本书

一个完全虚构的示例项目，用来验证 tome 的**成书链路**能不能在你机器上跑通。

- 人物「林舟」是虚构的，三篇口播稿是演示数据，链接指向 `example.com`，不联网。
- 目录结构与真实项目一致：`00-收集/` 放语料，`10-画像/` 放画像，`20-输出/` 放成品。
- 这里不演示采集（那一步需要 B站 cookie 或公众号链接），只演示「已有语料 → epub」。

## 跑通成书

在仓库根目录执行：

```bash
python3 ~/.claude/skills/tome-book/scripts/build_book.py \
  --config examples/mini-demo/book.json \
  --out ./mini-demo.epub
```

看到「全部通过」后，`mini-demo.epub` 可以直接拖进微信读书。

不指定 `--config` 也可以，构建器会自动识别目录：

```bash
python3 ~/.claude/skills/tome-book/scripts/build_book.py \
  --project examples/mini-demo \
  --title "林舟 · 思维操作系统" --out ./mini-demo.epub
```

## 跑通阅读卡片

示例笔记是「已采集但未提炼」的状态。用 Claude 档位导出任务：

```bash
python3 ~/.claude/skills/tome-wechat/wechat_distill.py \
  --dir examples/mini-demo/00-收集 --plan
```

会生成 `_distill-plan.json`。让 Claude 按里面的 `prompt_system` 为每篇写一份 JSON，
放进 `results` 数组后用 `--apply` 回写。切到 `openai` 或 `ollama` 档位时，同一条命令会直接跑完。

## 目录

```text
mini-demo/
├── 00-收集/
│   ├── 001：用户说的需求不是真需求.md
│   ├── 002：把复杂留给系统.md
│   ├── 003：一次失败的改版复盘.md
│   └── _manifest.json
├── 10-画像/
│   └── 林舟·思维操作系统.md
└── book.json
```
