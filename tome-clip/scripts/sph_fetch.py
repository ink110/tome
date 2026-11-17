#!/usr/bin/env python3
"""视频号采集层：分享链接 → 元数据 → mp4 → video_note.py 素材包。

设计目标：把「拿到 mp4」这件事从 tome-clip 里独立出来，按可靠性级联：

1. 本地文件（--file）：人工投送，永远可用。
2. 最近下载（--from-downloads）：给 Yuanbao / 微信 PC 等外部下载器做后处理。
3. 同源原片（默认）：用标题在 YouTube 搜索同名视频，核对标题、时长、日期后
   用 yt-dlp 下载。零配置、全自动，覆盖大量「视频号搬运 / 同步发布」内容。
4. 找不到时明确退出，并打印 Yuanbao / 微信 PC / 手动投送三条兜底，不静默失败。

拿到 mp4 后默认继续跑 video_note.py（转写 + 稳定帧 + 视觉描述），产物是
work.json，由 Claude 组装 Obsidian 图文笔记。
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
NODE = shutil.which("node")
NPM = shutil.which("npm")
YTDLP = os.environ.get("YTDLP") or shutil.which("yt-dlp") or str(Path.home() / ".local" / "bin" / "yt-dlp")
FFPROBE = shutil.which("ffprobe") or str(Path.home() / ".local" / "bin" / "ffprobe")
DEFAULT_PROXY = os.environ.get("YTDLP_PROXY") or os.environ.get("HTTPS_PROXY") or ""


def log(msg: str) -> None:
    print(f"[sph_fetch] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=timeout)


def slugify(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = re.sub(r"#\S+", "", text)
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:limit] or "sph-video"


def default_workdir(title: str, url: str) -> Path:
    import hashlib
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    path = Path.home() / ".cache" / "tome-clip" / f"{slugify(title)}-{digest}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_meta(workdir: Path) -> dict:
    path = workdir / "meta.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def capture_meta(url: str, workdir: Path) -> dict:
    """用 sph_meta.js 抓视频号公开元数据。失败时返回空 dict，不阻塞。"""
    workdir.mkdir(parents=True, exist_ok=True)
    meta_path = workdir / "meta.json"
    old = load_meta(workdir)
    if old.get("title"):
        return old
    if not NODE:
        log("没有 node，跳过视频号元数据抓取")
        return {}
    scripts = HERE
    if not (scripts / "node_modules" / "puppeteer-core").exists():
        if not NPM:
            log("缺少 puppeteer-core 且没有 npm，跳过元数据抓取")
            return {}
        log("安装 puppeteer-core（首次）…")
        run([NPM, "install", "--no-audit", "--no-fund"], cwd=scripts, timeout=300)
    log("抓取视频号元数据…")
    r = run([NODE, str(scripts / "sph_meta.js"), url, str(meta_path)], cwd=scripts, timeout=120)
    if r.returncode != 0 or not meta_path.exists():
        log(f"元数据抓取失败：{(r.stderr or r.stdout)[-300:]}")
        return {}
    return load_meta(workdir)


def normalize_title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"#\S+", "", text)
    text = re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’（）()【】\[\]《》<>|/\\—\-_]+", "", text)
    return text.lower()


def primary_title(title: str) -> str:
    """取标题的第一个完整句子，避免把整段简介都拿去搜索。"""
    parts = re.split(r"[！!？?。|]", title or "")
    first = (parts[0] or "").strip()
    return first if len(first) >= 6 else (title or "").strip()


def title_score(query: str, candidate: str) -> float:
    q, c = normalize_title(query), normalize_title(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 100.0
    ratio = difflib.SequenceMatcher(None, q, c).ratio() * 100
    if q in c or c in q:
        ratio = max(ratio, 92.0)
    return ratio


def parse_upload_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def parse_published(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def search_youtube(title: str, proxy: str, meta: dict) -> list[dict]:
    """搜 YouTube 候选。返回按匹配度排序的列表。"""
    queries = []
    first = primary_title(title)
    if first and first != title:
        queries.append(first)
    queries.append(title)
    seen: dict[str, dict] = {}
    for query in dict.fromkeys(queries):
        cmd = [YTDLP, f"ytsearch5:{query}", "--dump-json",
               "--no-warnings", "--skip-download"]
        if proxy:
            cmd[1:1] = ["--proxy", proxy]
        r = run(cmd, timeout=180)
        if r.returncode != 0:
            log(f"YouTube 搜索失败：{(r.stderr or r.stdout)[-200:]}")
            continue
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            vid = item.get("id")
            if not vid or vid in seen:
                continue
            cand_title = item.get("title") or ""
            score = title_score(title, cand_title)
            score = max(score, title_score(first, cand_title))
            duration = item.get("duration") or 0
            if duration and 15 <= float(duration) <= 1800:
                score += 3
            published = parse_published(meta.get("published") or "")
            uploaded = parse_upload_date(item.get("upload_date") or "")
            if published and uploaded and abs((uploaded - published).days) <= 30:
                score += 8
            item["_score"] = round(score, 1)
            item["_url"] = item.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
            seen[vid] = item
    return sorted(seen.values(), key=lambda x: x.get("_score", 0), reverse=True)


def probe(path: Path) -> dict:
    if not path.exists():
        return {}
    r = run([FFPROBE, "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,width,height",
             "-of", "json", str(path)], timeout=30)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def download_url(url: str, workdir: Path, proxy: str, label: str = "") -> Path | None:
    out = workdir / "source.%(ext)s"
    cmd = [YTDLP, "--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", str(out), url]
    if proxy:
        cmd[1:1] = ["--proxy", proxy]
    log(f"下载原片：{label or url}")
    r = run(cmd, timeout=900)
    if r.returncode != 0:
        log(f"下载失败：{(r.stderr or r.stdout)[-500:]}")
        return None
    candidates = sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        info = probe(path)
        if info.get("format", {}).get("duration"):
            return path
    log("下载完成但没有探测到有效视频流")
    return None


def download_youtube(item: dict, workdir: Path, proxy: str) -> Path | None:
    return download_url(item["_url"], workdir, proxy, item.get("title") or "")


def normalize_sph_url(url: str) -> str:
    """channels.weixin.qq.com/finder-preview/pages/sph?id=xxx → weixin.qq.com/sph/xxx"""
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url or "")
    if "channels.weixin.qq.com" in (url or "") and m:
        return f"https://weixin.qq.com/sph/{m.group(1)}"
    return url


def sph_key(url: str) -> str:
    m = re.search(r"/sph/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else url


def source_cache_path() -> Path:
    return Path.home() / ".cache" / "tome-clip" / "sph-sources.json"


def load_sources() -> dict:
    path = source_cache_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_sources(data: dict) -> None:
    path = source_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_download() -> Path | None:
    roots = [Path.home() / "Downloads" / "视频号", Path.home() / "Downloads"]
    files = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.mp4"):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                pass
        if files:
            break
    if not files:
        return None
    files.sort(reverse=True)
    return files[0][1]


def run_pipeline(source: Path, workdir: Path, meta: dict) -> int:
    cmd = [sys.executable, str(HERE / "video_note.py"), "--file", str(source),
           "--workdir", str(workdir)]
    meta_path = workdir / "meta.json"
    if meta_path.exists():
        cmd += ["--sph-meta", str(meta_path)]
    else:
        if meta.get("title"):
            cmd += ["--title", meta["title"]]
        if meta.get("author"):
            cmd += ["--author", meta["author"]]
        if meta.get("source_url"):
            cmd += ["--source-url", meta["source_url"]]
    log("运行 video_note.py（转写 + 抽帧 + 视觉描述）…")
    r = subprocess.run(cmd, cwd=str(HERE), text=True)
    if r.returncode != 0:
        return r.returncode
    work_json = workdir / "work.json"
    log(f"素材包：{work_json}")
    log("下一步：让 Claude 按 tome-clip 的视频笔记体例组装 Obsidian 笔记。")
    return 0


def print_fallbacks(url: str) -> None:
    log("没有找到可自动下载的同源原片。按可靠性从高到低，还有三条路：")
    print("""
