# 底层手工提取流程

脚本 `bilibili_to_md.py` / `bilibili_fetch.py` 坏了或要改逻辑时，按下面手工流程走。

## cookie 失效时，引导用户获取（F12 → Application → Cookies）

浏览器打开 B站视频页 → 按 `F12` 打开开发者工具 → 顶部点「应用 / Application」标签
（看不到就在最右 `»` 里找）→ 左侧「存储 / Cookies」→ 点 `https://www.bilibili.com` →
右侧表格找 `SESSDATA` 行 → 复制「值 / Value」那一格，粘贴回来。

（Safari 需先在「Safari → 偏好设置 → 高级」勾选「显示网页开发者功能」，
再走「开发 → 显示网页检查器」，切到「存储 / Storage」标签，同样找 Cookies → SESSDATA。）

拿到新的 SESSDATA 后，更新 `~/.bilibili-cookies.txt` 里的 SESSDATA 值即可继续。

## 提取流程

### 1. 解析地址
从用户输入提取 BV 号（或 av 号）。

### 2. 拿 cid + 字幕地址（用 wbi/v2，带 wbi 签名）

⚠️ **必须用 `wbi/v2`，不要用 `player/v2`**（后者对某些视频会返回错乱字幕）。

wbi 签名算法（Python）：
```python
import hashlib, time, urllib.parse
# ① 从 nav 接口拿 img_key / sub_key
nav = ...  # GET /x/web-interface/nav
img_key = nav['data']['wbi_img']['img_url'].split('/')[-1].split('.')[0]
sub_key = nav['data']['wbi_img']['sub_url'].split('/')[-1].split('.')[0]
# ② mixin 表取前 32 位
mixin = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
mkey = ''.join((img_key + sub_key)[i] for i in mixin)[:32]
# ③ 参数排序后 md5(query + mkey) 生成 w_rid
def sign(params):
    params['wts'] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    q = urllib.parse.urlencode(params)
    params['w_rid'] = hashlib.md5((q + mkey).encode()).hexdigest()
    return params
# ④ 请求
url = "https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(sign({'bvid': BVID, 'cid': CID}))
```
- `cid` 从 `view` 接口的 `data.cid` 拿。
- 返回 `data.subtitle.subtitles` 数组，取 `subtitle_url`。
- **必须用完整 URL（含 `?auth_key=...` 签名）**，否则下载 403。
- **校验**：条数应与视频时长匹配、首条内容应与标题相关，不对就重取。

### 3. 下载字幕 JSON
```bash
curl -s -b ~/.bilibili-cookies.txt -A "Mozilla/5.0" -H "Referer: https://www.bilibili.com/" \
  "<完整 subtitle_url>" -o subtitle.json
```
- `subtitle_url` 是 `//` 开头时补 `https:`。
- 字幕 JSON 的 `body` 数组，每条含 `from` / `to` / `content`。

### 4. 拿元信息（view 接口）
```bash
curl -s -b ~/.bilibili-cookies.txt -A "Mozilla/5.0" \
  "https://api.bilibili.com/x/web-interface/view?bvid=BVxxx"
```
取 `title`、`owner.name`、`duration`、`desc`。

### 5. 生成 Markdown 文档
输出到项目 `00-收集/` 目录，文件名 `<前缀><NNN>：<主题>.md`；同时落 `_raw.txt` 和 `_meta.json`。
`_meta.json` 要包含 `bvid / title / owner / pubdate / duration_sec / stat / season_id / episode`，
后面 `manifest.py` 和 `tome-spread/rank.py` 都依赖这些字段。

> 本 skill 已从 `bilibili-transcript` 改名为 `tome-bili`，脚本路径是 `~/.claude/skills/tome-bili/`。

## 命令行参数

`crawl_collection.py`：

| 参数 | 说明 |
| --- | --- |
| `--out` | 输出目录（必需，除非 `--list`） |
| `--prefix` | 文件名前缀；建议用 `<人物>` 或 `<人物>·<系列>` |
| `--start-ep` / `--end-ep` | 集号范围（含） |
| `--only` | 只跑指定集号，逗号分隔 |
| `--raw-only` | 只抓原文 + 元信息，不调模型 |
| `--distill-only` | 只对已有 `_raw.txt` 补蒸馏 |
| `--model` | 模型名，留空用配置（`models.provider`） |
| `--list` | 只打印合集清单 |

`manifest.py`：

| 参数 | 说明 |
| --- | --- |
| `--dir` | `00-收集` 目录 |
| `--out` | 输出 `_manifest.json` |
| `--coverage` | 输出 `_coverage.md`（年份分布、缺号、重复、无字幕、播放分布） |

## 蒸馏稿结构（必须遵循）

```markdown
# {标题}
> 一句话摘要

## 核心观点速览        ← 30 秒抓住要点（3–5 条）
## 分节正文            ← 按主题切块，案例保留完整叙事细节
## 案例速查表          ← 案例 | 一句话故事 | 支撑观点 | 可复用场景
## 金句摘录
## 学习记录            ← 6 类：核心观点 / 论证结构 / 案例 / 表达手法 / 争议点 / 思维地图

---
## 附：完整口播稿（带时间戳）
[mm:ss] 原文逐句……
```
