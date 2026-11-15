#!/usr/bin/env python3
"""tome-video: 小红书笔记 → 结构化素材包。

- 图文笔记：取原图、视觉模型逐字 OCR，产出 note.json + images/ + ocr/
- 视频笔记：解析元数据后自动转交 video_note.py（下载 → 转写 → 抽帧 → 视觉 → 对齐）

小红书笔记页需要链接里带 xsec_token（App「分享 → 复制链接」自带）。
服务端数据只在移动端 UA 下渲染；脚本用 iPhone UA 抓 window.__INITIAL_STATE__。

用法:
  python3 xhs_note.py --url <小红书链接>
  python3 xhs_note.py --url <链接> --no-ocr        # 只取图和元数据
  python3 xhs_note.py --url <链接> --meta-only     # 只看元数据，不下载/不转写
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request as urlreq

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
OLLAMA = "http://localhost:11434"
VL_MODEL = "qwen3-vl:32b-instruct"
OCR_PROMPT = (
    "你是 OCR 引擎。逐字转写这张小红书图文卡片里的所有可见文字，"
    "按从上到下、从左到右的自然阅读顺序，保留标题、列表、表格和层级；"
    "不要概括、不要解释、不要翻译、不要遗漏小字，看不清的字用 [?]。只输出文字。"
)


def slugify(s: str, n=40) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", (s or "").strip()).strip("-")
    return s[:n] or "xhs-note"


def workdir_for(title: str, key: str) -> Path:
    slug = slugify(title)
    h = hashlib.md5(key.encode()).hexdigest()[:6]
    return Path(os.path.expanduser(f"~/.cache/tome-video/{slug}-{h}"))


def http_get(url: str, referer="https://www.xiaohongshu.com/", timeout=30) -> bytes:
    req = urlreq.Request(url, headers={
        "User-Agent": MOBILE_UA,
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urlreq.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_state(url: str) -> dict:
    html = http_get(url).decode("utf-8", "ignore")
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', html, re.S)
    if not m:
        raise SystemExit("页面里没有 __INITIAL_STATE__。链接可能失效，或小红书改了页面结构。")
    try:
        return json.loads(re.sub(r'\bundefined\b', 'null', m.group(1)))
    except json.JSONDecodeError as e:
        raise SystemExit(f"解析页面状态失败：{e}。请重新分享链接后再试。")


def pick_note(state: dict) -> dict:
    """兼容移动端 SSR（noteData.data.noteData）和桌面端（note.noteDetailMap）。"""
    mobile = (state.get("noteData") or {}).get("data") or {}
    note = mobile.get("noteData") or mobile.get("note")
    if note:
        return note
    detail = (state.get("note") or {}).get("noteDetailMap") or {}
    for v in detail.values():
        n = v.get("note") or v
        if isinstance(n, dict) and (n.get("title") or n.get("desc") or n.get("imageList")):
            return n
    raise SystemExit(
        "没拿到笔记数据。常见原因：\n"
        "  1) 链接缺少 xsec_token —— 在 App 里「分享 → 复制链接」重新取；\n"
        "  2) token 过期 —— 重新分享；\n"
        "  3) 笔记已删除或仅自己可见。"
    )


def pick_image_url(im: dict) -> str:
    if im.get("url"):
        return im["url"]
    for item in im.get("infoList") or []:
        if item.get("url"):
            return item["url"]
    return im.get("urlDefault") or im.get("urlPre") or ""


def normalize(note: dict, url: str) -> dict:
    user = note.get("user") or {}
    inter = note.get("interactInfo") or {}
    ts = note.get("time")
    published = ""
    if isinstance(ts, (int, float)):
        published = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    note_id = note.get("noteId") or ""
    if not note_id:
        m = re.search(r'/(?:explore|discovery/item)/([0-9a-f]+)', url)
        note_id = m.group(1) if m else ""
    return {
        "note_id": note_id,
        "type": note.get("type") or ("video" if note.get("video") else "image"),
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
        "author": user.get("nickName") or "",
        "author_id": user.get("userId") or "",
        "avatar": user.get("avatar") or "",
        "published": published,
        "source_url": url,
        # 分享链接里的 xsec_token 会过期；长期引用用不带 token 的 canonical
        "canonical_url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else url,
        "platform": "小红书",
        "stats": {
            "点赞": inter.get("likedCount", ""),
            "收藏": inter.get("collectedCount", ""),
            "评论": inter.get("commentCount", ""),
            "分享": inter.get("shareCount", ""),
        },
        "tags": [t.get("name") for t in (note.get("tagList") or []) if t.get("name")],
        "images": [u for u in (pick_image_url(im) for im in (note.get("imageList") or [])) if u],
        "has_video": bool(note.get("video")),
    }


def download_images(urls: list, outdir: Path, max_images=0) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, u in enumerate(urls[:max_images or None], 1):
        ext = ".jpg"
        if ".png" in u.lower():
            ext = ".png"
        dst = outdir / f"{i:03d}{ext}"
        last = None
        for attempt in range(3):
            try:
                dst.write_bytes(http_get(u))
                paths.append(str(dst))
                print(f"  [image {i}/{len(urls)}] {dst.name} {dst.stat().st_size // 1024} KB", flush=True)
                last = None
                break
            except Exception as e:
                last = e
                time.sleep(1 + attempt)
        if last:
            print(f"  [image {i}/{len(urls)}] FAIL {last}", flush=True)
    return paths


def ocr_one(path: str, timeout=300) -> str:
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    payload = {
        "model": VL_MODEL,
        "prompt": OCR_PROMPT,
        "images": [b64],
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.1, "num_predict": 1200},
    }
    req = urlreq.Request(f"{OLLAMA}/api/generate",
                         data=json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urlreq.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()).get("response", "").strip()
        except Exception as e:
            if attempt == 2:
                return f"(OCR 失败: {e})"
            time.sleep(2)
    return ""


def ocr_images(paths: list, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    result = {}
    for i, p in enumerate(paths, 1):
        dst = outdir / (Path(p).stem + ".txt")
        if dst.exists():
            result[Path(p).name] = dst.read_text(encoding="utf-8")
            print(f"  [ocr {i}/{len(paths)}] skip {dst.name}", flush=True)
            continue
        t0 = time.time()
        text = ocr_one(p)
        # 视觉模型偶尔会把结果包在 ``` 围栏里，写进笔记前剥掉
        text = re.sub(r'^```[^\n]*\n', '', text)
        text = re.sub(r'\n```\s*$', '', text).strip()
        dst.write_text(text, encoding="utf-8")
        result[Path(p).name] = text
        print(f"  [ocr {i}/{len(paths)}] {dst.name} {len(text)} 字 {time.time()-t0:.0f}s", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--workdir")
    ap.add_argument("--no-ocr", action="store_true", help="只取图和元数据，不做 OCR")
    ap.add_argument("--meta-only", action="store_true", help="只解析元数据，不下载、不转交视频")
    ap.add_argument("--max-images", type=int, default=0, help="最多下载几张图，0=全部")
    args = ap.parse_args()

    url = args.url
    if "xsec_token=" not in url:
        raise SystemExit(
            "链接缺少 xsec_token，服务端不会返回笔记数据。\n"
            "请在小红书 App 里打开笔记 → 分享 → 复制链接，再把整段内容发过来。"
        )

    state = fetch_state(url)
    note = normalize(pick_note(state), url)
    workdir = Path(args.workdir) if args.workdir else workdir_for(note["title"] or note["note_id"], url)
    workdir.mkdir(parents=True, exist_ok=True)
    work_json = workdir / "note.json"
    note["workdir"] = str(workdir)
    work_json.write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"标题: {note['title'] or '(无标题)'}")
    print(f"作者: {note['author'] or '(未知)'}  ·  {note['published'] or '(未知日期)'}  ·  {note['type']}")
    print(f"互动: {note['stats']}")
    print(f"标签: {' / '.join(note['tags']) or '(无)'}")
    print(f"素材包: {work_json}")

    if args.meta_only:
        return

    if note["type"] == "video" or note["has_video"]:
        print("\n检测到视频笔记，转交 video_note.py（下载 → 转写 → 抽帧 → 视觉 → 对齐）")
        cmd = [
            sys.executable, str(Path(__file__).with_name("video_note.py")),
            "--url", url,
            "--title", note["title"], "--author", note["author"],
            "--source-url", url, "--workdir", str(workdir),
        ]
        sys.exit(subprocess.run(cmd).returncode)

    if not note["images"]:
        raise SystemExit("这条笔记既没有图片也没有视频，无法生成图文笔记。")

    print(f"\n下载 {len(note['images'])} 张图 → {workdir / 'images'}")
    local_images = download_images(note["images"], workdir / "images", args.max_images)
    note["local_images"] = local_images

    if not args.no_ocr and local_images:
        print(f"\nOCR（{VL_MODEL}，约 10–20s/张）→ {workdir / 'ocr'}")
        note["ocr"] = ocr_images(local_images, workdir / "ocr")
    else:
        note["ocr"] = {}

    work_json.write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成。素材包: {work_json}")
    print("下一步：读 note.json，按 tome-video SKILL.md 的图文笔记结构组装 Obsidian 笔记。")


if __name__ == "__main__":
    main()
