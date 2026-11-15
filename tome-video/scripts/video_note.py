#!/usr/bin/env python3
"""tome-video: 视频 → 结构化素材包（口播转写 + 关键帧 + 视觉描述 + 时间对齐）。

只做机器活，不写笔记。产物 work.json 交给 Claude 组装 Obsidian 图文笔记。

用法:
  python3 video_note.py --url <小红书/视频号链接> [--topic 自媒体收集]
  python3 video_note.py --file /path/to/video.mp4 [--title 标题] [--author 作者]
  python3 video_note.py --url ... --sph-meta meta.json   # 视频号：先用 sph_meta.js 抓的元数据

阶段（可单独重跑）: download → transcribe → frames → vision → align
"""
import argparse, json, os, re, subprocess, sys, time, hashlib
from pathlib import Path
from urllib import request as urlreq

WHISPER_PY = os.path.expanduser("~/.whisper-venv/bin/python3")
MLX_WHISPER = os.path.expanduser("~/.local/bin/mlx_whisper")
WHISPER_MODEL = os.path.expanduser("~/.models/whisper-large-v3-turbo")
if not Path(WHISPER_MODEL).exists():
    WHISPER_MODEL = os.path.expanduser("~/models/whisper-large-v3-turbo")
FFMPEG = os.path.expanduser("~/.local/bin/ffmpeg")
FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")
OLLAMA = "http://localhost:11434"
VL_MODEL = "qwen3-vl:32b-instruct"

SCENE_THRESH = 0.35      # 场景切换阈值；帧太少会自动调低
MIN_FRAME_GAP = 2.0      # 两帧最小间隔（秒）
MAX_FRAMES = 40          # 关键帧上限（控制视觉模型耗时）
VL_PROMPT = (
    "这是视频里截出的一帧。请用中文描述画面内容，要求："
    "1) 如果是幻灯片/图表/界面/文字板，逐字提取标题和要点文字（尽量完整，这是最重要的）；"
    "2) 如果是人物口播或演示画面，一句话说清场景和在演示什么；"
    "3) 不要评价、不要猜测，100 字以内。"
)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def slugify(s: str, n=40) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", (s or "").strip()).strip("-")
    return s[:n] or "video"


# ---------- 1. 下载 / 元数据 ----------

def download(url: str, workdir: Path) -> dict:
    """返回 {video, meta}。支持 yt-dlp 认识的站点（小红书等）。"""
    out = str(workdir / "source.%(ext)s")
    r = sh([WHISPER_PY, "-m", "yt_dlp", "--no-playlist", "-o", out,
            "--write-info-json", "--no-warnings", url])
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:], file=sys.stderr)
        sys.exit("下载失败：yt-dlp 不认识或链接失效。视频号链接请改用 --file 手动投送（见 SKILL.md）。")
    info = json.loads((workdir / "source.info.json").read_text())
    vids = list(workdir.glob("source.*"))
    video = next(p for p in vids if p.suffix != ".json")
    canonical = ""
    if info.get("extractor_key") == "XiaoHongShu" and info.get("display_id"):
        # 分享链接里的 xsec_token 会过期；长期引用用不带 token 的 canonical
        canonical = f"https://www.xiaohongshu.com/explore/{info['display_id']}"
    return {"video": str(video), "meta": {
        "title": info.get("title") or "",
        "author": info.get("uploader") or info.get("channel") or "",
        "description": info.get("description") or "",
        "source_url": url,
        "canonical_url": canonical or url,
        "platform": info.get("extractor_key") or "",
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or url,
    }}


def probe_duration(video: str) -> float:
    r = sh([FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ---------- 2. 口播转写 ----------

def transcribe(video: str, workdir: Path) -> list:
    """mlx_whisper → segments[{start,end,text}]。"""
    out_json = workdir / "transcript.json"
    r = sh([MLX_WHISPER, video, "--model", WHISPER_MODEL, "--language", "zh",
            "--output-format", "json", "--output-dir", str(workdir),
            "--output-name", "transcript"])
    if r.returncode != 0 or not out_json.exists():
        print(r.stdout[-1500:]); print(r.stderr[-1500:], file=sys.stderr)
        sys.exit("转写失败：mlx_whisper 报错。")
    data = json.loads(out_json.read_text())
    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2),
             "text": s["text"].strip()} for s in data.get("segments", [])]
    if not segs:
        sys.exit("转写结果为空：视频可能没有语音。")
    return segs


