#!/usr/bin/env python3
"""Prepare a lecture video for OCR: audio, stable keyframes, metadata.

Pipeline:
- ffprobe metadata
- extract mono 16k audio for ASR
- sample every N seconds, hash only the top part (speaker moves below)
- 512-bit dHash on 33x16 grayscale; cluster by distance to the current stable frame
- wait for a stable frame after a change, then extract it at full resolution
"""
import argparse
import json
import statistics
import subprocess
from pathlib import Path


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def probe(video: Path):
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,disposition",
        "-of", "json", str(video),
    ]).stdout
    data = json.loads(out)
    info = {"duration": float(data["format"]["duration"]), "size": int(data["format"]["size"])}
    videos = [
        s for s in data.get("streams", [])
        if s.get("codec_type") == "video" and s.get("width")
        and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if videos:
        # Cover art is sometimes exposed as a second video stream; take the largest real one.
        v = max(videos, key=lambda s: int(s["width"]) * int(s["height"]))
        info.update({
            "width": int(v["width"]), "height": int(v["height"]),
            "fps": v.get("r_frame_rate", ""), "video_codec": v.get("codec_name", ""),
        })
    for s in data.get("streams", []):
        if s.get("codec_type") == "audio":
            info.update({
                "audio_codec": s.get("codec_name", ""),
                "sample_rate": int(s.get("sample_rate", 0) or 0),
                "channels": int(s.get("channels", 0) or 0),
            })
            break
    return info


def dhash(buf: bytes, width: int, height: int) -> int:
    value = 0
    for y in range(height):
        row = buf[y * width:(y + 1) * width]
        for x in range(width - 1):
            value = (value << 1) | (1 if row[x] > row[x + 1] else 0)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def extract_audio(video: Path, out: Path, start: float, duration: float):
    out.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out),
    ])


def hash_frames(video: Path, start: float, duration: float, sample: float,
                crop_top: int, width: int, height: int):
    vf = (
        f"fps=1/{sample},crop=iw:{crop_top}:0:0,"
        f"scale={width}:{height}:flags=area,format=gray"
    )
    raw = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]).stdout
    size = width * height
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def choose_stable(frames, width: int, height: int,
                  threshold: int, stable: int, max_wait: int):
    if not frames:
        return []
    hashes = [dhash(b, width, height) for b in frames]
    reps = [0]
    chosen = [0]
    cand = None
    for i in range(1, len(frames)):
        d_rep = hamming(hashes[i], hashes[reps[-1]])
        d_prev = hamming(hashes[i], hashes[i - 1])
        if cand is None and d_rep > threshold:
            cand = i
        if cand is not None:
            if (i - cand >= 1 and d_prev <= stable) or (i - cand >= max_wait):
                reps.append(i)
                chosen.append(i)
                cand = None
    return [(i, hashes[i]) for i in chosen]


def extract_frames(video: Path, times, out_dir: Path, quality: int):
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for seq, t in enumerate(times, 1):
        name = f"{seq:03d}_{int(round(t))}s.jpg"
        dst = out_dir / name
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
            "-q:v", str(quality), str(dst),
        ])
        rows.append({"seq": seq, "time": round(t, 1), "file": name})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--sample", type=float, default=2.0, help="seconds between samples")
    ap.add_argument("--crop-top", type=int, default=760, help="hash only the top N pixels")
    ap.add_argument("--hash-width", type=int, default=33)
    ap.add_argument("--hash-height", type=int, default=16)
    ap.add_argument("--threshold", type=int, default=70, help="512-bit hamming threshold")
    ap.add_argument("--stable", type=int, default=17, help="adjacent hamming to consider stable")
    ap.add_argument("--max-wait", type=int, default=8, help="max samples to wait for stability")
    ap.add_argument("--min-std", type=float, default=2.0, help="skip near-uniform frames")
    ap.add_argument("--max-frames", type=int, default=120)
    ap.add_argument("--quality", type=int, default=2)
    ap.add_argument("--panel", action="store_true",
                    help="roundtable/panel: extract one representative frame, not a slide list")
    ap.add_argument("--frame-at", type=float, default=2.0,
                    help="seconds into the clip for --panel")
    args = ap.parse_args()

    video = Path(args.video).expanduser().resolve()
    if not video.exists():
        raise SystemExit(f"video not found: {video}")
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    info = probe(video)
    duration = info["duration"] - args.start
    if args.end is not None:
        duration = min(duration, args.end - args.start)
    if duration <= 0:
        raise SystemExit("empty time range")
    crop_top = min(args.crop_top, info.get("height", args.crop_top))

    print(f"video: {info.get('width')}x{info.get('height')} "
          f"{info.get('fps')} {info.get('video_codec')} | {duration/60:.1f} min")
    extract_audio(video, out / "audio" / "audio.wav", args.start, duration)

    if args.panel:
        # A roundtable has one recurring stage shot; a slide gallery adds no value.
        t = min(args.start + args.frame_at, info["duration"] - 0.5)
        rows = extract_frames(video, [t], out / "keyframes", args.quality)
        (out / "times.txt").write_text(
            "\n".join(str(r["time"]) for r in rows) + "\n", encoding="utf-8")
        (out / "keyframes.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "video_info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"panel frame: {len(rows)} | audio: {out/'audio/audio.wav'}")
        return

    frames = hash_frames(video, args.start, duration, args.sample,
                         crop_top, args.hash_width, args.hash_height)
    chosen = choose_stable(frames, args.hash_width, args.hash_height,
                           args.threshold, args.stable, args.max_wait)
    times = [args.start + i * args.sample + args.sample / 2 for i, _ in chosen]
    times = [min(t, info["duration"] - 0.5) for t in times]

    # Drop near-uniform/black frames, but never drop the opening title.
    kept = []
    for (i, h), t in zip(chosen, times):
        std = statistics.pstdev(frames[i])
        if not kept or std >= args.min_std:
            kept.append((t, std))
    if len(kept) > args.max_frames:
        print(f"WARN: {len(kept)} frames > --max-frames {args.max_frames}; "
              f"raise --threshold to reduce.")
        if args.max_frames == 1:
            kept = kept[:1]
        else:
            idxs = [round(i * (len(kept) - 1) / (args.max_frames - 1))
                    for i in range(args.max_frames)]
            kept = [kept[i] for i in idxs]

    rows = extract_frames(video, [t for t, _ in kept], out / "keyframes", args.quality)
    for row, (_, std) in zip(rows, kept):
        row["std"] = round(std, 1)
    (out / "times.txt").write_text(
        "\n".join(str(r["time"]) for r in rows) + "\n", encoding="utf-8")
    (out / "keyframes.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "video_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"keyframes: {len(rows)} | audio: {out/'audio/audio.wav'}")
    print(f"next: python3 {Path(__file__).with_name('ocr_images.py')} "
          f"--src {out/'keyframes'} --out {out/'ocr'} --strict --max-side 1920 --restart-every 0")


if __name__ == "__main__":
    main()
