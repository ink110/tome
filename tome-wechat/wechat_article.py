#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_article.py — 一篇微信公众号文章 → Markdown 笔记 + 媒体

整条管线：抓原文 → 抽文字 + 图片/GIF/视频 → 下载到项目 assets/ →
（可选）传 Eagle → 写笔记（内联图片）→ 更新专题 index.md。

用法:
  python3 wechat_article.py --url <微信文章链接> [--topic <专题名>] [--dry-run]

依赖: 仅 Python3 标准库（urllib + html.parser），无需 pip install。
配置: 路径和模型都由 ~/.config/tome/config.json 决定，先跑 tome/scripts/setup.py。
前置: 只在启用 Eagle 时需要打开 Eagle。
"""
import sys, os, re, json, time, html as html_mod, subprocess, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser
from datetime import date, datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

DEFAULT_TOPIC = "素材收集"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
REFERER = "https://mp.weixin.qq.com/"

FS_ILLEGAL = r'[\\/:*?"<>|\n\r\t]'            # 文件系统非法字符
LINK_ILLEGAL = r'[\\/:*?"<>|\n\r\t\[\]#^|]'  # 追加 wikilink 特殊字符（缓存文件名用）

VOID_TAGS = {"img", "br", "hr", "input", "source", "wbr", "embed", "meta", "link",
             "area", "base", "col", "track"}
SKIP_TAGS = {"script", "style", "noscript", "template"}

ALBUM_PAGE_SIZE = 20       # 专辑 JSON 接口每页最多 20 条
ALBUM_PAGE_DELAY = 1.2     # 翻页间隔，过快会返回空页
ALBUM_MAX_PAGES = 50


class WechatError(Exception):
    """可预期的业务失败（不打印 traceback）。"""


class ArticleError(WechatError):
    """单篇抓取/校验失败。"""


class EmptyBodyError(WechatError):
    """取到 js_content 但正文解析为空（沿用 exit 3）。"""


class AlbumError(WechatError):
    """专辑枚举失败。"""


# ---------------------------------------------------------------- 小工具
def collapse(s):
    return re.sub(r"\s+", " ", s)


def strip_tags(s):
    return collapse(html_mod.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def safe_name(text, illegal=FS_ILLEGAL):
    t = re.sub(illegal, "", text or "").strip()
    t = collapse(t).strip(" .")
    return t or "untitled"


def parse_date(text):
    """从「2026年10月1日 16:48」或「2026-10-01 16:48」里取 YYYY-MM-DD，失败返回原串。"""
    t = (text or "").strip()
    m = re.search(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})", t)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return t


# ---------------------------------------------------------------- Eagle API
def _http_json(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _eagle_base():
    return str(tc.eagle_settings().get("base_url") or "").rstrip("/")


def eagle_get(path):
    req = urllib.request.Request(_eagle_base() + path,
                                 headers={"Accept": "application/json"})
    return _http_json(req, 15)


def eagle_post(path, body, timeout=600):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(_eagle_base() + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return _http_json(req, timeout)


def eagle_library_root():
    d = eagle_get("/library/info")
    if d.get("status") != "success":
        raise RuntimeError("Eagle 未运行或 API 异常，请先打开 Eagle（端口 41595）")
    return ((d.get("data") or {}).get("library") or {}).get("path")


def _flatten_folders(nodes, prefix=""):
    out = []
    for f in nodes:
        path = f"{prefix}/{f['name']}" if prefix else f["name"]
        out.append((path, f.get("id"), f.get("name")))
        out.extend(_flatten_folders(f.get("children") or [], path))
    return out


def resolve_or_create_folder(name):
    d = eagle_get("/folder/list")
    if d.get("status") != "success":
        raise RuntimeError("无法列出 Eagle 文件夹")
    for path, fid, fname in _flatten_folders(d.get("data") or []):
        if fname == name and path == name:  # 顶层同名
            return fid
    c = eagle_post("/folder/create", {"folderName": name})
    if c.get("status") == "success" and isinstance(c.get("data"), dict) and c["data"].get("id"):
        return c["data"]["id"]
    raise RuntimeError(f"无法创建 Eagle 文件夹: {name}")


def eagle_add_from_path(local_path, name, folder_id):
    body = {"path": local_path, "name": name}
    if folder_id:
        body["folderId"] = folder_id
    d = eagle_post("/item/addFromPath", body)
    if d.get("status") != "success" or not isinstance(d.get("data"), str):
        raise RuntimeError(f"Eagle 上传失败: {d.get('message') or d}")
    return d["data"]  # itemId


# ---------------------------------------------------------------- 抓取 + 元数据
def _retryable(exc):
    """只重试瞬时网络问题；403/404 重试无意义。"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (429, 500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _with_retry(fn, retries=3, base_delay=1.0):
    last = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if not _retryable(exc) or attempt == retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise last


def fetch_html(url, retries=3):
    def once():
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": REFERER,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    return _with_retry(once, retries=retries)


def extract_metas(html):
    metas = {}
    for m in re.finditer(r"<meta\b([^>]*)>", html):
        attrs = m.group(1)

        def get(k):
            mm = re.search(r'\b%s=["\']([^"\']*)["\']' % k, attrs)
            return html_mod.unescape(mm.group(1)) if mm else None

        key = get("property") or get("name")
        val = get("content")
        if key and val:
            metas[key] = val
    return metas


def extract_js_content(html):
    """定位 <div id="js_content"> 并按 div 括号配对截取其内层 HTML。"""
    m = re.search(r'<div[^>]*\bid="js_content"[^>]*>', html)
    if not m:
        return None
    start, depth, i = m.end(), 1, m.end()
    while i < len(html) and depth > 0:
        op = html.find("<div", i)
        cl = html.find("</div>", i)
        if cl == -1:
            break
        if op != -1 and op < cl:
            depth += 1
            i = op + 4
        else:
            depth -= 1
            i = cl + 6
    return html[start:i - 6]


def extract_meta_info(html):
    metas = extract_metas(html)
    title = metas.get("og:title") or strip_tags(
        re.search(r'<h1[^>]*class="[^"]*rich_media_title[^"]*"[^>]*>(.*?)</h1>', html, re.S).group(1)
        if re.search(r'<h1[^>]*class="[^"]*rich_media_title[^"]*"[^>]*>(.*?)</h1>', html, re.S) else "")
    author = metas.get("author") or strip_tags(
        re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html, re.S).group(1)
        if re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html, re.S) else "")
    publish = (metas.get("article:published_time") or metas.get("og:release_date")
               or metas.get("publish_time")
               or strip_tags(re.search(r'<em[^>]*id="publish_time"[^>]*>(.*?)</em>', html, re.S).group(1)
                             if re.search(r'<em[^>]*id="publish_time"[^>]*>(.*?)</em>', html, re.S) else "")
               or (re.search(r"var\s+createTime\s*=\s*['\"]([^'\"]*)", html).group(1)
                   if re.search(r"var\s+createTime\s*=\s*['\"]([^'\"]*)", html) else ""))
    desc = metas.get("og:description") or metas.get("description") or ""
    return title, author, publish, desc


