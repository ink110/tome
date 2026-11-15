# 批量后处理：素材去重

批量抓取结束后由 `wechat_article.py` 自动调用 `post_batch_dedupe.py`。目标是同一图片 / 视频 / GIF 在 Obsidian 只存一份，Eagle 的重复交给 Eagle 自己处理。

## 分工

| 位置 | 处理 | 自动化程度 |
|---|---|---|
| Obsidian `eagle-cache/` | 按内容哈希去重，重复文件进废纸篓 | 全自动 |
| Eagle 资源库 | 只统计 + 打开扫描窗口 | 只提醒，不合并 |

Eagle 的「扫描重复的文件」在菜单 **查找** 里，`osascript` 可以打开；但扫描窗口是 Electron 内部 UI，后续「扫描全部 / 扫描相同的文件 / 合并」没有公开 API，合并又不可逆，所以不自动点。

## Obsidian 去重算法

1. 遍历 vault 下所有名为 `eagle-cache` 的目录（含文章子目录里的局部缓存），只处理 ≥1KB 的文件。
2. 先按 `(size, ext)` 分组，只对同组文件算 MD5；`(MD5, ext)` 相同才算重复。
3. 每组选保留副本：被引用次数最多 → 文件名最短 → 字典序。
4. 把所有 Markdown 里的 `eagle-cache/<重复名>`、`[[重复名]]`、`[[重复名|...]]` 改到保留副本。
5. 确认没有任何引用残留后，才用 `/usr/bin/trash` 把重复文件移到废纸篓；有残留则跳过并在 JSON 里报告。
6. 有改动时先打包备份全部 Markdown 到 `.backups/vault_md_<时间戳>.tgz`。

## Eagle 重复统计

- 读 `images/*.info/metadata.json`，跳过 `isDeleted`。
- 用元数据里的 `name` + `ext` 定位原文件，先按 `(size, ext)` 粗筛，再算 MD5。
- 分两类报告：
  - **完全重复**：MD5 相同，可直接合并；
  - **疑似重复**：同名同大小但 MD5 不同（差异常从 512KB 边界开始，是并发下载写坏的副本，Eagle 的感知扫描能识别）。
- 有任一类时输出组数、多余个数、可回收空间和样例，并尝试打开 Eagle 扫描窗口。

## 常用命令

```bash
# 手动补跑（默认执行）
python3 ~/.claude/skills/tome-wechat/post_batch_dedupe.py

# 只看统计，不动文件
python3 ~/.claude/skills/tome-wechat/post_batch_dedupe.py --dry-run

# 不检查 Eagle / 不弹窗
python3 ~/.claude/skills/tome-wechat/post_batch_dedupe.py --no-eagle --no-open-eagle

# 抓取时跳过整个后处理
python3 ~/.claude/skills/tome-wechat/wechat_article.py ... --no-post-dedupe
```

## 注意

- 去重只处理媒体文件；同名但内容不同的文件不会被合并。
- 删除走废纸篓，可从废纸篓恢复；改引用前已有 Markdown 备份。
- 若某篇笔记引用的是文章自己目录下的局部 `eagle-cache/`，Obsidian 按路径后缀解析，属于正常情况，不要当成断链。
