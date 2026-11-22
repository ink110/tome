#!/usr/bin/env python3
"""转写音频 / 视频。本地 mlx_whisper 或在线 OpenAI 兼容接口二选一。

用法：
  python3 transcribe.py <音频/视频/文件夹> <输出目录> [语言]
  python3 transcribe.py <输入> <输出> zh --provider local
  python3 transcribe.py <输入> <输出> zh --provider openai --model whisper-1

输出：每个输入生成 <名字>.json（带分段时间戳）和 <名字>.txt。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import config

MEDIA_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".m4v", ".mkv"}


def log(msg):
    print(msg, flush=True)


def find_ffmpeg():
    configured = config.get("tools.ffmpeg", "auto")
    if configured and configured != "auto":
        return configured
    return shutil.which("ffmpeg") or ""


def find_mlx():
    configured = config.get("asr.local.binary", "")
    if configured:
        return configured
    if os.environ.get("MLX_WHISPER"):
        return os.environ["MLX_WHISPER"]
    found = shutil.which("mlx_whisper")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "mlx_whisper"
    return str(candidate) if candidate.is_file() else ""


def find_model():
    configured = config.get("asr.local.model", "")
    if configured:
        return configured
    if os.environ.get("WHISPER_MODEL"):
        return os.environ["WHISPER_MODEL"]
    candidates = [
        Path.home() / "models" / "whisper-large-v3-turbo",
        Path.home() / ".models" / "whisper-large-v3-turbo",
        Path.home() / "models" / "whisper-large-v3",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return ""


def extract_audio(video, outdir):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("找不到 ffmpeg。安装：brew install ffmpeg（macOS）/ apt install ffmpeg（Linux）")
    out = Path(outdir) / (Path(video).stem + ".wav")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def collect_inputs(src):
    p = Path(src).expanduser()
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted([f for f in p.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS])
    raise SystemExit(f"输入不存在：{p}")


def safe_stem(path, outdir):
    stem = Path(path).stem
    candidate = outdir / (stem + ".json")
    if not candidate.exists():
        return stem
    n = 2
    while (outdir / f"{stem}-{n}.json").exists():
        n += 1
    return f"{stem}-{n}"


def run_local(audio, outdir, stem, language, binary, model):
    if not binary or not Path(binary).exists():
        raise SystemExit(
            "本地转写需要 mlx_whisper。\n"
            "  macOS：pip install mlx-whisper\n"
            "  或运行 python3 setup.py 改用在线 provider=openai。"
        )
    if not model:
        raise SystemExit(
            "找不到 whisper 模型路径。\n"
            "  设置 asr.local.model 指向 whisper-large-v3-turbo 目录，"
            "或运行 python3 setup.py 配置。"
        )
    cmd = [
        binary, str(audio),
        "--model", model,
        "--output-dir", str(outdir),
        "--output-name", stem,
        "--output-format", "json",
        "--task", "transcribe",
        "--word-timestamps", "True",
        "--condition-on-previous-text", "False",
        "--compression-ratio-threshold", "1.8",
        "--hallucination-silence-threshold", "2",
        "--no-speech-threshold", "0.6",
    ]
    if language and language != "auto":
        cmd += ["--language", language]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1000:], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit("mlx_whisper 转写失败")
    out_json = outdir / (stem + ".json")
    if not out_json.exists():
        raise SystemExit(f"转写完成但没有找到 {out_json}")
    data = json.loads(out_json.read_text(encoding="utf-8"))
    segs = data.get("segments") or []
    text = data.get("text") or "".join(s.get("text", "") for s in segs)
    (outdir / (stem + ".txt")).write_text(text.strip(), encoding="utf-8")
    return segs, text


def multipart(fields, file_field, file_path):
    boundary = "----lecture-notes-" + os.urandom(8).hex()
    lines = []
    for k, v in fields.items():
        if v in (None, ""):
            continue
        lines.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    name, path = file_field
    lines.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{Path(path).name}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode())
    lines.append(Path(path).read_bytes())
    lines.append(f"\r\n--{boundary}--\r\n".encode())
    return boundary, b"".join(lines)


def run_openai(audio, outdir, stem, language, base_url, model, api_key_env):
    key = os.environ.get(api_key_env, "")
    if not key:
        raise SystemExit(f"在线转写需要环境变量 {api_key_env}。\n  export {api_key_env}=sk-...")
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/audio/transcriptions"
    fields = {"model": model or "whisper-1", "response_format": "verbose_json"}
    if language and language != "auto":
        fields["language"] = language
    boundary, body = multipart(fields, ("file", audio), audio)
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:800]
        raise SystemExit(f"在线转写失败：HTTP {exc.code}\n{detail}")
    segs = data.get("segments") or []
    text = data.get("text") or "".join(s.get("text", "") for s in segs)
    (outdir / (stem + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / (stem + ".txt")).write_text(text.strip(), encoding="utf-8")
    return segs, text


def choose_provider(requested):
    p = (requested or config.provider("asr") or "auto").lower()
    if p != "auto":
        return p
    if find_mlx() and find_model():
        return "local"
    if config.api_key("asr"):
        return "openai"
    raise SystemExit(
        "没有可用的转写 provider。\n"
        "  本地：pip install mlx-whisper，并配置 asr.local.model\n"
        "  在线：export OPENAI_API_KEY=...（或运行 python3 setup.py）"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("outdir")
    ap.add_argument("language", nargs="?", default="")
    ap.add_argument("--provider", choices=["auto", "local", "openai"], default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--binary", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    args = ap.parse_args()

    language = args.language or config.get("asr.language", "zh") or "zh"
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    files = collect_inputs(args.input)
    if not files:
        raise SystemExit(f"没有找到音频/视频：{args.input}")

    provider = choose_provider(args.provider)
    log(f"[transcribe] provider={provider} language={language} files={len(files)}")
    for src in files:
        stem = safe_stem(src, outdir)
        log(f"[transcribe] {src.name}")
        with tempfile.TemporaryDirectory(prefix="lecture-notes-") as tmp:
            audio = extract_audio(src, tmp) if src.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"} else src
            if provider == "local":
                binary = args.binary or find_mlx()
                model = args.model or find_model()
                segs, text = run_local(audio, outdir, stem, language, binary, model)
            else:
                base = args.base_url or config.get("asr.openai.base_url", "https://api.openai.com/v1")
                model = args.model or config.get("asr.openai.model", "whisper-1")
                env = args.api_key_env or config.get("asr.openai.api_key_env", "OPENAI_API_KEY")
                segs, text = run_openai(audio, outdir, stem, language, base, model, env)
        log(f"  -> {outdir / (stem + '.txt')}  {len(text)} 字 / {len(segs)} 段")


if __name__ == "__main__":
    main()