# ---------------------------------------------------------------- 视频
def decode_video_url(raw):
    """把 videoPageInfos 里的 mp4 url 解码成可下载地址。

    JS 里形如 `url: ('http://mpvideo.qpic.cn/...mp4?dis_k=...\\x26amp;dis_t=...')`：
    `\\x26amp;` 要先换成 `&`，再 `html.unescape` 把 `&amp;` 转 `&`。
    """
    return html_mod.unescape((raw or "").replace("\\x26", "&"))


def extract_video_urls(html):
    """从全文 JS 的 `var videoPageInfos` 抽 `vid → 最高清 mp4` 映射。

    腾讯视频播放器 iframe 只给 vid（wxv_…），真实 mp4 在 videoPageInfos 里，
    每支视频有多个清晰度（mp_video_trans_info），取 video_quality_level 最高那档。
    """
    m = re.search(r"var\s+videoPageInfos\s*=\s*\[", html)
    if not m:
        return {}
    start, depth, i = m.end() - 1, 0, m.end() - 1
    while i < len(html):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = html[start:i + 1]

    vids = list(re.finditer(r"video_id:\s*['\"]([^'\"]+)['\"]", block))
    out = {}
    for k, vm in enumerate(vids):
        vid = vm.group(1)
        seg = block[vm.start(): vids[k + 1].start()] if k + 1 < len(vids) else block[vm.start():]
        urls = re.findall(r"url:\s*\(\s*'([^']+)'", seg)
        levels = re.findall(r"video_quality_level:\s*['\"](\d+)['\"]", seg)
        if urls:
            best = urls[-1]
            if levels and len(urls) == len(levels):
                best = urls[levels.index(max(levels, key=int))]
            out[vid] = decode_video_url(best)
    return out


def classify_iframe(attrs):
    """iframe 可能是腾讯视频播放器：带 data-mpvid / vid=wxv_… 就转成「视频」，否则当占位链接。"""
    src = html_mod.unescape((attrs.get("data-src") or attrs.get("src") or "").strip())
    vid = (attrs.get("data-mpvid") or "").strip()
    if not vid:
        m = re.search(r"[?&]vid=(wxv_[A-Za-z0-9_]+)", src)
        vid = m.group(1) if m else ""
    if vid:
        return "video", vid, "mp4"
    if src:
        return "iframe", src, ""
    return None


# ---------------------------------------------------------------- HTML 树 → Markdown
class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag=None, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("__root__")
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag in SKIP_TAGS:
                self.skip += 1
            return
        if tag in SKIP_TAGS:
            self.skip = 1
            return
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in SKIP_TAGS:
                self.skip -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].children.append(data)


def text_content(node):
    parts = []
    for c in node.children:
        parts.append(c if isinstance(c, str) else text_content(c))
    return "".join(parts)


class MediaCollector:
    def __init__(self):
        self.items = []   # dicts: kind, url, ext
        self.by_url = {}

    def add(self, kind, url, ext):
        if url in self.by_url:
            return self.by_url[url]
        idx = len(self.items)
        self.items.append({"kind": kind, "url": url, "ext": ext})
        self.by_url[url] = idx
        return idx

    def token(self, idx):
        return "{{WEIXIN_MEDIA_%d}}" % idx


def ext_from_url(url):
    path = url.split("?")[0]
    m = re.search(r"\.(png|jpe?g|gif|webp|svg|bmp|avif|mp4|mp3|m4a)$", path, re.I)
    if m:
        return "jpg" if m.group(1).lower() == "jpeg" else m.group(1).lower()
    m = re.search(r"wx_fmt=(\w+)", url)
    if m:
        f = m.group(1).lower()
        return "jpg" if f == "jpeg" else f
    return None


def classify_img(attrs):
    url = html_mod.unescape((attrs.get("data-src") or attrs.get("src") or "").strip())
    if not url or url.startswith("data:"):
        return None
    if attrs.get("width") == "1" and attrs.get("height") == "1":
        return None  # 1x1 追踪像素
    low = url.lower()
    if "wx_fmt=gif" in low or ".gif" in low.split("?")[0]:
        return "gif", url, "gif"
    return "image", url, (ext_from_url(url) or "jpg")


def classify_src(attrs, default_ext):
    src = html_mod.unescape((attrs.get("src") or attrs.get("data-src") or "").strip())
    if not src or src.startswith("data:"):
        return None
    return src, (ext_from_url(src) or default_ext)


def inline_md(node, media):
    parts = []
    for c in node.children:
        if isinstance(c, str):
            parts.append(collapse(c))
            continue
        if c.tag is None:
            parts.append(inline_md(c, media))
            continue
        t = c.tag.lower()
        if t == "br":
            parts.append("\n")
        elif t in ("strong", "b"):
            inner = inline_md(c, media)
            parts.append("**" + inner + "**" if inner.strip() else "")
        elif t in ("em", "i"):
            inner = inline_md(c, media)
            parts.append("*" + inner + "*" if inner.strip() else "")
        elif t == "a":
            href = html_mod.unescape(c.attrs.get("href", "")).strip()
            txt = inline_md(c, media).strip() or collapse(text_content(c))
            parts.append(f"[{txt}]({href})" if href.startswith("http") else txt)
        elif t == "img":
            r = classify_img(c.attrs)
            if r:
                parts.append(media.token(media.add(*r)))
        elif t == "video":
            r = classify_src(c.attrs, "mp4")
            if r:
                parts.append(media.token(media.add("video", *r)))
        elif t == "mpvoice":
            r = classify_src(c.attrs, "mp3")
            if r:
                parts.append(media.token(media.add("audio", *r)))
        elif t == "iframe":
            r = classify_iframe(c.attrs)
            if r:
                parts.append(media.token(media.add(*r)))
        else:
            parts.append(inline_md(c, media))
    return "".join(parts)


BLOCK_WRAP = {"section", "div", "span", "font", "center", "figure", "figcaption",
              "header", "footer", "article", "blockquote", "table", "tbody", "tr", "td", "th"}


