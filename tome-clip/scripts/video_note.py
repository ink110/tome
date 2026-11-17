#!/usr/bin/env python3
"""tome-clip: 视频 → 结构化素材包（口播转写 + 关键帧 + 视觉描述 + 时间对齐）。

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

SCENE_THRESH = 0.35      # 旧场景检测阈值（保留兼容）
MIN_FRAME_GAP = 2.0      # 两帧最小间隔（秒）
MAX_FRAMES = 40          # 关键帧上限（控制视觉模型耗时）
SAMPLE_SEC = 1.0         # 稳定帧检测的采样间隔
HASH_W, HASH_H = 33, 16  # dHash 尺寸（528 bit）
HASH_CROP_RATIO = 0.68   # 只哈希画面上方 68%，避开右下角人脸
HASH_THRESH = 90         # 判定「换页」的汉明距离
HASH_STABLE = 16         # 相邻采样判定「稳定」的汉明距离
HASH_MAX_WAIT = 6        # 换页后最多等几个采样找稳定帧
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


TOPIC_MARKERS = (
    "首先", "第一个是", "第二个是", "第三个是", "第四个是", "第五个是",
    "第六个是", "第七个是", "第八个是", "接下来", "下一个是", "最后是",
    "最后如果", "总结", "以上就是",
)
SUB_MARKERS = (
    "它的优势", "它的核心优势", "核心优势", "优势就是", "缺点也很明显",
    "但劣势", "它的劣势", "对于AI", "对AI", "这种方案适合", "它十分适合",
    "这套方法是和", "解决办法", "它的本质", "它的核心理念", "这种方式",
    "这套方案", "它的缺陷", "这种方案的优势",
)
STRONG_CUTS = ("但是", "所以", "而且", "因为", "如果", "当", "那么",
               "另外", "同时", "不过", "于是")


def paragraphize(text: str, min_len: int = 80, max_len: int = 260) -> list:
    """把无标点的 ASR 全文按主题词/转折词切成可读段落，不改原文。"""
    markers = [(m, True) for m in TOPIC_MARKERS] + [(m, False) for m in SUB_MARKERS]
    paras, start, i = [], 0, 0
    while i < len(text):
        hits = [(text.find(m, i), topic) for m, topic in markers]
        hits = [h for h in hits if h[0] >= 0]
        if not hits:
            break
        pos, topic = min(hits, key=lambda x: x[0])
        if pos > start and (topic or pos - start >= min_len):
            paras.append(text[start:pos].strip())
            start = pos
            i = pos + 1
        else:
            i = pos + 1
    if start < len(text):
        paras.append(text[start:].strip())
    # 长段落再按强转折词切，目标每段 < max_len
    out = []
    for para in paras:
        while len(para) > max_len:
            cut = -1
            for w in STRONG_CUTS:
                j = para.find(w, 100)
                if j > 0 and (cut < 0 or j < cut):
                    cut = j
            if cut < 0:
                break
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        out.append(para)
    # 段尾的转折词属于下一段，移过去
    out = [p for p in out if p]
    for i in range(len(out) - 1):
        for tail in ("但", "所以", "而且", "因为"):
            if out[i].endswith(tail):
                out[i] = out[i][:-len(tail)].strip()
                out[i + 1] = tail + out[i + 1]
                break
    return [p for p in out if p]


# ---------- 3. 关键帧抽取 ----------

def probe_size(video: str):
    r = sh([FFPROBE, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video])
    try:
        w, h = r.stdout.strip().split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def dhash(buf: bytes, width: int, height: int) -> int:
    value = 0
    for y in range(height):
        row = buf[y * width:(y + 1) * width]
        for x in range(width - 1):
            value = (value << 1) | (1 if row[x] > row[x + 1] else 0)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def hash_samples(video: str) -> list:
    """1fps 取灰度小图，只哈希上方 68%，避开右下角人脸。"""
    w, h = probe_size(video)
    crop_top = max(1, int(h * HASH_CROP_RATIO)) if h else 480
    vf = (f"fps=1/{SAMPLE_SEC},crop=iw:{crop_top}:0:0,"
          f"scale={HASH_W}:{HASH_H}:flags=area,format=gray")
    # rawvideo 是二进制，不能走 sh() 的 text=True
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", video,
                        "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    size = HASH_W * HASH_H
    return [r.stdout[i:i + size] for i in range(0, len(r.stdout) - size + 1, size)]


def choose_stable(samples: list) -> list:
    """换页后等到画面连续两帧稳定，再取该帧；返回采样下标。"""
    if not samples:
        return []
    hashes = [dhash(b, HASH_W, HASH_H) for b in samples]
    reps, chosen, cand = [0], [0], None
    for i in range(1, len(hashes)):
        d_rep = hamming(hashes[i], hashes[reps[-1]])
        d_prev = hamming(hashes[i], hashes[i - 1])
        if cand is None and d_rep > HASH_THRESH:
            cand = i
        if cand is not None:
            if (i - cand >= 1 and d_prev <= HASH_STABLE) or (i - cand >= HASH_MAX_WAIT):
                reps.append(i)
                chosen.append(i)
                cand = None
    return chosen


def extract_frames(video: str, duration: float, frames_dir: Path) -> list:
    """稳定帧优先：1fps 采样 + dHash，避免抓到淡入/动画中间态。"""
    ensure_dir(frames_dir)
    for p in frames_dir.glob("*.jpg"):
        p.unlink()
    times = []
    try:
        times = [round((i + 0.5) * SAMPLE_SEC, 2)
                 for i in choose_stable(hash_samples(video))]
        print(f"  [frames] 稳定帧候选 {len(times)} 个", flush=True)
    except Exception as e:
        print(f"  [frames] 稳定帧检测失败，转均匀采样：{e}", flush=True)
    # 兜底：画面几乎不变，或稳定帧检测失败 → 均匀采样
    if len(times) < 4 and duration > 0:
        n = min(24, max(8, int(duration // 45)))
        times = [round(duration * (i + 0.5) / n, 2) for i in range(n)]
        print(f"  [frames] 均匀采样 {len(times)} 个", flush=True)
    if len(times) > MAX_FRAMES:
        step = len(times) / MAX_FRAMES
        times = [times[int(i * step)] for i in range(MAX_FRAMES)]
    frames = []
    for i, t in enumerate(times, 1):
        fp = frames_dir / f"f{i:04d}.jpg"
        sh([FFMPEG, "-hide_banner", "-ss", f"{t:.2f}", "-i", video,
            "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(fp), "-y"])
        if fp.exists():
            frames.append({"file": str(fp), "t": t})
    return frames


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
    ap.add_argument("--workdir", help="默认 ~/.cache/tome-clip/<slug>-<hash>")
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
    workdir = Path(args.workdir or os.path.expanduser(f"~/.cache/tome-clip/{slug}-{h}"))
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
        paras = paragraphize(full)
        (workdir / "transcript_paragraphs.txt").write_text("\n\n".join(paras))
        print(f"[transcribe] {len(state['segments'])} 段, {len(full)} 字, "
              f"{len(paras)} 个自然段, {time.time()-t0:.0f}s")

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
