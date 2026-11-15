# 微信语料成书（staging）

> 适用：`00-收集/` 里是公众号文章标题，不是 `NNN：主题.md`。直接用 `--project` 自动识别会漏掉全部文章。

## 为什么不能直接构建

微信笔记和口播稿的假设不一样：

- 文件名是文章标题，没有集号；自动识别只认 `NNN：`。
- 正文含 `![[...]]`、`[Eagle](eagle://...)` 和大量配图，epub 会产生失效图。
- 正文可能出现第二个 H1、`<滑动查看下一张图片>`、裸 `<`。
- frontmatter 的 `source` 是文章 URL，构建器会把它当视频注入「原视频」链接。

所以先 staging 成书稿，再用 `--config` 显式指定 parts。

## 五步 staging

1. **排序**：读 `00-收集/_manifest.json`，按 `(published, id)` 升序，复制成 `001.md`–`NNN.md`。不要按文件名或抓取顺序。
2. **去 source**：从前言删除 `source:` 行，避免注入错误的「原视频」链接。
3. **去媒体**：删除 `![[...]]`、`[Eagle](eagle://...)`，清掉只剩 `**` 的空行。阅读版是纯文本版；配图仍在 Obsidian 原笔记和 Eagle。
4. **转义**：先把 `<滑动查看下一张图片>` 换成中文括号，再把所有裸 `<` 换成 `&lt;`。顺序反了会在合并 md 里留下 `&lt;`。不要用注释掉报错的方式绕过。
5. **H1**：每篇只保留第一个 H1（阅读卡片标题），后续 H1 降成 H3。

最小实现可参考：

```python
body = re.sub(r"!\[\[[^\]]*\]\]", "", body)
body = re.sub(r"\[Eagle\]\(eagle://[^)]*\)", "", body)
body = re.sub(r"^[ \t]*\*{2,}[ \t]*$", "", body, flags=re.M)
body = body.replace("<滑动查看下一张图片>", "（滑动查看下一张图片）")
body = body.replace("<", "&lt;")
```

## book.json 的 parts

顺序固定：总纲 → 第一篇逐篇案例 → 第二篇外部素材 → 第三篇传播案例 → 附录 F → 附录 G。

```json
{"kind": "main",     "title": "总纲", "items": [...]}
{"kind": "chapters", "title": "第一篇 · 逐篇案例", "part_page": true, "items": [...]}
{"kind": "chapters", "title": "第二篇 · 外部素材", "part_page": true, "items": [...]}
{"kind": "spread",   "title": "第三篇 · 传播案例", "part_page": true, "items": [...]}
{"kind": "topics",   "title": "附录 F · 选题清单", "items": [...]}
{"kind": "topics",   "title": "附录 G · 内容结构模板", "items": [...]}
```

- 篇章页由 `part_page: true` 生成；附录不要篇章页。
- `kind: "spread"` 会把每个 `# Sxx` 当一章；`kind: "topics"` 会把附录正文的 H1 降级，只保留 part 标题为 H1。
- 有多个附录时，标题必须以「附录」开头。构建器会把所有「附录*」提升为顶层目录项，并按原顺序排列。

## 构建命令

```bash
python3 ~/.claude/skills/tome-book/scripts/build_book.py \
  --config "$JOB/tmp/book.json" \
  --mode reading --md-mode archive \
  --work "$JOB/tmp/epub-work"
```

`--work` 放本地临时目录，避免 iCloud / 中文路径在 zip 阶段冲突。

## 验收

- [ ] 章节数 = 总纲 1 + 文章 N + 外部 N + 传播 N + 附录 F 1 + 附录 G 1。
- [ ] 合并 md 中 `![[`、`eagle://`、`&lt;` 计数都是 0。
- [ ] 每篇文章只有一个 H1；原文内的小标题是 H3。
- [ ] 全部 XML 可解析；`mimetype` 是 zip 第一条且 store。
- [ ] 目录里附录 F、G 是顶层，顺序正确，没有嵌进第三篇。
- [ ] 封面、扉页、3 个篇章页都在 spine。

## 已知取舍

- 阅读版不含原文配图。要配图，另做图文版，不要把 Eagle 链接直接塞进 epub。
- 微信不公开阅读 / 在看 / 转发数；传播篇只能用代理指标，见 `tome-spread/references/selection.md`。
- 文章 URL 以 `_manifest.json` 的 `source_url` 为准，不要用「下一篇」链接或自己拼接 `chksm`。