def block_md(node, media, out):
    for c in node.children:
        if isinstance(c, str):
            txt = collapse(c)
            if txt:
                out.append(txt)          # 裸文本流动，不打断
            continue
        if c.tag is None:
            block_md(c, media, out)
            continue
        t = c.tag.lower()
        if t == "p":
            txt = inline_md(c, media).strip()
            if txt:
                out.append("\n\n" + txt + "\n\n")
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            txt = inline_md(c, media).strip()
            if txt:
                out.append("\n\n" + "#" * int(t[1]) + " " + txt + "\n\n")
        elif t == "blockquote":
            inner = []
            block_md(c, media, inner)
            txt = "".join(inner).strip()
            if txt:
                out.append("\n\n" + "\n".join("> " + l for l in txt.split("\n")) + "\n\n")
        elif t in ("ul", "ol"):
            items = []
            for li in c.children:
                if isinstance(li, str) or li.tag is None or li.tag.lower() != "li":
                    continue
                txt = inline_md(li, media).strip()
                if txt:
                    items.append("- " + txt)
            if items:
                out.append("\n\n" + "\n".join(items) + "\n\n")
        elif t == "li":
            txt = inline_md(c, media).strip()
            if txt:
                out.append("\n\n- " + txt + "\n\n")
        elif t == "img":
            r = classify_img(c.attrs)
            if r:
                out.append("\n\n" + media.token(media.add(*r)) + "\n\n")
        elif t == "video":
            r = classify_src(c.attrs, "mp4")
            if r:
                out.append("\n\n" + media.token(media.add("video", *r)) + "\n\n")
        elif t == "mpvoice":
            r = classify_src(c.attrs, "mp3")
            if r:
                out.append("\n\n" + media.token(media.add("audio", *r)) + "\n\n")
        elif t == "iframe":
            r = classify_iframe(c.attrs)
            if r:
                out.append("\n\n" + media.token(media.add(*r)) + "\n\n")
        elif t == "br":
            out.append("\n")
        elif t == "hr":
            out.append("\n\n---\n\n")
        elif t == "pre":
            out.append("\n\n```\n" + text_content(c).strip() + "\n```\n\n")
        else:
            block_md(c, media, out)     # section/div/span/其他容器递归
    return out


def html_to_markdown(js_inner):
    tb = TreeBuilder()
    tb.feed(js_inner)
    media = MediaCollector()
    out = block_md(tb.root, media, [])
    body = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
    return body, media


# ---------------------------------------------------------------- 下载 / 上传
def download(url, dest, retries=2):
    """流式下载到 .part 再原子替换，避免半文件被当成成功。"""
    def once():
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return os.path.getsize(dest)
    return _with_retry(once, retries=retries, base_delay=0.8)


# ---------------------------------------------------------------- 笔记组装
def yaml_str(s):
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def note_ref(text):
    """笔记之间的引用：Obsidian 用双链，普通文件夹用纯文本。"""
    return f"[[{text}]]" if tc.is_obsidian() else text


def author_ref(author):
    return f"[[{author}]]" if tc.is_obsidian() else author


def build_note(title, author, publish, desc, url, body, albums=None):
    today = date.today().isoformat()
    lines = ["---", f"title: {yaml_str(title)}", f"source: {yaml_str(url)}"]
    if author:
        lines.append("author:")
        lines.append(f'  - "{author_ref(author)}"')
    if publish:
        lines.append(f"published: {yaml_str(parse_date(publish))}")
    if albums:
        primary = albums[0]
        lines.append(f"album: {yaml_str(primary.get('name'))}")
        lines.append(f"album_id: {yaml_str(primary.get('id'))}")
        lines.append(f"album_order: {int(primary.get('order') or 0)}")
        lines.append(f"album_url: {yaml_str(primary.get('url'))}")
        lines.append("albums:")
        for a in albums:
            lines.append(f"  - name: {yaml_str(a.get('name'))}")
            lines.append(f"    id: {yaml_str(a.get('id'))}")
            lines.append(f"    order: {int(a.get('order') or 0)}")
            lines.append(f"    url: {yaml_str(a.get('url'))}")
    lines.append(f"created: {yaml_str(today)}")
    if desc:
        lines.append(f"description: {yaml_str(desc)}")
    lines.append("tags:")
    lines.append('  - "clippings"')
    lines.append("---")
    lines.append("")
    meta_line = f"> 原文：[公众号原文]({url})"
    if author:
        meta_line += f" · 作者 {author_ref(author)}"
    if publish:
        meta_line += f" · 发布于 {publish}"
    lines.append(meta_line)
    if albums:
        primary = albums[0]
        lines.append(f"> 专辑：[{primary.get('name')}]({primary.get('url')}) · 第 {int(primary.get('order') or 0)} 篇")
    lines.append("")
    lines.append(body.rstrip())
    lines.append("")
    return "\n".join(lines)


def render_media_item(item, cache_rel):
    if item["kind"] == "iframe":
        return f"\n> ⚠️ iframe 嵌入（非腾讯视频，未抓取）：[原文内]({item['url']})"
    line = f"\n{tc.embed(cache_rel)}"
    if item.get("item_id"):
        line += f"\n\n[Eagle](eagle://item/{item['item_id']})"
    return line


# ---------------------------------------------------------------- index.md
def update_index(topic_dir, title, publish, url):
    idx_path = os.path.join(topic_dir, "index.md")
    row_date = parse_date(publish) if publish else date.today().isoformat()
    if not os.path.exists(idx_path) or os.path.getsize(idx_path) < 3:
        content = (
            f"# {os.path.basename(topic_dir)}\n\n"
            "> 专题：自媒体内容收集。流程：收集 → 整理 → 输出。本页是中枢导航（hub）。\n\n"
            "## 收集的笔记（00-收集/）\n\n"
            "| 标题 | 日期 | 来源 |\n"
            "| --- | --- | --- |\n"
            f"| {note_ref(title)} | {row_date} | [公众号]({url}) |\n"
        )
    else:
        with open(idx_path, encoding="utf-8") as f:
            existing = f.read()
        content = existing.rstrip() + f"\n- {note_ref(title)} — {row_date} · [公众号]({url})\n"
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(content)
    return idx_path

# ---------------------------------------------------------------- 专辑 / 列表
def is_album_url(url):
    return "appmsgalbum" in (url or "") or "action=getalbum" in (url or "")


def album_params(url):
    """从专辑链接里取 __biz 与 album_id；兼容 HTML 里的 &amp;。"""
    raw = html_mod.unescape(url or "")
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw).query)
    biz = urllib.parse.unquote((qs.get("__biz") or [""])[0])
    aid = (qs.get("album_id") or [""])[0]
    if not aid:
        m = re.search(r"[?&]album_id=(\d+)", raw)
        aid = m.group(1) if m else ""
    return biz, aid