# ---------- 3. 关键帧抽取 ----------

def extract_frames(video: str, duration: float, frames_dir: Path) -> list:
    """场景切换检测抽帧，返回 [{file, t}]。帧太少自动降低阈值重试。"""
    ensure_dir(frames_dir)
    for thresh in (SCENE_THRESH, 0.25, 0.15):
        for p in frames_dir.glob("*.jpg"):
            p.unlink()
        cmd = [FFMPEG, "-hide_banner", "-i", video,
               "-vf", f"select='gt(scene,{thresh})+eq(n,0)',showinfo,scale=960:-2",
               "-fps_mode", "vfr", "-q:v", "3", str(frames_dir / "f%04d.jpg"), "-y"]
        r = sh(cmd)
        # showinfo 里拿每帧 pts_time，与输出文件一一对应
        times = [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]
        files = sorted(frames_dir.glob("f*.jpg"))
        frames = [{"file": str(f), "t": times[i] if i < len(times) else 0.0}
                  for i, f in enumerate(files)]
        # 去掉间隔太近的帧
        kept, last = [], -1e9
        for fr in frames:
            if fr["t"] - last >= MIN_FRAME_GAP:
                kept.append(fr); last = fr["t"]
            else:
                Path(fr["file"]).unlink(missing_ok=True)
        frames = kept
        if len(frames) >= 8 or thresh == 0.15:
            break
    # 兜底：纯说话视频几乎没有场景切换 → 均匀采样
    if len(frames) < 4 and duration > 0:
        for p in frames_dir.glob("*.jpg"):
            p.unlink()
        n = min(12, max(4, int(duration // 30)))
        frames = []
        for i in range(n):
            t = duration * (i + 0.5) / n
            # 从 f0001 开始编号，避免后面重命名时和 f0000 互相覆盖
            fp = frames_dir / f"f{i + 1:04d}.jpg"
            sh([FFMPEG, "-hide_banner", "-ss", f"{t:.2f}", "-i", video,
                "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(fp), "-y"])
            if fp.exists():
                frames.append({"file": str(fp), "t": round(t, 2)})
    # 超上限：均匀降采样
    if len(frames) > MAX_FRAMES:
        step = len(frames) / MAX_FRAMES
        keep = [frames[int(i * step)] for i in range(MAX_FRAMES)]
        drop = {f["file"] for f in frames} - {f["file"] for f in keep}
        for d in drop:
            Path(d).unlink(missing_ok=True)
        frames = keep
    # 重命名连续编号，避免 obsidian 里乱序。
    # 两阶段重命名：a→b 时 b 可能还是下一帧的源文件，直接 rename 会互相覆盖。
    staged = []
    for i, fr in enumerate(frames, 1):
        tmp = frames_dir / f"_tmp{i:04d}.jpg"
        Path(fr["file"]).rename(tmp)
        staged.append((tmp, fr["t"]))
    final = []
    for i, (tmp, t) in enumerate(staged, 1):
        nf = frames_dir / f"f{i:04d}.jpg"
        tmp.rename(nf)
        final.append({"file": str(nf), "t": t})
    return final


# ---------- 4. 视觉描述（本地多模态） ----------

def vision_describe(frames: list) -> list:
    for i, fr in enumerate(frames, 1):
        import base64
        b64 = base64.b64encode(Path(fr["file"]).read_bytes()).decode()
        payload = {"model": VL_MODEL, "prompt": VL_PROMPT, "images": [b64],
                   "stream": False, "options": {"temperature": 0.1}}
        req = urlreq.Request(f"{OLLAMA}/api/generate",
                             data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json"})
        try:
            with urlreq.urlopen(req, timeout=300) as resp:
                fr["vl"] = json.loads(resp.read()).get("response", "").strip()
        except Exception as e:
            fr["vl"] = f"(视觉描述失败: {e})"
        print(f"  帧 {i}/{len(frames)} t={fr['t']:.0f}s → {fr['vl'][:40]}…", flush=True)
    return frames


# ---------- 5. 时间对齐 ----------

def align(frames: list, segs: list, before=8.0, after=4.0) -> list:
    """每帧取 [t-before, t+after] 窗口内的口播文本。"""
    for fr in frames:
        lo, hi = fr["t"] - before, fr["t"] + after
        txt = "".join(s["text"] for s in segs if s["end"] >= lo and s["start"] <= hi)
        fr["speech"] = txt.strip()
    return frames


def main():
    global MAX_FRAMES
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--file")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--sph-meta", help="视频号元数据 json（sph_meta.js 产物）")
    ap.add_argument("--workdir", help="默认 ~/.cache/tome-video/<slug>-<hash>")
    ap.add_argument("--from-stage", default="download",
                    choices=["download", "transcribe", "frames", "vision", "align"])
    ap.add_argument("--max-frames", type=int, default=0, help=f"关键帧上限，默认 {MAX_FRAMES}")
    args = ap.parse_args()

    if args.max_frames:
        MAX_FRAMES = args.max_frames

    stages = ["download", "transcribe", "frames", "vision", "align"]
    start = stages.index(args.from_stage)

    # 工作目录
    key = args.url or args.file
    slug = slugify(args.title or Path(key).stem)
    h = hashlib.md5(key.encode()).hexdigest()[:6]
    workdir = Path(args.workdir or os.path.expanduser(f"~/.cache/tome-video/{slug}-{h}"))
    ensure_dir(workdir)
    work_json = workdir / "work.json"
    state = json.loads(work_json.read_text()) if work_json.exists() else {}

    # 1) download
    if start <= 0:
        if args.url:
            if "weixin.qq.com/sph" in args.url or "channels.weixin.qq.com" in args.url:
                sys.exit("视频号链接拿不到视频流（匿名接口只返回封面）。"
                         "请用桌面微信播放并借助 res-downloader 抓包保存 mp4 后，"
                         "改用 --file 投送；元数据可先用 scripts/sph_meta.js 抓取。")
            state.update(download(args.url, workdir))
        else:
            state["video"] = args.file
            state["meta"] = {"title": args.title or Path(args.file).stem,
                             "author": args.author, "description": "",
                             "source_url": args.source_url, "platform": "local",
                             "duration": None, "webpage_url": args.source_url}
        if args.sph_meta and Path(args.sph_meta).exists():
            sm = json.loads(Path(args.sph_meta).read_text())
            state["meta"].update({k: v for k, v in sm.items() if v})
            state["meta"]["platform"] = "微信视频号"
        if args.title:
            state["meta"]["title"] = args.title
        if args.author:
            state["meta"]["author"] = args.author
        state["meta"]["duration"] = state["meta"].get("duration") or probe_duration(state["video"])
        work_json.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"[download] {state['meta']['title']}  {state['meta']['duration']:.0f}s")

    # 2) transcribe
    if start <= 1:
        if not state.get("video"):
            sys.exit("断点续跑需要同目录下已有 work.json（含 video 路径）；首次请从 download 阶段开始。")
        t0 = time.time()
        state["segments"] = transcribe(state["video"], workdir)
        work_json.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        full = "".join(s["text"] for s in state["segments"])
        (workdir / "transcript.txt").write_text(full)
        print(f"[transcribe] {len(state['segments'])} 段, {len(full)} 字, {time.time()-t0:.0f}s")

    # 3) frames
    if start <= 2:
        frames_dir = workdir / "frames"
        state["frames"] = extract_frames(state["video"],
                                         state["meta"].get("duration") or probe_duration(state["video"]),
                                         frames_dir)
        work_json.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"[frames] {len(state['frames'])} 帧")

    # 4) vision
    if start <= 3:
        state["frames"] = vision_describe(state["frames"])
        work_json.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print("[vision] 完成")

    # 5) align
    if start <= 4:
        state["frames"] = align(state["frames"], state["segments"])
        work_json.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print("[align] 完成")

    print(f"\n工作目录: {workdir}")
    print(f"素材包:   {work_json}")


if __name__ == "__main__":
    main()
