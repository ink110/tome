# 公众号专辑批量抓取

> 记录 `wechat_article.py` 的专辑枚举 / 合并 / 批量抓取实现细节。改批量逻辑前先读这里。

## 专辑 JSON 接口

```text
https://mp.weixin.qq.com/mp/appmsgalbum
  ?__biz=<biz>&action=getalbum&album_id=<album_id>
  &count=20&f=json&scene=126
  [&begin_msgid=<上页最后 msgid>&begin_itemidx=<其 itemidx>]
```

- 无需登录、无需 cookie；`__biz` 与 `album_id` 从专辑链接解析。
- 返回 `getalbum_resp`：
  - `base_info`：`title`（专辑名）、`nickname`（博主）、`article_count`、`is_reverse`、`description`
  - `article_list[]`：`msgid`、`itemidx`、`pos_num`、`title`、`url`、`create_time`
- `count` 最大 20；翻页游标是上一页最后一条的 `msgid` + `itemidx`。
- 终止条件：已收集 ≥ `article_count`，或 `continue_flag != '1'`，或本页无新增。
- **`pos_num` 是专辑内权威顺序**，按升序排；不要依赖 `is_reverse`。
- JSON 的 `url` 自带完整 `chksm`；**缺 `chksm` 会 302 到错误页**，必须原样使用。

## 必须记住的坑

1. **`continue_flag` / `article_count` 是字符串**（`'1'` / `'232'`）。按整数比较会在第一页后错误停止；一律 `str(x) == '1'` / `int(x)`。
2. **请求过快返回空页**：翻页间隔 1.2–1.5s；空页按 1.5/3/6/… 退避重试（最多 5 次）。
   - 但「`base_info` 存在且 `continue_flag != '1'`」的空页是正常结束，不重试。
3. **声明篇数可能与实际不一致**：枚举不足时警告并继续；一篇都没有才报错。
4. **没有公开接口列出某博主全部专辑**：只能从文章正文的 `album_id` 链接发现（`--discover-albums`，默认抽样 30 篇）。
5. **全量历史拿不到**：`profile_ext?action=getmsg` 无登录返回 `{"ret":-3,"errmsg":"no session"}`。需要全量时让用户提供导出链接列表走 `--list`。

## 合并与去重

- 去重键：长链 `mid:idx`，短链 `/s/<token>`。
- 同一篇可属于多个专辑，保留全部 `albums[]` 归属。
- **主专辑 = 篇数最少的归属**（最具体），例：KOL 10 → 功能解读 20 → 小红书 232。
- 合并顺序：专辑按篇数升序，各自按 `pos_num` 升序；`--start` / `--limit` 按此顺序切片。

## 笔记与索引

笔记 frontmatter（仅批量）：

```yaml
album: "小红书功能解读"
album_id: "2160301625171558401"
album_order: 20
album_url: "https://mp.weixin.qq.com/mp/appmsgalbum?..."
albums:
  - name: "小红书功能解读"
    id: "2160301625171558401"
    order: 20
    url: "https://..."
```

- 笔记文件名：`safe_name(title, LINK_ILLEGAL)[:60]`，避免 `[]#^|` 破坏 wikilink，并控制 APFS 255 字节上限。
- 缓存 stem 同源截断 60 字；同批重名追加 `-<key 后 6 位>`。
- 索引 `index.md` 每个专辑一个区块，重复运行只替换：

```markdown
<!-- wechat-album:<album_id>:start -->
### 专辑：<专辑名>（<声明篇数> 篇）

- 已收集 <实际>/<声明>

| # | 标题 | 发布 | 来源 |
| --- | --- | --- | --- |
| 1 | [[真实笔记 stem]] | 2023-09-08 | [公众号](...) |
<!-- wechat-album:<album_id>:end -->
```

- 行按 `pos_num` 升序；标题里的 `|` 转义为 `\|`；wikilink 用磁盘上真实文件名 stem。
- 索引从磁盘扫描 `00-收集/*.md` 生成，因此补跑 / 部分失败后会自动补齐，不会留下指向不存在笔记的链接。

## 批量执行与退出码

- 扫描目标专题 `00-收集/*.md` 的 `source`，按去重键跳过已存在；`--force` 原地覆盖。
- Eagle 预检一次（`eagle_library_root` + `resolve_or_create_folder`），失败直接 exit 2。
- 串行逐篇，篇间 `--interval`（默认 1.5s）；单篇失败记入清单继续。
- 结束后一次性重建索引；打印成功 / 跳过 / 失败 / 媒体统计。

| exit | 含义 |
| ---: | --- |
| 0 | 全部成功 / 全部跳过 |
| 1 | 枚举失败、没有可处理文章、参数错误 |
| 2 | Eagle 预检失败 |
| 3 | 部分文章失败，或媒体有失败，或部分专辑枚举失败 |
| 4 | 全部文章失败 |
| 130 | Ctrl-C 中断（已完成进度保留，重跑续抓） |

## 账号列表模式

```bash
# 只读盘点：分开文章链接与专辑链接
... --list links.txt --list-only --json

# 没有专辑链接时，从正文发现（只读；必须配 --list-only）
... --list links.txt --list-only --json --discover-albums --discover-sample 30
```

- `--discover-albums` 只允许和 `--list-only` 一起用：**先发现、让用户选，再抓**。
- `--discover-sample 0` = 全量扫描（593 篇约 2 GB 流量，默认只抽 30 篇）。
- 纯文章链接列表可直接 `--list links.txt --topic <专题>` 抓取。

## 视频转写（交给 tome-clip）

抓取阶段只负责把腾讯视频 mp4 下到 `assets/`，不自动转写。批量结束后：

1. 列出 `assets/` 里的 `*.mp4`，让用户选需要转成图文笔记的（不要默认全转，转写很慢）。
2. 对选中的文件跑 `tome-clip`：
   ```bash
   python3 ~/.claude/skills/tome-clip/scripts/video_note.py \
     --file "<专题>/assets/<视频>.mp4" --title "<标题>" --source-url "<原文章链接>"
   ```
3. 产物统一放 `topics/<topic>/30-视频/`，索引增加 `<!-- tome-clip:start/end -->` 区块。
4. 长讲座 / 圆桌录像改用 `lecture-notes` 的视频流程。

## 批量产物

- 笔记：`<专题>/00-收集/<标题>.md`；frontmatter 在单篇字段外增加 `album` / `album_id` / `album_order` / `album_url` / `albums[]`。
- 索引：`<专题>/index.md`；每个专辑一个 `<!-- wechat-album:<id>:start/end -->` 区块，重复运行只替换区块。
- 目录：批量自动建 `00-收集` / `10-笔记` / `20-输出` / `assets`。
- 后续做画像 / 成书时：用 `_manifest.json` 的 `(published, id)` 排序，用 `source_url` 做唯一可信链接；`00-收集/` 是文章标题不是 `NNN：`，直接跑 epub 会漏文章，先按 `tome-book/references/wechat-book.md` staging。
