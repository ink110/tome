#!/usr/bin/env python3
"""检查 lecture-notes 的运行依赖，并给出安装建议。

用法：
  python3 doctor.py          # 人类可读
  python3 doctor.py --json   # 机器可读
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import config
import transcribe

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def add(rows, name, status, detail="", fix=""):
    rows.append({"name": name, "status": status, "detail": detail, "fix": fix})


def find_chrome():
    configured = config.get("tools.chrome", "auto")
    if configured and configured != "auto":
        return configured
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def ollama_models(base_url):
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = []

    # Python
    if sys.version_info >= (3, 9):
        add(rows, "Python", OK, f"{sys.version.split()[0]}")
    else:
        add(rows, "Python", FAIL, sys.version.split()[0], "升级到 Python 3.9+")

    cfg = config.load()
    out = config.output_dir()
    if not out:
        add(rows, "笔记保存目录", WARN, "未设置", "运行 python3 scripts/setup.py；首次会要求你确认保存地址")
    else:
        target = out if out.exists() else out.parent
        if target.exists() and os.access(target, os.W_OK):
            add(rows, "笔记保存目录", OK, str(out))
        else:
            add(rows, "笔记保存目录", FAIL, str(out), f"创建目录或改成有写权限的位置：{out}")

    # ffmpeg / ffprobe
    ffmpeg = transcribe.find_ffmpeg()
    if ffmpeg:
        add(rows, "ffmpeg", OK, ffmpeg)
    else:
        add(rows, "ffmpeg", FAIL, "未找到", "macOS: brew install ffmpeg；Linux: apt install ffmpeg；Windows: choco install ffmpeg")
    ffprobe = shutil.which("ffprobe")
    add(rows, "ffprobe", OK if ffprobe else FAIL, ffprobe or "未找到",
        "通常随 ffmpeg 一起安装")

    # ASR
    asr = (cfg.get("asr") or {}).get("provider", "auto")
    mlx = transcribe.find_mlx()
    model = transcribe.find_model()
    key_asr = config.api_key("asr")
    local_ok = bool(mlx and model)
    if asr in ("auto", "local"):
        if local_ok:
            add(rows, "ASR（本地）", OK, f"{mlx} · {model}")
        else:
            add(rows, "ASR（本地）", WARN if asr == "auto" else FAIL,
                f"binary={mlx or '未找到'} model={model or '未找到'}",
                "macOS: pip install mlx-whisper，并下载 whisper-large-v3-turbo；或在 setup.py 选 openai")
    if asr in ("auto", "openai"):
        if key_asr:
            add(rows, "ASR（在线）", OK, (cfg.get("asr", {}).get("openai", {}) or {}).get("model", "whisper-1"))
        else:
            add(rows, "ASR（在线）", WARN if asr == "auto" else FAIL,
                "未设置 API Key", "export OPENAI_API_KEY=sk-...，或改用本地 provider=local")

    # Vision
    vision = (cfg.get("vision") or {}).get("provider", "auto")
    ollama_url = (cfg.get("vision", {}).get("ollama", {}) or {}).get("base_url", "http://localhost:11434")
    ollama_model = (cfg.get("vision", {}).get("ollama", {}) or {}).get("model", "qwen3-vl:32b-instruct")
    models = ollama_models(ollama_url)
    ollama_ok = any(m == ollama_model or m.startswith(ollama_model + ":") for m in models)
    key_vision = config.api_key("vision")
    if vision in ("auto", "ollama"):
        if ollama_ok:
            add(rows, "Vision（本地 Ollama）", OK, ollama_model)
        else:
            detail = "Ollama 未运行" if not models else f"未找到模型 {ollama_model}"
            add(rows, "Vision（本地 Ollama）", WARN if vision == "auto" else FAIL, detail,
                f"安装 Ollama 后运行：ollama pull {ollama_model}；显存小可换 qwen2.5vl:7b")
    if vision in ("auto", "openai"):
        if key_vision:
            add(rows, "Vision（在线）", OK, (cfg.get("vision", {}).get("openai", {}) or {}).get("model", "gpt-4o-mini"))
        else:
            add(rows, "Vision（在线）", WARN if vision == "auto" else FAIL,
                "未设置 API Key", "export OPENAI_API_KEY=sk-...，或改用 ollama")
    if vision == "claude":
        add(rows, "Vision（Claude Code）", OK, "由 Claude Code 在会话中直接读图")

    # PDF 可选
    chrome = find_chrome()
    add(rows, "Chrome（PDF 可选）", OK if chrome else WARN, chrome or "未找到",
        "安装 Google Chrome；不用 PDF 可忽略")
    has_md = importlib.util.find_spec("markdown") is not None
    add(rows, "Python markdown（PDF 可选）", OK if has_md else WARN, "已安装" if has_md else "未安装",
        "pip install markdown")
    add(rows, "sips / 图片缩放", OK if shutil.which("sips") else WARN,
        "macOS sips" if shutil.which("sips") else "没有 sips，会退回 ffmpeg",
        "非 macOS 可忽略，脚本会尝试 ffmpeg")

    required_fail = any(r["status"] == FAIL for r in rows)
    if args.json:
        print(json.dumps({"ok": not required_fail, "checks": rows}, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print(f"[{r['status']}] {r['name']}: {r['detail']}")
            if r["fix"] and r["status"] != OK:
                print(f"        → {r['fix']}")
        print("\n" + ("PASS" if not required_fail else "FAIL — 先处理 FAIL 项；WARN 可按需处理"))
    sys.exit(1 if required_fail else 0)


if __name__ == "__main__":
    main()
