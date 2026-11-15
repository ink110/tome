#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili_fetch.py — 从 B站视频提取口播稿（AI 字幕）的「原始文本 + 元信息」

只负责「爬」，不负责「写文章」。生成结构化 Markdown 交给模型做，
提示词见同目录《格式化提示词.md》。

用法:
  python3 bilibili_fetch.py BV1xxxxxxxxx                 # 打印到终端
  python3 bilibili_fetch.py BV1xxxxxxxxx --out ~/tmp     # 同时存文件

输出文件（--out 时）:
  <输出目录>/<标题>_raw.txt       原始字幕（[mm:ss] 文本，喂给模型的输入）
  <输出目录>/<标题>_meta.json     元信息（标题/UP主/时长/简介）
  <输出目录>/subtitle.json        字幕原始 JSON（调试用）

依赖: 仅用 Python 标准库；cookie 文件路径由配置决定（默认 ~/.bilibili-cookies.txt），
Netscape 格式（含 SESSDATA + buvid3 + buvid4），获取方式见 SKILL.md。
"""
import sys, os, json, time, hashlib, re, urllib.parse, urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

COOKIE_FILE = tc.cookie_file()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5,
         49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55,
         40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57,
         62, 11, 36, 20, 34, 44, 52]


def load_cookie_header():
    if not os.path.exists(COOKIE_FILE):
        sys.stderr.write(f"[错误] 找不到 cookie 文件: {COOKIE_FILE}\n")
        sys.stderr.write("请按 SKILL.md 的步骤导出 B站 SESSDATA 等 cookie 到该文件。\n")
        return None
    pairs = []
    with open(COOKIE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 7:
                # Netscape 格式: domain flag path secure expiry name value
                name, value = parts[-2], parts[-1]
            elif "=" in line:
                name, value = line.split("=", 1)
            else:
                continue
            if name and value:
                pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _request(url, cookie=None, raw=False):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://www.bilibili.com/")
    if cookie:
        req.add_header("Cookie", cookie)
    # 使用默认 TLS 证书校验，不关闭校验（保证 cookie 传输安全）
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    return body if raw else json.loads(body)


def get_nav(cookie):
    data = _request("https://api.bilibili.com/x/web-interface/nav", cookie)
    if data.get("code") != 0 or not data.get("data", {}).get("isLogin"):
        sys.stderr.write(f"[错误] 未登录: code={data.get('code')} msg={data.get('message')}\n")
        sys.stderr.write("cookie 已失效，需重新获取 SESSDATA（见 SKILL.md）。\n")
        return None
    return data["data"]


def get_view(bvid, cookie):
    data = _request(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", cookie)
    if data.get("code") != 0:
        sys.stderr.write(f"[错误] view 接口: code={data.get('code')} msg={data.get('message')}\n")
        return None
    return data["data"]


def wbi_sign(params, img_key, sub_key):
    mixin_key = "".join((img_key + sub_key)[i] for i in MIXIN)[:32]
    params = dict(params)
    params["wts"] = round(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


def get_subtitle_url(bvid, cid, img_key, sub_key, cookie):
    params = wbi_sign({"bvid": bvid, "cid": cid}, img_key, sub_key)
    url = "https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(params)
    data = _request(url, cookie)
    if data.get("code") != 0:
        sys.stderr.write(f"[错误] wbi/v2: code={data.get('code')} msg={data.get('message')}\n")
        return None
    subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
    if not subtitles:
        return None
    for s in subtitles:
        lan = (s.get("lan") or "").lower()
        if lan.startswith("zh") or "zh" in lan:
            return s["subtitle_url"]
    return subtitles[0]["subtitle_url"]


def download_subtitle(url, cookie):
    if url.startswith("//"):
        url = "https:" + url
    raw = _request(url, cookie, raw=True)
    data = json.loads(raw)
    lines = []
    for item in data.get("body", []):
        frm = int(item.get("from", 0))
        mm, ss = divmod(frm, 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {item.get('content', '')}")
    return lines


def fmt_duration(sec):
    mm, ss = divmod(int(sec), 60)
    hh, mm = divmod(mm, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:60]


def main():
    argv = sys.argv[1:]
    out_dir = None
    page = None
    if "--out" in argv:
        i = argv.index("--out")
        out_dir = os.path.expanduser(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--page" in argv:
        i = argv.index("--page")
        page = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        sys.stderr.write("用法: python3 bilibili_fetch.py <BV号或链接> [--page N] [--out 目录]\n")
        sys.exit(1)

    raw = argv[0].strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    bvid = m.group(1) if m else raw
    # 链接里带 ?p=N 时自动识别分P
    if page is None:
        mp = re.search(r"[?&]p=(\d+)", raw)
        if mp:
            page = int(mp.group(1))
    if page is None:
        page = 1

    cookie = load_cookie_header()
    if not cookie:
        sys.exit(1)

    nav = get_nav(cookie)
    if not nav:
        sys.exit(1)
    wbi = nav.get("wbi_img", {})
    img_key = (wbi.get("img_url", "") or "").split("/")[-1].split(".")[0]
    sub_key = (wbi.get("sub_url", "") or "").split("/")[-1].split(".")[0]

    view = get_view(bvid, cookie)
    if not view:
        sys.exit(1)

    title = view.get("title", "")
    owner = view.get("owner", {}).get("name", "")
    duration = view.get("duration", 0)
    desc = view.get("desc", "").strip()
    cid = view.get("cid")
    # 多P视频：按 page 选对应的 cid、分P标题和时长
    pages = view.get("pages", [])
    if pages:
        idx = min(max(page, 1), len(pages)) - 1
        pg = pages[idx]
        cid = pg.get("cid", cid)
        part = pg.get("part", "")
        if part:
            title = part.replace("：：", "：")  # 多P直接用分P标题，避免与合集标题重复
        if pg.get("duration"):
            duration = pg.get("duration")

    season = view.get("ugc_season") or {}
    meta = {
        "bvid": bvid,
        "title": title,
        "owner": owner,
        "duration_sec": duration,
        "duration": fmt_duration(duration),
        "desc": desc,
        "pubdate": view.get("pubdate"),
        "cid": cid,
        "stat": view.get("stat", {}),
        "season_id": season.get("id"),
        "collection_title": season.get("title"),
    }

    sub_url = get_subtitle_url(bvid, cid, img_key, sub_key, cookie)
    if not sub_url:
        sys.stderr.write("[提示] 该视频无 AI 字幕（subtitle.subtitles 为空），需走 ASR 转写（见 SKILL.md）。\n")
        sys.stderr.write("已获取元信息:\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        sys.exit(2)

    lines = download_subtitle(sub_url, cookie)
    if not lines:
        sys.stderr.write("[错误] 字幕内容为空。\n")
        sys.exit(3)

    # 校验：条数与时长应大致匹配（粗略：平均 2~4 秒一句）
    if duration > 0 and (len(lines) < duration / 10 or len(lines) > duration):
        sys.stderr.write(f"[警告] 字幕条数 {len(lines)} 与时长 {duration}s 不匹配，请人工核对。\n")

    transcript = "\n".join(lines)

    print(f"=== 元信息 ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"\n=== 原始字幕（{len(lines)} 条）===")
    print(transcript)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        base = safe_name(title) or bvid
        raw_path = os.path.join(out_dir, f"{base}_raw.txt")
        meta_path = os.path.join(out_dir, f"{base}_meta.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(transcript + "\n")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"\n[已保存] {raw_path}")
        print(f"[已保存] {meta_path}")


if __name__ == "__main__":
    main()