def album_id_from_url(url):
    return album_params(url)[1] or (re.search(r"album_id[=:/](\d+)", url or "").group(1)
                                     if re.search(r"album_id[=:/](\d+)", url or "") else "")


def canonical_album_url(biz, aid):
    return (f"https://mp.weixin.qq.com/mp/appmsgalbum?__biz={biz}"
            f"&action=getalbum&album_id={aid}&scene=126")


def normalize_article_url(url):
    u = html_mod.unescape(url or "").strip()
    u = u.split("#", 1)[0]
    return re.sub(r"^http://", "https://", u, count=1)


def article_key(url):
    """稳定去重键：长链用 mid:idx，短链用 /s/xxx。"""
    u = urllib.parse.urlsplit(html_mod.unescape(url or ""))
    q = urllib.parse.parse_qs(u.query)
    if q.get("mid") and q.get("idx"):
        return f"{q['mid'][0]}:{q['idx'][0]}"
    m = re.match(r"^/s/([A-Za-z0-9_-]+)$", u.path)
    if m:
        return "s:" + m.group(1)
    return (url or "").split("#", 1)[0]


def ts_date(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def album_api_url(biz, aid, cursor=None):
    u = (f"https://mp.weixin.qq.com/mp/appmsgalbum?__biz={biz}"
         f"&action=getalbum&album_id={aid}&count={ALBUM_PAGE_SIZE}&f=json&scene=126")
    if cursor:
        u += f"&begin_msgid={cursor[0]}&begin_itemidx={cursor[1] or '1'}"
    return u


def fetch_album_page(biz, aid, cursor=None, retries=5):
    """抓一页专辑 JSON。请求过快会返回空页，按退避重试。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(album_api_url(biz, aid, cursor), headers={
                "User-Agent": UA, "Referer": REFERER,
                "Accept": "application/json,text/plain,*/*",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            resp = data.get("getalbum_resp") or {}
            if resp.get("article_list"):
                return resp
            if resp.get("base_info") and str(resp.get("continue_flag")) != "1":
                return resp
            last = AlbumError(f"专辑 {aid} 返回空页（可能被限流）")
        except Exception as exc:
            last = exc
            if not _retryable(exc):
                raise
        time.sleep(1.5 * (attempt + 1))
    raise last or AlbumError(f"专辑 {aid} 抓取失败")


def enumerate_album(album_url, max_pages=ALBUM_MAX_PAGES):
    """枚举一个专辑，返回 (meta, items)；items 按 pos_num 升序。"""
    biz, aid = album_params(album_url)
    if not biz or not aid:
        raise AlbumError(f"无法从链接解析 __biz / album_id：{album_url}")

    meta = None
    items, seen, cursor = [], set(), None
    for page in range(max_pages):
        resp = fetch_album_page(biz, aid, cursor)
        base = resp.get("base_info") or {}
        if meta is None:
            count = int(base.get("article_count") or 0)
            meta = {
                "album_id": aid,
                "title": base.get("title") or aid,
                "nickname": base.get("nickname") or "",
                "description": base.get("description") or "",
                "article_count": count,
                "is_reverse": base.get("is_reverse") or "",
                "url": canonical_album_url(biz, aid),
            }
        articles = resp.get("article_list") or []
        fresh = 0
        for a in articles:
            mid = str(a.get("msgid") or "")
            idx = str(a.get("itemidx") or "1")
            if not mid:
                continue
            key = f"{mid}:{idx}"
            if key in seen:
                continue
            seen.add(key)
            fresh += 1
            items.append({
                "key": key,
                "msgid": mid,
                "itemidx": idx,
                "pos_num": int(a.get("pos_num") or 0),
                "title": html_mod.unescape(a.get("title") or "").strip(),
                "url": normalize_article_url(a.get("url") or ""),
                "create_time": int(a.get("create_time") or 0),
                "album_id": aid,
                "album_title": meta["title"],
                "album_article_count": meta["article_count"],
                "album_url": meta["url"],
            })

        total = meta["article_count"]
        more = str(resp.get("continue_flag")) == "1"   # 注意：接口返回字符串 '1'
        if (total and len(items) >= total) or not more or fresh == 0:
            break
        last = articles[-1]
        cursor = (last.get("msgid"), last.get("itemidx") or "1")
        time.sleep(ALBUM_PAGE_DELAY)

    if not items:
        raise AlbumError(f"未解析到专辑文章：{meta['title'] if meta else aid}")
    if meta["article_count"] and len(items) < meta["article_count"]:
        print(f"[警告] 专辑「{meta['title']}」声明 {meta['article_count']} 篇，"
              f"实际枚举到 {len(items)} 篇", file=sys.stderr)
    if not meta["article_count"]:
        meta["article_count"] = len(items)
    items.sort(key=lambda x: (x["pos_num"] or 0, x["create_time"] or 0))
    return meta, items


def merge_albums(specs):
    """多专辑合并去重；保留每篇的全部专辑归属，主专辑取篇数最少（最具体）的。"""
    merged, order = {}, []
    for meta, items in sorted(specs, key=lambda s: (s[0].get("article_count") or 10 ** 9,
                                                    s[0].get("title") or "")):
        for it in sorted(items, key=lambda x: (x["pos_num"] or 0, x["create_time"] or 0)):
            key = it["key"]
            if key not in merged:
                merged[key] = dict(it)
                merged[key]["memberships"] = []
                order.append(key)
            member = {
                "name": meta["title"],
                "id": meta["album_id"],
                "order": it["pos_num"],
                "url": meta["url"],
                "count": meta.get("article_count") or 0,
            }
            if not any(m["id"] == member["id"] and m["order"] == member["order"]
                       for m in merged[key]["memberships"]):
                merged[key]["memberships"].append(member)

    out = []
    for key in order:
        a = merged[key]
        a["memberships"].sort(key=lambda m: (m["count"], m["order"]))
        primary = a["memberships"][0]
        a["album"] = primary["name"]
        a["album_id"] = primary["id"]
        a["album_order"] = primary["order"]
        a["album_url"] = primary["url"]
        out.append(a)
    return out


def parse_list_file(path):
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            found = re.findall(r"(?:https?://)?mp\.weixin\.qq\.com/[^\s)\]\"'<>]+", line)
            if not found and line.startswith("http"):
                found = [line]
            for u in found:
                if not u.startswith("http"):
                    u = "https://" + u
                u = normalize_article_url(u)
                if u not in urls:
                    urls.append(u)
    return urls


def discover_albums(article_urls, sample=30):
    """从文章正文发现 album_id（用于账号列表先问后抓；只读）。"""
    subset = article_urls if sample <= 0 else article_urls[:sample]
    biz = ""
    for u in article_urls:
        biz = album_params(u)[0] or urllib.parse.parse_qs(
            urllib.parse.urlsplit(u).query).get("__biz", [""])[0]
        if biz:
            break
    ids = {}
    for i, u in enumerate(subset, 1):
        try:
            page = fetch_html(u, retries=2)
        except Exception as exc:
            print(f"  [发现专辑 {i}/{len(subset)}] 跳过：{exc}", file=sys.stderr)
            continue
        for m in re.finditer(r"album_id[=:'\"\s]+['\"]?(\d{15,25})", page):
            ids[m.group(1)] = ids.get(m.group(1), 0) + 1
        print(f"  [发现专辑 {i}/{len(subset)}] 已发现 {len(ids)} 个专辑", file=sys.stderr)

    albums = []
    for aid in ids:
        try:
            resp = fetch_album_page(biz, aid)
            base = resp.get("base_info") or {}
            albums.append({
                "album_id": aid,
                "title": base.get("title") or aid,
                "nickname": base.get("nickname") or "",
                "article_count": int(base.get("article_count") or 0),
                "url": canonical_album_url(biz, aid),
                "mentions": ids[aid],
            })
        except Exception as exc:
            albums.append({"album_id": aid, "title": aid, "error": str(exc),
                           "mentions": ids[aid]})
    albums.sort(key=lambda a: a.get("article_count") or 0, reverse=True)
    return albums


# ---------------------------------------------------------------- 专题目录 / 索引
def ensure_topic_dirs(topic_dir):
    for sub in ("00-收集", "10-笔记", "20-输出", "assets"):
        os.makedirs(os.path.join(topic_dir, sub), exist_ok=True)


def _yaml_unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except Exception:
            return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def scan_topic_notes(topic_dir):
    """扫描 00-收集/*.md 的 frontmatter，用于跳过已存在与生成索引。"""
    collect = os.path.join(topic_dir, "00-收集")
    if not os.path.isdir(collect):
        return []
    out = []
    for fn in sorted(os.listdir(collect)):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        path = os.path.join(collect, fn)
        try:
            text = open(path, encoding="utf-8").read()
        except Exception:
            continue
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            if line.startswith((" ", "\t")):
                continue
            mm = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if mm:
                fm[mm.group(1)] = _yaml_unquote(mm.group(2))
        out.append({
            "path": path,
            "stem": fn[:-3],
            "title": fm.get("title") or fn[:-3],
            "source": fm.get("source") or "",
            "published": fm.get("published") or "",
            "album": fm.get("album") or "",
            "album_id": fm.get("album_id") or "",
            "album_order": int(fm["album_order"]) if str(fm.get("album_order", "")).isdigit() else None,
        })
    return out


def _album_block(meta, items, notes_by_key):
    aid = meta["album_id"]
    lines = [f"<!-- wechat-album:{aid}:start -->",
             f"### 专辑：{meta['title']}（{meta.get('article_count') or len(items)} 篇）", ""]
    rows = []
    for it in sorted(items, key=lambda x: (x["pos_num"] or 0, x["create_time"] or 0)):
        note = notes_by_key.get(it["key"])
        if not note:
            continue
        title = (note["title"] or it["title"]).replace("|", "\\|")
        published = note["published"] or ts_date(it["create_time"])
        rows.append(f"| {it['pos_num']} | {note_ref(note['stem'])} | {published} | [公众号]({it['url']}) |")
    lines.append(f"- 已收集 {len(rows)}/{meta.get('article_count') or len(items)}")
    lines.append("")
    if rows:
        lines += ["| # | 标题 | 发布 | 来源 |", "| --- | --- | --- | --- |"] + rows
    else:
        lines.append("> 本专辑暂无已抓取笔记。")
    lines.append(f"<!-- wechat-album:{aid}:end -->")
    return "\n".join(lines)


def write_album_index(topic_dir, specs, articles):
    idx_path = os.path.join(topic_dir, "index.md")
    notes = scan_topic_notes(topic_dir)
    notes_by_key = {article_key(n["source"]): n for n in notes if n.get("source")}
    blocks = [_album_block(meta, items, notes_by_key) for meta, items in specs]
    total_rows = sum(len(items) for _, items in specs)
    summary = (f"> 去重后 {len(articles)} 篇；专辑条目合计 {total_rows}。"
               f"同一篇可能出现在多个专辑。")
    header = (
        f"# {os.path.basename(topic_dir)}\n\n"
        f"> 专题：{os.path.basename(topic_dir)}。流程：收集 → 整理 → 输出。本页是中枢导航（hub）。\n\n"
        f"## 收集的笔记（00-收集/）\n\n"
        f"{summary}\n"
    )

    if os.path.exists(idx_path):
        text = open(idx_path, encoding="utf-8").read()
        if re.search(r"^> 去重后 .*$", text, re.M):
            text = re.sub(r"^> 去重后 .*$", summary, text, count=1, flags=re.M)
        elif "## 收集的笔记（00-收集/）" in text:
            text = text.replace("## 收集的笔记（00-收集/）",
                                "## 收集的笔记（00-收集/）\n\n" + summary, 1)
    else:
        text = header

    for block in blocks:
        aid = re.search(r"wechat-album:(\d+):start", block).group(1)
        pat = re.compile(r"<!-- wechat-album:%s:start -->.*?<!-- wechat-album:%s:end -->"
                         % (re.escape(aid), re.escape(aid)), re.S)
        if pat.search(text):
            text = pat.sub(block, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + block + "\n"

    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    tmp = idx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, idx_path)
    return idx_path


# ---------------------------------------------------------------- 单篇管线
EMPTY_BODY_MESSAGE = (
    "[错误] 正文解析为空：取到了 js_content 容器，但没解析出任何文字。\n"
    "       已停止，未下载 / 未上传 / 未写入任何文件（笔记与索引均未改动）。\n"
    "\n"
    "可能原因：\n"
    "  1. 该文章正文本来就是纯图片 / 纯视频，没有文字；\n"
    "  2. 微信改版了正文 DOM，解析器 html_to_markdown 跟不上；\n"
    "  3. 命中半拦截页（如「关注后查看」），js_content 在、但里面只是提示语。\n"
    "\n"
    "推荐下一步（按序排查）：\n"
    "  1. 加 --dry-run 重跑：确认正文是否本就 0 字、媒体清单是否正常；\n"
    "  2. 浏览器打开原文 → F12 → 看 <div id=\"js_content\"> 的真实结构，"
    "对照 references/wechat-parsing.md；\n"
    "  3. 确认是解析器跟不上升级后，先更新该文档、再改 html_to_markdown。\n"
    "\n"
    "未做任何写入，可安全重试；请人工确认下一步。\n"
)


def process_article(url, *, topic, eagle_folder=None,
                    dry_run=False, verbose=True, folder_id=None,
                    albums=None, note_path=None, stem_override=None,
                    note_name_max=0, manage_index=True):
    """抓一篇 → 下载媒体 → 写笔记。成功返回 dict，失败抛 WechatError。"""
    log = print if verbose else (lambda *a, **k: None)

    log("[1/6] 抓取文章…")
    html_text, js_inner = "", None
    for attempt in range(3):
        try:
            html_text = fetch_html(url)
        except Exception as exc:
            if attempt == 2:
                raise ArticleError(f"抓取失败：{exc}")
            time.sleep(2 * (attempt + 1))
            continue
        js_inner = extract_js_content(html_text)
        if js_inner:
            break
        if attempt < 2:
            time.sleep(2 * (attempt + 1))

    title, author, publish, desc = extract_meta_info(html_text)

    # 反爬判断：只认「js_content 非空 + og:title 有值」，不靠关键词。
    if not js_inner:
        raise ArticleError("未取到正文（js_content 为空）：可能是验证页 / 已删除 / 需登录。")
    if not title:
        raise ArticleError("未识别到标题（og:title 为空）：可能不是微信文章页。")
    log(f"    标题: {title}")
    log(f"    作者: {author or '（未知）'}")
    log(f"    发布: {publish or '（未知）'}")

    vid_map = extract_video_urls(html_text)

    log("[2/6] 解析正文…")
    body, media = html_to_markdown(js_inner)
    log(f"    正文 {len(body)} 字，媒体 {len(media.items)} 个")

    result = {
        "status": "ok", "url": url, "title": title, "author": author,
        "publish": publish, "body_chars": len(body),
        "media_total": 0, "media_ok": 0, "media_failed": 0,
        "iframe_count": 0, "note_path": None, "index_path": None,
    }

    if dry_run:
        print("\n===== 预览（dry-run，未下载/上传/写入）=====")
        print("--- 媒体清单 ---")
        for i, it in enumerate(media.items):
            print(f"  [{i}] {it['kind']}: {it['url'][:80]}")
        print("--- 正文前 1500 字 ---")
        print(body[:1500])
        result["status"] = "dry-run"
        return result

    if not body.strip():
        raise EmptyBodyError(EMPTY_BODY_MESSAGE)

    project = Path(tc.project_dir(topic))
    media_root = project / "assets"
    eagle_on = tc.eagle_enabled()
    eagle_folder = eagle_folder or tc.eagle_settings().get("folder") or "tome素材"

    stem = stem_override or safe_name(title, LINK_ILLEGAL)[:80]
    if not media.items:
        log("[跳过] 无媒体，直接写纯文字笔记。")
    else:
        if eagle_on and folder_id is None:
            log("[3/6] 检查 Eagle…")
            eagle_library_root()
            folder_id = resolve_or_create_folder(eagle_folder)
            log(f"    Eagle 文件夹: {eagle_folder} (id={folder_id})")
        log("[4/6] 下载媒体到 assets/" + (" 并上传 Eagle…" if eagle_on else "…"))
        os.makedirs(media_root, exist_ok=True)
        for i, it in enumerate(media.items):
            if it["kind"] == "iframe":
                it["item_id"] = None
                log(f"    [{i}] iframe 占位跳过: {it['url'][:60]}")
                continue
            src = it["url"]
            if it["kind"] == "video" and src.startswith("wxv_"):
                src = vid_map.get(src)
                if not src:
                    it["item_id"] = None
                    log(f"    [{i}] ✗ 视频 mp4 未解析到（vid={it['url']}）")
                    continue
                it["url"] = src
            cache_rel = f"assets/{stem}-{i}.{it['ext']}"
            cache_abs = media_root / f"{stem}-{i}.{it['ext']}"
            try:
                download(src, str(cache_abs))
                it["downloaded"] = True
                if eagle_on:
                    it["item_id"] = eagle_add_from_path(
                        str(cache_abs), f"{stem}-{i}", folder_id)
                log(f"    [{i}] ✓ {it['kind']} {os.path.getsize(cache_abs)}B")
            except Exception as exc:
                it["item_id"] = None
                log(f"    [{i}] ✗ 失败（{exc}），保留原链接")

    log("[5/6] 组装笔记…")
    for i, it in enumerate(media.items):
        if it["kind"] == "iframe":
            rendered = render_media_item(it, "")
        elif it.get("downloaded"):
            rendered = render_media_item(it, f"assets/{stem}-{i}.{it['ext']}")
        else:
            rendered = f"> ⚠️ 下载失败：[{it['url']}]({it['url']})"
        body = body.replace(f"{{{{WEIXIN_MEDIA_{i}}}}}", rendered)
    body = re.sub(r"\{\{WEIXIN_MEDIA_(\d+)\}\}",
                  lambda m: media.items[int(m.group(1))]["url"], body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    note = build_note(title, author, publish, desc, url, body, albums=albums)

    topic_dir = str(project)
    collect_dir = os.path.join(topic_dir, "00-收集")
    os.makedirs(collect_dir, exist_ok=True)

    if note_name_max:
        note_stem = safe_name(title, LINK_ILLEGAL)[:note_name_max]
    elif albums is None:
        note_stem = safe_name(title)
    else:
        note_stem = safe_name(title, LINK_ILLEGAL)[:60]
    if note_path is None:
        note_path = os.path.join(collect_dir, f"{note_stem}.md")
        n = 2
        while os.path.exists(note_path):
            note_path = os.path.join(collect_dir, f"{note_stem}-{n}.md")
            n += 1

    log("[6/6] 写笔记 + 更新 index…" if manage_index and albums is None else "[6/6] 写笔记…")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)

    idx_path = None
    if manage_index and albums is None:
        idx_path = update_index(topic_dir, title, publish, url)

    iframe_count = sum(1 for it in media.items if it["kind"] == "iframe")
    real_items = [it for it in media.items if it["kind"] != "iframe"]
    result.update({
        "media_total": len(real_items),
        "media_ok": sum(1 for it in real_items if it.get("downloaded")),
        "media_failed": sum(1 for it in real_items if not it.get("downloaded")),
        "iframe_count": iframe_count,
        "note_path": note_path,
        "index_path": idx_path,
    })
    return result


def run_single(args):
    try:
        res = process_article(
            args.url, topic=args.topic or DEFAULT_TOPIC,
            eagle_folder=args.eagle_folder, dry_run=args.dry_run, verbose=True)
    except EmptyBodyError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 3
    except WechatError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1

    if res["status"] == "dry-run":
        return 0

    print("\n完成 ✓")
    print(f"  笔记: {res['note_path']}")
    print(f"  索引: {res['index_path']}")
    dest = "入 Eagle" if tc.eagle_enabled() else "存入 assets/"
    print(f"  媒体: {res['media_ok']} 个{dest}"
          f"（{res['iframe_count']} 个 iframe 占位，{res['media_failed']} 个失败）")
    return 0


# ---------------------------------------------------------------- 批量
def _load_specs(args, quiet=False):
    """枚举 --album-url / --list 里的专辑。返回 (specs, list_articles, errors)。"""
    specs, errors = [], []
    album_urls = list(args.album_url or [])
    list_articles = []

    if args.list:
        urls = parse_list_file(args.list)
        for u in urls:
            if is_album_url(u):
                album_urls.append(u)
            else:
                list_articles.append(u)

    for u in album_urls:
        aid = album_id_from_url(u)
        if args.only_album and aid not in args.only_album:
            continue
        try:
            meta, items = enumerate_album(u, max_pages=args.max_pages)
            specs.append((meta, items))
            if not quiet:
                print(f"[专辑] {meta['title']} · {meta['nickname'] or '?'} · "
                      f"{meta['article_count']} 篇", file=sys.stderr)
        except Exception as exc:
            errors.append({"url": u, "error": str(exc)})
            print(f"[专辑失败] {u} — {exc}", file=sys.stderr)
    return specs, list_articles, errors


def _print_plan(specs, articles, existing_keys):
    for meta, items in specs:
        print(f"[专辑] {meta['title']} · {meta['nickname'] or '?'} · "
              f"{meta['article_count']} 篇")
    print(f"[计划] 去重后 {len(articles)} 篇，已存在 {sum(1 for a in articles if a['key'] in existing_keys)} 篇，"
          f"待抓 {sum(1 for a in articles if a['key'] not in existing_keys)} 篇")
    for i, a in enumerate(articles, 1):
        mark = " [已存在]" if a["key"] in existing_keys else ""
        print(f"[{i:03d}/{len(articles)}] #{a.get('album_order') or '-'} {a['title'] or a['url']}{mark}")
        print(f"        {a['url']}")


def _manifest(specs, articles, list_articles, errors, discovered):
    return {
        "albums": [
            {"album_id": m["album_id"], "title": m["title"], "nickname": m["nickname"],
             "article_count": m["article_count"], "url": m["url"]}
            for m, _ in specs
        ],
        "unique_count": len(articles),
        "album_rows": sum(len(items) for _, items in specs),
        "articles": [
            {"key": a["key"], "title": a["title"], "url": a["url"],
             "create_time": a.get("create_time"), "album": a.get("album"),
             "album_id": a.get("album_id"), "album_order": a.get("album_order"),
             "albums": a.get("memberships", [])}
            for a in articles
        ],
        "list_articles": list_articles,
        "discovered_albums": discovered,
        "errors": errors,
    }


def _build_stem_map(articles, reserved=None):
    """为批量预计算缓存 stem，避开已存在笔记与截断后的重名。"""
    counts = {}
    for a in articles:
        base = safe_name(a["title"] or ("article-" + re.sub(r"[^A-Za-z0-9]+", "", a["key"])),
                         LINK_ILLEGAL)[:60] or "article"
        counts[base] = counts.get(base, 0) + 1
    out, used = {}, set(reserved or ())
    for a in articles:
        base = safe_name(a["title"] or ("article-" + re.sub(r"[^A-Za-z0-9]+", "", a["key"])),
                         LINK_ILLEGAL)[:60] or "article"
        stem = base
        if counts[base] > 1 or stem in used:
            stem = f"{base}-{a['key'][-6:]}"
        n = 2
        while stem in used:
            stem = f"{base}-{a['key'][-6:]}-{n}"
            n += 1
        used.add(stem)
        out[a["key"]] = stem
    return out


def _run_post_dedupe():
    """批量结束后的固定后处理：Obsidian 自动去重 + Eagle 重复提醒。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "post_batch_dedupe.py")
    print("\n[后处理] 媒体去重 + Eagle 重复检查…")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True,
                           text=True, timeout=600)
        lines = [l.strip() for l in (r.stdout or "").strip().splitlines()
                 if l.strip().startswith(("媒体目录：", "Eagle：", "请用"))]
        for line in lines:
            print("       " + line)
        if r.returncode != 0:
            print(f"       [警告] 后处理退出码 {r.returncode}：{(r.stderr or '')[:200]}",
                  file=sys.stderr)
    except Exception as exc:  # 后处理失败不影响抓取结果
        print(f"       [跳过] {exc}", file=sys.stderr)


def run_batch(args):
    specs, list_articles, errors = _load_specs(
        args, quiet=args.list_only or args.dry_run)

    # 账号列表里没有专辑链接时，可选从文章正文发现专辑（只读，先问后抓）。
    discovered = []
    if args.list and args.discover_albums:
        if not args.list_only:
            print("[错误] --discover-albums 只能与 --list-only 一起用：先发现、问过 ink 再抓。",
                  file=sys.stderr)
            return 1
        discovered = discover_albums(list_articles, sample=args.discover_sample)

    merged = merge_albums(specs)
    known = {a["key"] for a in merged}
    for u in list_articles:
        key = article_key(u)
        if key in known:
            continue
        known.add(key)
        merged.append({
            "key": key, "msgid": key.split(":")[0], "itemidx": "1",
            "pos_num": 0, "title": "", "url": u, "create_time": 0,
            "album": None, "album_id": None, "album_order": None,
            "album_url": None, "memberships": [],
        })

    start = max(1, int(args.start or 1))
    articles = merged[start - 1:]
    if args.limit is not None:
        articles = articles[:max(0, args.limit)]

    if args.list_only:
        manifest = _manifest(specs, articles, list_articles, errors, discovered)
        if args.json:
            print(json.dumps(manifest, ensure_ascii=False, indent=1))
        else:
            _print_plan(specs, articles, set())
            for d in discovered:
                print(f"[发现的专辑] {d.get('title')} · {d.get('article_count', '?')} 篇 · {d.get('url')}")
            for e in errors:
                print(f"[失败] {e['url']} — {e['error']}", file=sys.stderr)
        return 0

    if not articles:
        print("[错误] 没有可处理的文章。", file=sys.stderr)
        return 1

    topic = args.topic
    if not topic:
        print("[错误] 批量模式必须指定 --topic（先分析内容、与 ink 确认专题名）。",
              file=sys.stderr)
        return 1

    topic_dir = str(tc.project_dir(topic))
    existing = {article_key(n["source"]): n for n in scan_topic_notes(topic_dir)
                if n.get("source")}
    existing_keys = set(existing)

    if args.dry_run:
        _print_plan(specs, articles, existing_keys)
        return 0

    ensure_topic_dirs(topic_dir)

    todo = [a for a in articles if args.force or a["key"] not in existing_keys]
    skipped = len(articles) - len(todo)

    folder_id = None
    if todo:
        try:
            eagle_library_root()
            folder_id = resolve_or_create_folder(args.eagle_folder)
            print(f"[Eagle] 文件夹: {args.eagle_folder} (id={folder_id})")
        except Exception as exc:
            print(f"[错误] Eagle 预检失败：{exc}", file=sys.stderr)
            return 2

    stems = _build_stem_map(todo, reserved={n["stem"] for n in existing.values()})
    failures, ok_count, media_ok, media_failed = [], 0, 0, 0

    try:
        for n, a in enumerate(todo, 1):
            force_note = existing.get(a["key"]) if args.force else None
            try:
                res = process_article(
                    a["url"], topic=topic, eagle_folder=args.eagle_folder,
                    verbose=args.verbose, folder_id=folder_id,
                    albums=a.get("memberships") or None,
                    note_path=force_note["path"] if force_note else None,
                    stem_override=stems.get(a["key"]),
                    note_name_max=60, manage_index=False,
                )
                ok_count += 1
                media_ok += res["media_ok"]
                media_failed += res["media_failed"]
                print(f"[{n}/{len(todo)}] #{a.get('album_order') or '-'} 完成：{res['title']} "
                      f"（媒体 {res['media_ok']}/{res['media_total']}）")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failures.append({"title": a.get("title") or a["url"], "url": a["url"],
                                 "error": str(exc)})
                print(f"[{n}/{len(todo)}] #{a.get('album_order') or '-'} 失败："
                      f"{a.get('title') or a['url']} — {exc}", file=sys.stderr)
            if n < len(todo):
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[中断] 已保存当前进度；重跑会自动跳过已完成篇目。", file=sys.stderr)
        try:
            write_album_index(topic_dir, specs, merged)
        except Exception:
            pass
        return 130

    idx_path = write_album_index(topic_dir, specs, merged) if specs else None

    print(f"\n[总结] 专辑 {len(specs)} 个，去重 {len(articles)} 篇；"
          f"成功 {ok_count}，跳过 {skipped}，失败 {len(failures)}")
    print(f"       媒体：入 Eagle {media_ok}，失败 {media_failed}")
    if idx_path:
        print(f"       索引: {idx_path}")
    for f in failures:
        print(f"       ✗ {f['title']} — {f['error']}\n         {f['url']}", file=sys.stderr)

    if ok_count and not args.no_post_dedupe:
        _run_post_dedupe()

    if failures:
        return 3 if (ok_count > 0 or skipped > 0 or idx_path) else 4
    if media_failed or errors:
        return 3
    return 0


# ---------------------------------------------------------------- 主流程
def build_parser(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="微信文章 / 公众号专辑 → Obsidian + Eagle")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="单篇文章链接；传 appmsgalbum 链接会自动切专辑模式")
    g.add_argument("--album-url", action="append", default=[],
                   help="公众号专辑链接，可重复传多个")
    g.add_argument("--list", help="账号列表/文章链接文件，每行一个链接")

    ap.add_argument("--topic", default=None,
                    help=f"专题名；单篇默认 {DEFAULT_TOPIC}，专辑/列表模式必填")
    ap.add_argument("--eagle-folder", default=None,
                    help="Eagle 文件夹名（默认取配置 media.eagle.folder）")
    ap.add_argument("--dry-run", action="store_true", help="只枚举/预览，不下载/上传/写文件")

    ap.add_argument("--list-only", action="store_true", help="只枚举清单，不抓文章")
    ap.add_argument("--json", action="store_true", help="清单以 JSON 输出（隐含 --list-only）")
    ap.add_argument("--only-album", action="append", default=[],
                    help="只处理指定 album_id，可重复")
    ap.add_argument("--start", type=int, default=1, help="从合并后的第 N 篇开始（1 起）")
    ap.add_argument("--limit", type=int, default=None, help="最多处理 N 篇（冒烟用）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的同源笔记")
    ap.add_argument("--interval", type=float, default=1.5, help="篇间间隔秒数（默认 1.5）")
    ap.add_argument("--max-pages", type=int, default=ALBUM_MAX_PAGES,
                    help=f"专辑翻页上限（默认 {ALBUM_MAX_PAGES}）")
    ap.add_argument("--discover-albums", action="store_true",
                    help="--list 模式下从文章正文发现专辑（仅配合 --list-only）")
    ap.add_argument("--discover-sample", type=int, default=30,
                    help="发现专辑时抽样文章数，0=全量（默认 30）")
    ap.add_argument("--verbose", action="store_true", help="批量时打印每篇详细日志")
    ap.add_argument("--no-post-dedupe", action="store_true",
                    help="批量结束后不执行素材去重后处理")
    args = ap.parse_args(argv)

    if args.json:
        args.list_only = True
    if args.url and is_album_url(args.url):
        args.album_url = [args.url]
        args.url = None

    batch_mode = bool(args.album_url or args.list)
    if batch_mode and not args.topic and not args.list_only:
        ap.error("专辑/列表模式必须指定 --topic（先分析内容、与 ink 确认专题名）")
    if not batch_mode:
        batch_only = {
            "--list-only": args.list_only, "--json": args.json,
            "--only-album": bool(args.only_album), "--force": args.force,
            "--discover-albums": args.discover_albums,
            "--start": args.start != 1, "--limit": args.limit is not None,
            "--interval": args.interval != 1.5, "--max-pages": args.max_pages != ALBUM_MAX_PAGES,
        }
        bad = [k for k, v in batch_only.items() if v]
        if bad:
            ap.error("单篇模式不支持：" + "、".join(bad))
    return args


def main(argv=None):
    args = build_parser(argv)
    try:
        if args.url:
            return run_single(args)
        return run_batch(args)
    except KeyboardInterrupt:
        print("\n[中断]", file=sys.stderr)
        return 130
    except WechatError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
