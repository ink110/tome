#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asr_episode.py — 无 AI 字幕的单集，用本地 faster-whisper 转写后走同样的结构化生成

运行环境：用装了 faster-whisper 的 Python 运行（可用配置 tools.whisper_python 指定；还需 opencc）。
bf / batch_crawl / bilibili_to_md 只依赖标准库，在此环境下也能 import。

用法:
  python3 asr_episode.py BV1xxxxxxxxx --out <目录> --pages 10,19 [--prefix <系列名>]
"""
import sys, os, json, re, urllib.request, urllib.parse, tempfile
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bilibili_fetch as bf
import batch_crawl as bc
from bilibili_to_md import build_user_message

MODEL_PATH = os.path.expanduser(tc.get("tools.whisper_model", "~/whisper-models/small"))


def _load_asr_deps():
    """按需导入转写依赖，避免 --help 或只做采集时因为缺包直接报错。"""
    try:
        from faster_whisper import WhisperModel
        from opencc import OpenCC
    except ImportError as exc:
        raise SystemExit(
            "缺少转写依赖（faster-whisper / opencc）。\n"
            "只做普通采集不需要它们；要转写无字幕视频请先：\n"
            "  pip3 install faster-whisper opencc\n"
            "并确保用同一个 Python 运行本脚本（可用配置 tools.whisper_python 指定）。"
        ) from exc
    return WhisperModel, OpenCC


def get_meta_and_cid(bvid, page, cookie):
    view = bf.get_view(bvid, cookie)
    title = view.get("title", "")
    owner = view.get("owner", {}).get("name", "")
    duration = view.get("duration", 0)
    desc = view.get("desc", "").strip()
    cid = view.get("cid")
    pages = view.get("pages", [])
    if pages:
        idx = min(max(page, 1), len(pages)) - 1
        pg = pages[idx]
        cid = pg.get("cid", cid)
        part = pg.get("part", "")
        if part:
            title = part.replace("：：", "：")
        if pg.get("duration"):
            duration = pg.get("duration")
    season = view.get("ugc_season") or {}
    return {"bvid": bvid, "title": title, "owner": owner,
            "duration_sec": duration, "desc": desc,
            "pubdate": view.get("pubdate"), "cid": cid,
            "stat": view.get("stat", {}),
            "season_id": season.get("id"),
            "collection_title": season.get("title"),
            "asr": True}, cid


def download_audio(bvid, cid, cookie, img_key, sub_key, out_path):
    params = bf.wbi_sign({"bvid": bvid, "cid": cid, "fnval": "16"}, img_key, sub_key)
    url = "https://api.bilibili.com/x/player/wbi/playurl?" + urllib.parse.urlencode(params)
    data = bf._request(url, cookie)
    audio = data.get("data", {}).get("dash", {}).get("audio", [])
    if not audio:
        raise RuntimeError(f"无 dash.audio（playurl code={data.get('code')}）")
    audio = sorted(audio, key=lambda a: a.get("bandwidth", 0))
    base = audio[-1].get("baseUrl") or audio[-1].get("base_url")
    if base.startswith("//"):
        base = "https:" + base
    req = urllib.request.Request(base)
    req.add_header("User-Agent", bf.UA)
    req.add_header("Referer", "https://www.bilibili.com/")
    req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=180) as r, open(out_path, "wb") as f:
        f.write(r.read())
    return out_path


def transcribe(audio_path):
    WhisperModel, OpenCC = _load_asr_deps()
    cc = OpenCC("t2s")
    m = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8")
    segs, info = m.transcribe(audio_path, language="zh", beam_size=5, vad_filter=True)
    lines = []
    for s in segs:
        mm, ss = int(s.start // 60), int(s.start % 60)
        txt = cc.convert(s.text.strip())
        if txt:
            lines.append(f"[{mm:02d}:{ss:02d}] {txt}")
    return lines


def main():
    argv = sys.argv[1:]

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            val = argv[i + 1]
            argv[:] = argv[:i] + argv[i + 2:]
            return val
        return default

    out_dir = opt("--out")
    pages_str = opt("--pages") or ""
    ep_str = opt("--ep")
    model = opt("--model")
    prefix = opt("--prefix") or ""
    raw_only = "--raw-only" in argv
    if raw_only:
        argv.remove("--raw-only")
    bc.PREFIX = prefix

    if not argv or not out_dir:
        print("用法: asr_episode.py <BV> --out <目录> --pages 10,19")
        print("      asr_episode.py <BV> --out <目录> --ep 104 --prefix <前缀>")
        print("      asr_episode.py <BV> --out <目录> --ep 104 --raw-only  # 只转写，不调模型")
        sys.exit(1)
    raw = argv[0].strip()
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    bvid = m.group(1) if m else raw
    pages = [int(p) for p in pages_str.split(",") if p.strip().isdigit()]
    ep = int(ep_str) if ep_str and ep_str.isdigit() else None
    if raw_only and ep is None:
        print("--raw-only 需要同时给 --ep，才能按集号落盘")
        sys.exit(1)
    if ep is not None and not pages:
        pages = [1]

    out_dir = os.path.expanduser(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    cookie = bf.load_cookie_header()
    if not cookie:
        sys.exit(1)
    nav = bf.get_nav(cookie)
    if not nav:
        sys.exit(1)
    wbi = nav.get("wbi_img", {})
    img_key = (wbi.get("img_url", "") or "").split("/")[-1].split(".")[0]
    sub_key = (wbi.get("sub_url", "") or "").split("/")[-1].split(".")[0]

    for page in pages:
        print(f"[p={page}] 下载音频 → ASR 转写中…", flush=True)
        try:
            meta, cid = get_meta_and_cid(bvid, page, cookie)
            if ep is not None:
                meta["episode"] = ep
            tmp = os.path.join(tempfile.gettempdir(), f"bili_{bvid}_p{page}.m4s")
            download_audio(bvid, cid, cookie, img_key, sub_key, tmp)
            lines = transcribe(tmp)
            os.remove(tmp)
            if not lines:
                raise RuntimeError("转写结果为空")
            if ep is not None:
                import crawl_collection as cc
                cc.write_raw(out_dir, prefix, ep, meta, lines)
                print(f"[ep={ep:03d}] 原文 + 元信息已落盘", flush=True)
            if raw_only:
                print(f"[ep={ep:03d}] raw-only，跳过结构化（交给 distill-only）", flush=True)
                continue
            print(f"[p={page}] 转写得 {len(lines)} 行，结构化中…", flush=True)
            user = build_user_message(meta, lines)
            md = bc.strip_transcript_section(bc.call_ollama_stream(model, bc.SYSTEM_PROMPT, user))
            if len(md.strip()) < 200:
                raise RuntimeError("模型输出过短")
        except Exception as e:
            print(f"[p={page}] 失败: {e}", flush=True)
            continue

        if ep is not None:
            from crawl_collection import clean_topic
            fname = f"{prefix}{ep:03d}：{clean_topic(meta.get('title', ''), ep)}.md"
        else:
            fname = bc.build_filename(page, meta["title"])
        path = os.path.join(out_dir, fname)
        source = (f"https://www.bilibili.com/video/{bvid}/" if ep is not None
                  else f"https://www.bilibili.com/video/{bvid}/?p={page}")
        episode_line = f"episode: {ep}\n" if ep is not None else ""
        frontmatter = (
            "---\n"
            f"title: {meta['title']}\n"
            f"up: {meta['owner']}\n"
            f"source: {source}\n"
            f"{episode_line}"
            f"date: {date.today().isoformat()}\n"
            "tags: [B站口播, ASR转写]\n"
            "---\n\n"
        )
        content = frontmatter + md + "\n\n---\n## 附：完整口播稿（带时间戳）\n" + "\n".join(lines) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[p={page}] ✅ 已保存: {fname}", flush=True)

    print("ASR 补录完成", flush=True)


if __name__ == "__main__":
    main()
