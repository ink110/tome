#!/usr/bin/env python3
"""OCR lecture photos / video frames with a local or online vision model.

Provider 选择顺序：--provider > config vision.provider > auto。
  ollama  : 本地 Ollama（默认 qwen3-vl）
  openai  : OpenAI 兼容的 vision chat/completions
  claude  : 不调用脚本模型，列出图片交给 Claude Code 在会话中读图
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp")


def build_prompt(topics, strict=False):
    if strict:
        return (
            "你是OCR引擎。逐字输出图中所有可见文字，按从上到下、从左到右的自然阅读顺序。"
            "不要概括，不要解释，不要翻译，不要遗漏任何一行，包括底部的小字、注释和英文。只输出文字。"
        )
    topic_line = f"（候选：{topics}；无法判断就写「无法判断」）" if topics else "（无法判断就写「无法判断」）"
    return (
        "这是讲座、大会或会议现场拍摄的幻灯片/会场照片。请完成三件事：\n"
        "1.【OCR】尽量完整准确地提取图片中所有文字（标题、要点、图表标注、页脚等），保留层级。\n"
        "2.【主题】用一句话概括这张图在讲什么。\n"
        f"3.【归属】判断它属于哪个分享话题{topic_line}。\n"
        "输出格式：\nOCR:\n<文字内容>\n主题: <一句话>\n归属: <候选话题或无法判断>"
    )


def shrink(src, dst, max_side):
    if os.path.exists(dst):
        return
    if shutil.which("sips"):
        subprocess.run(["sips", "-Z", str(max_side), src, "--out", dst], check=True, capture_output=True)
    elif shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                        "-vf", f"scale='min({max_side},iw)':-2", "-q:v", "2", dst],
                       check=True, capture_output=True)
    else:
        shutil.copy2(src, dst)


def ollama_model_ok(base_url, model):
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return any(m.get("name", "").startswith(model) for m in data.get("models", []))
    except Exception:
        return False


def ocr_ollama(path, model, prompt, timeout, base_url):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({
        "model": model, "prompt": prompt, "images": [b64], "stream": False,
        "keep_alive": "30m", "options": {"temperature": 0.1, "num_predict": 1200},
    }).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "").strip()


def ocr_openai(path, model, prompt, timeout, base_url, api_key_env):
    key = os.environ.get(api_key_env, "")
    if not key:
        raise RuntimeError(f"缺少环境变量 {api_key_env}")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def restart_ollama(log_path):
    subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)
    time.sleep(3)
    log = open(log_path, "ab")
    subprocess.Popen(["ollama", "serve"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("ollama restart failed")


def pick_provider(requested):
    p = (requested or config.provider("vision") or "auto").lower()
    if p != "auto":
        return p
    v = config.load().get("vision", {})
    ollama = v.get("ollama", {}) or {}
    if ollama_model_ok(ollama.get("base_url", "http://localhost:11434"), ollama.get("model", "")):
        return "ollama"
    if config.api_key("vision"):
        return "openai"
    return "claude"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--topics", default="", help="comma-separated session topics")
    ap.add_argument("--provider", choices=["auto", "ollama", "openai", "claude"], default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--max-side", type=int, default=0)
    ap.add_argument("--restart-every", type=int, default=20, help="0 disables restart (ollama only)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    out = Path(args.out).expanduser()
    small_dir = out / "_small"
    out.mkdir(parents=True, exist_ok=True)
    small_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in src.iterdir() if p.is_file() and p.suffix.lower() in EXTS]) if src.is_dir() else [src]
    if not files:
        raise SystemExit(f"no images found in {src}")

    provider = pick_provider(args.provider)
    cfg = config.load()
    vision = cfg.get("vision", {})
    max_side = args.max_side or vision.get("max_side", 1920)
    prompt = build_prompt(args.topics, args.strict)
    print(f"[ocr] provider={provider} images={len(files)}", flush=True)

    if provider == "claude":
        manifest = out / "_claude_images.json"
        manifest.write_text(json.dumps([str(p) for p in files], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ocr] Claude 模式：请在会话中逐张读图，把文字写到 {out}/<图片名>.txt；清单：{manifest}")
        return

    if provider == "ollama":
        base = args.base_url or (vision.get("ollama", {}) or {}).get("base_url", "http://localhost:11434")
        model = args.model or (vision.get("ollama", {}) or {}).get("model", "qwen3-vl:32b-instruct")
    else:
        base = args.base_url or (vision.get("openai", {}) or {}).get("base_url", "https://api.openai.com/v1")
        model = args.model or (vision.get("openai", {}) or {}).get("model", "gpt-4o-mini")
        key_env = args.api_key_env or (vision.get("openai", {}) or {}).get("api_key_env", "OPENAI_API_KEY")
    done = 0
    for i, path in enumerate(files, 1):
        name = path.stem
        dst = out / (name + ".txt")
        if dst.exists() and not args.force:
            print(f"[{i}/{len(files)}] skip {name}", flush=True)
            continue
        if provider == "ollama" and args.restart_every and done and done % args.restart_every == 0:
            print(f"--- restart ollama after {done} images ---", flush=True)
            restart_ollama(str(out / "_ollama.log"))
        small = small_dir / path.name
        try:
            shrink(str(path), str(small), max_side)
            text = None
            for attempt in range(3):
                try:
                    if provider == "ollama":
                        text = ocr_ollama(small, model, prompt, args.timeout, base)
                    else:
                        text = ocr_openai(small, model, prompt, args.timeout, base, key_env)
                    break
                except Exception as exc:
                    print(f"[{i}/{len(files)}] retry {attempt + 1} {name}: {exc}", flush=True)
            if text is None:
                raise RuntimeError("3 attempts failed")
            dst.write_text(text, encoding="utf-8")
            done += 1
            print(f"[{i}/{len(files)}] OK {name}", flush=True)
        except Exception as exc:
            print(f"[{i}/{len(files)}] FAIL {name}: {exc}", flush=True)
    print("OCR_DONE", flush=True)


if __name__ == "__main__":
    main()