1) 腾讯元宝官方链路（一次扫码，之后全自动）：
   安装 download-wechat-channels，扫码登录 yuanbao.tencent.com，
   再跑 channels.py download '<链接>' --quality auto
   下载完成后：python3 sph_fetch.py --url <链接> --from-downloads

2) 微信 PC + 本地代理（兼容性最强）：
   安装 ltaoo/wx_channels_download 或 ChantillyAn/wechat-video-skill，
   在电脑微信里播放一次视频，再从浏览历史下载并解密。

3) 手动投送：
   电脑微信保存 / 手机保存后传回 / 录屏，然后：
   python3 sph_fetch.py --url <链接> --file <mp4>

注意：视频号匿名分享页只返回封面和元数据，不返回视频流；
Chrome 和 Eagle 插件都拿不到媒体，必须走登录态或微信客户端。
""")


def main() -> None:
    ap = argparse.ArgumentParser(description="视频号链接 → mp4 → tome-clip 素材包")
    ap.add_argument("--url", required=True, help="weixin.qq.com/sph/... 分享链接")
    ap.add_argument("--workdir", help="工作目录（默认 ~/.cache/tome-clip/<slug>-<hash>）")
    ap.add_argument("--file", help="已有的本地视频，跳过下载")
    ap.add_argument("--from-downloads", action="store_true", help="使用 ~/Downloads/视频号 最新 mp4")
    ap.add_argument("--mode", choices=["auto", "original", "local"], default="auto",
                    help="auto=本地/下载/原片依次尝试；original=只找同源原片；local=只认 --file/--from-downloads")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="yt-dlp 代理，如 http://127.0.0.1:7890")
    ap.add_argument("--reuse", action="store_true", default=True, help="复用 workdir 里已有的 source.mp4")
    ap.add_argument("--no-reuse", dest="reuse", action="store_false", help="强制重新下载")
    ap.add_argument("--title", help="元数据抓取失败时用于搜索的标题")
    ap.add_argument("--youtube-url", help="已知同源原片的 YouTube 链接；会写入映射缓存")
    ap.add_argument("--search-only", action="store_true", help="只搜索同源原片，不下载")
    ap.add_argument("--no-pipeline", action="store_true", help="只拿 mp4，不跑转写/抽帧")
    args = ap.parse_args()
    args.url = normalize_sph_url(args.url)

    base = Path.home() / ".cache" / "tome-clip"
    import hashlib
    digest = hashlib.sha1(args.url.encode("utf-8")).hexdigest()[:8]
    hashdir = base / f"sph-{digest}"
    meta = capture_meta(args.url, hashdir)
    title = meta.get("title") or args.title or args.url
    workdir = Path(args.workdir) if args.workdir else default_workdir(title, args.url)
    workdir.mkdir(parents=True, exist_ok=True)
    if workdir != hashdir and (hashdir / "meta.json").exists():
        if not (workdir / "meta.json").exists():
            shutil.move(str(hashdir / "meta.json"), str(workdir / "meta.json"))
        else:
            (hashdir / "meta.json").unlink()
    if workdir != hashdir:
        try:
            hashdir.rmdir()
        except OSError:
            pass
    meta = load_meta(workdir) or meta

    cache = load_sources()
    key = sph_key(args.url)
    if args.youtube_url:
        cache[key] = args.youtube_url
        save_sources(cache)
    cached = cache.get(key)

    if args.search_only:
        if cached:
            print(f"已登记原片：{cached}")
        candidates = search_youtube(title, args.proxy, meta)
        for item in candidates[:8]:
            print(f"{item.get('_score'):>6}  {item.get('duration') or '?'}s  "
                  f"{item.get('upload_date') or '?'}  {item.get('title')}\n        {item.get('_url')}")
        return

    source: Path | None = None
    if args.file:
        source = Path(args.file).expanduser().resolve()
        if not source.exists():
            log(f"文件不存在：{source}")
            sys.exit(2)
    elif args.from_downloads:
        source = latest_download()
        if not source:
            log("~/Downloads 里没有找到 mp4")
            sys.exit(2)
        log(f"使用最近下载：{source}")
    elif args.mode in ("auto", "original"):
        existing = workdir / "source.mp4"
        if args.reuse and probe(existing).get("format", {}).get("duration"):
            source = existing
            log(f"复用已下载视频：{source}")
        elif cached:
            log(f"使用已登记的原片映射：{cached}")
            source = download_url(cached, workdir, args.proxy, title)
        else:
            candidates = search_youtube(title, args.proxy, meta)
            if candidates:
                log("YouTube 候选：")
                for item in candidates[:5]:
                    log(f"  {item.get('_score'):>5}  {item.get('duration') or '?'}s  {item.get('title')}")
                best = candidates[0]
                if best.get("_score", 0) >= 80:
                    source = download_youtube(best, workdir, args.proxy)
                    if source:
                        cache[key] = best["_url"]
                        save_sources(cache)
                        log(f"已登记原片映射：{best['_url']}")
                else:
                    log("候选标题匹配度不足，放弃自动下载，避免下错片。")

    if not source:
        print_fallbacks(args.url)
        sys.exit(2)

    # 统一到 workdir/source.mp4，方便断点续跑
    target = workdir / "source.mp4"
    if source.resolve() != target.resolve():
        if target.exists() and not (args.reuse and probe(target).get("format", {}).get("duration")):
            target.unlink()
        if not target.exists():
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
    log(f"视频：{target}")
    info = probe(target)
    duration = info.get("format", {}).get("duration")
    if duration:
        log(f"时长：{float(duration):.1f}s")

    if args.no_pipeline:
        return
    sys.exit(run_pipeline(target, workdir, meta))


if __name__ == "__main__":
    main()
