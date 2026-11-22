#!/usr/bin/env python3
"""lecture-notes 共享配置。

默认配置在 ~/.config/lecture-notes/config.json（可用 LECTURE_NOTES_CONFIG 覆盖）。
只依赖标准库，方便在 macOS / Linux / Windows 上跑。
"""
import json
import os
from pathlib import Path

CONFIG_ENV = "LECTURE_NOTES_CONFIG"

DEFAULTS = {
    "output_dir": "",
    "asr": {
        "provider": "auto",  # auto | local | openai
        "language": "zh",
        "local": {
            "binary": "",
            "model": "",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "whisper-1",
            "api_key_env": "OPENAI_API_KEY",
        },
    },
    "vision": {
        "provider": "auto",  # auto | ollama | openai | claude
        "max_side": 1920,
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "qwen3-vl:32b-instruct",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
    },
    "tools": {
        "ffmpeg": "auto",
        "ffprobe": "auto",
        "chrome": "auto",
    },
    "media": {
        "local_root": "",
        "link_prefix": "eagle-cache/lectures",
    },
    "pdf": {
        "enabled": False,
    },
}


def config_path():
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "lecture-notes" / "config.json"


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    p = Path(path).expanduser() if path else config_path()
    if p.is_file():
        try:
            cfg = _merge(cfg, json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            raise SystemExit(f"读不了配置 {p}: {exc}")
    return cfg


def save(cfg, path=None):
    p = Path(path).expanduser() if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def get(dotted, default=None):
    cfg = load()
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def output_dir():
    raw = get("output_dir", "")
    return Path(raw).expanduser().resolve() if raw else None


def media_root(topic=""):
    raw = get("media.local_root", "") or ""
    if raw:
        try:
            raw = raw.format(topic=topic or "")
        except Exception:
            pass
        return Path(raw).expanduser().resolve()
    out = output_dir()
    if out:
        return out / "assets"
    return None


def link_prefix(topic=""):
    raw = get("media.link_prefix", "eagle-cache/lectures") or "eagle-cache/lectures"
    try:
        return raw.format(topic=topic or "").strip("/")
    except Exception:
        return raw.strip("/")


def api_key(section):
    """从 asr.openai / vision.openai 的 api_key_env 读取密钥。"""
    cfg = load()
    node = cfg.get(section, {}).get("openai", {})
    env = node.get("api_key_env") or "OPENAI_API_KEY"
    return os.environ.get(env, "")


def provider(section):
    return (load().get(section, {}).get("provider") or "auto").lower()
