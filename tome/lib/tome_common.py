#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tome_common.py — tome 技能族的共享层（配置 + 模型 + 路径）

所有 tome-* 脚本都从这里取配置，不再各自硬编码路径。

配置文件（按优先级从低到高）：
  1. 本文件里的 DEFAULTS
  2. ~/.config/tome/config.json（或 $XDG_CONFIG_HOME/tome/config.json）
  3. 环境变量（TOME_PROVIDER / TOME_MODEL / TOME_WORKSPACE ...）
  4. 命令行参数（各脚本自己处理）

模型档位（models.provider）：
  claude  由 Claude Code 会话直接处理，脚本不调用外部模型；零配置
  openai  任意 OpenAI 兼容接口（DeepSeek / Kimi / GLM / OpenAI / Qwen）
  ollama  本地 Ollama

只依赖 Python 标准库。
"""
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_ENV = "TOME_CONFIG"

DEFAULTS = {
    "workspace": "~/Documents/tome",
    "storage": "folder",  # folder | obsidian
    "obsidian": {"vault": "", "folder": "topics"},
    "models": {
        "provider": "claude",  # claude | openai | ollama
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "qwen2.5:14b",
        },
        "openai": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key_env": "TOME_API_KEY",
        },
    },
    "bilibili": {"cookie_file": "~/.bilibili-cookies.txt"},
    "media": {
        "store": "local",  # local | eagle
        # 本地素材根：空 = 自动。Obsidian 模式 <vault>/eagle-cache；folder 模式 <project>/assets
        "local_root": "",
        # 笔记里的链接前缀：空 = 自动。eagle-cache 或 assets
        "link_prefix": "",
        "eagle": {
            "enabled": False,
            "base_url": "http://localhost:41595/api",
            "folder": "tome素材",
            "library": "",
        },
    },
    "tools": {
        "pandoc": "auto",
        "chrome": "auto",
        "whisper_python": "",
        "whisper_model": "~/whisper-models/small",
    },
    "output": {"dir": "~/Desktop"},
    "backups": {"dir": "~/.local/state/tome/backups"},
}


# ---------------------------------------------------------------- 配置读写
def config_path():
    raw = os.environ.get(CONFIG_ENV)
    if raw:
        return Path(raw).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "tome" / "config.json"


def _deep_merge(base, over):
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


_ENV_MAP = {
    "TOME_STORAGE": ("storage",),
    "TOME_WORKSPACE": ("workspace",),
    "TOME_VAULT": ("obsidian", "vault"),
    "TOME_PROVIDER": ("models", "provider"),
    "TOME_MODEL": ("models", "ollama", "model"),
    "TOME_OLLAMA_URL": ("models", "ollama", "base_url"),
    "TOME_BASE_URL": ("models", "openai", "base_url"),
    "TOME_OPENAI_MODEL": ("models", "openai", "model"),
}


def load_config(path=None):
    """返回合并后的配置字典。文件不存在时返回默认值，不报错。"""
    cfg = json.loads(json.dumps(DEFAULTS))
    target = Path(path).expanduser() if path else config_path()
    if target.is_file():
        try:
            cfg = _deep_merge(cfg, json.loads(target.read_text("utf-8")))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"[配置错误] 读不了 {target}：{exc}")
    for env_key, dotted in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if not value:
            continue
        node = cfg
        for part in dotted[:-1]:
            node = node.setdefault(part, {})
        node[dotted[-1]] = value
    return cfg


def save_config(cfg, path=None):
    target = Path(path).expanduser() if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", "utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def get(dotted, default=None, cfg=None):
    node = cfg if cfg is not None else load_config()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ---------------------------------------------------------------- 路径
def _expand(raw):
    return Path(raw).expanduser() if raw else None


def workspace():
    return _expand(get("workspace")) or Path.cwd()


def is_obsidian():
    return get("storage", "folder") == "obsidian"


def source_root():
    """信息源根目录：Obsidian 模式是库根，普通模式是工作目录。"""
    if is_obsidian():
        return _expand(get("obsidian.vault")) or workspace()
    return workspace()


def project_dir(name):
    """信息源管理根目录下的一个专题。两种模式返回结构相同的目录。"""
    if is_obsidian():
        vault = _expand(get("obsidian.vault"))
        if not vault:
            raise SystemExit(
                "[配置错误] storage=obsidian 但没有设置 obsidian.vault。"
                "运行 python3 tome/scripts/setup.py 重新配置。"
            )
        return vault / get("obsidian.folder", "topics") / name
    return workspace() / name


def collect_dir(project):
    return Path(project) / "00-收集"


def media_root(project=None):
    """本地素材根。

    优先用 media.local_root；否则 Obsidian 模式落到库根 eagle-cache，
    folder 模式落到专题内 assets。所有图片/GIF/音频/视频都应写到这里。
    """
    configured = get("media.local_root", "")
    if configured:
        return _expand(configured)
    if is_obsidian():
        vault = _expand(get("obsidian.vault"))
        if vault:
            return vault / "eagle-cache"
    return Path(project) / "assets" if project else workspace() / "assets"


def media_link_prefix(project=None):
    """笔记里引用本地素材的前缀（相对库根/项目根）。"""
    configured = get("media.link_prefix", "")
    if configured:
        return configured.strip("/")
    return "eagle-cache" if media_root(project).name == "eagle-cache" else "assets"


def media_dir(project):
    return media_root(project)


def output_dir():
    return _expand(get("output.dir", "~/Desktop")) or Path.home() / "Desktop"


def backup_dir():
    return _expand(get("backups.dir", "~/.local/state/tome/backups")) or \
        Path.home() / ".local" / "state" / "tome" / "backups"


def cookie_file():
    return _expand(get("bilibili.cookie_file", "~/.bilibili-cookies.txt"))


def link(text, target):
    """按信息源模式生成链接：Obsidian 用 wikilink，普通文件夹用相对链接。"""
    if is_obsidian():
        return f"[[{target}|{text}]]" if text and text != target else f"[[{target}]]"
    return f"[{text}]({target})"


def embed(target):
    if is_obsidian():
        return f"![[{target}]]"
    return f"![]({target})"


def eagle_enabled():
    return bool(get("media.eagle.enabled", False))


def eagle_settings():
    return {
        "enabled": eagle_enabled(),
        "base_url": get("media.eagle.base_url", "http://localhost:41595/api"),
        "folder": get("media.eagle.folder", "tome素材"),
        "library": get("media.eagle.library", ""),
    }


def find_tool(name, config_key):
    """按 配置 → 常见位置 → PATH 的顺序找可执行文件。"""
    configured = get(f"tools.{config_key}", "auto")
    if configured and configured != "auto":
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(configured)
        if found:
            return found
        return None
    if name == "pandoc":
        for candidate in (
            Path.home() / "tools" / "obsidian-epub" / "pandoc",
            Path("/opt/homebrew/bin/pandoc"),
            Path("/usr/local/bin/pandoc"),
        ):
            if candidate.is_file():
                return str(candidate)
    elif name == "chrome":
        for candidate in (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def pandoc_bin():
    return find_tool("pandoc", "pandoc")


def chrome_bin():
    return find_tool("chrome", "chrome")


# ---------------------------------------------------------------- 模型
class ModelError(RuntimeError):
    """模型不可用或返回错误。"""


class ClaudeMode(RuntimeError):
    """当前是 claude 档位：应由 Claude Code 直接处理，脚本不调用模型。"""


CLAUDE_HINT = (
    "当前模型档位是 claude（由 Claude Code 直接处理，脚本不调用外部模型）。\n"
    "直接用本会话或子代理读取 _raw.txt / 笔记正文完成这一步；\n"
    "如果要用脚本批量跑，先切到 openai 或 ollama 档位：\n"
    "  python3 tome/scripts/setup.py\n"
    "或临时指定：TOME_PROVIDER=openai TOME_API_KEY=... 或 TOME_PROVIDER=ollama"
)


def provider():
    return str(get("models.provider", "claude")).lower()


def model_name(explicit=None):
    if explicit:
        return explicit
    p = provider()
    if p == "ollama":
        return get("models.ollama.model", "qwen2.5:14b")
    if p == "openai":
        return get("models.openai.model", "deepseek-chat")
    return "claude"


def _http_json(url, payload=None, headers=None, timeout=60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:800]
        raise ModelError(f"HTTP {exc.code} ← {url}\n{body}") from exc
    except urllib.error.URLError as exc:
        raise ModelError(f"连不上 {url}：{exc.reason}") from exc


def _ollama_chat(model, system, user, timeout, temperature, num_ctx,
                 num_predict, json_mode, stream):
    base = get("models.ollama.base_url", "http://localhost:11434").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": bool(stream),
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if json_mode:
        payload["format"] = "json"
    if num_predict:
        payload["options"]["num_predict"] = num_predict
    if not stream:
        raw = _http_json(f"{base}/api/chat", payload, timeout=timeout)
        obj = json.loads(raw)
        if obj.get("error"):
            raise ModelError(obj["error"])
        return obj.get("message", {}).get("content", "")

    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise ModelError(obj["error"])
                piece = obj.get("message", {}).get("content", "")
                if piece:
                    chunks.append(piece)
                if obj.get("done"):
                    break
    except urllib.error.URLError as exc:
        raise ModelError(f"连不上 Ollama（{base}）：{exc.reason}") from exc
    return "".join(chunks)


def _openai_chat(model, system, user, timeout, temperature, json_mode, stream):
    base = str(get("models.openai.base_url", "https://api.deepseek.com/v1")).rstrip("/")
    key_env = get("models.openai.api_key_env", "TOME_API_KEY")
    api_key = os.environ.get(key_env) or get("models.openai.api_key", "")
    if not api_key:
        raise ModelError(
            f"找不到 API Key。请设置环境变量 {key_env}，或运行 python3 tome/scripts/setup.py 配置。"
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": bool(stream),
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{base}/chat/completions"
    if not stream:
        raw = _http_json(url, payload, headers, timeout)
        obj = json.loads(raw)
        try:
            return obj["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError(f"返回结构异常：{raw[:500]}")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                piece = delta.get("content") or ""
                if piece:
                    chunks.append(piece)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:800]
        raise ModelError(f"HTTP {exc.code} ← {url}\n{body}") from exc
    except urllib.error.URLError as exc:
        raise ModelError(f"连不上 {base}：{exc.reason}") from exc
    return "".join(chunks)


def chat(system, user, model=None, timeout=900, temperature=0.3,
         json_mode=False, stream=True, num_ctx=32768, num_predict=None):
    """统一模型调用。claude 档位会抛 ClaudeMode，调用方应据此走「Claude 自己处理」路径。"""
    p = provider()
    model = model_name(model)
    if p == "claude":
        raise ClaudeMode(CLAUDE_HINT)
    if p == "ollama":
        return _ollama_chat(model, system, user, timeout, temperature,
                            num_ctx, num_predict, json_mode, stream)
    if p == "openai":
        return _openai_chat(model, system, user, timeout, temperature,
                            json_mode, stream)
    raise ModelError(f"未知的模型档位：{p}（可选 claude / openai / ollama）")


def list_models():
    """列出当前档位可用的模型，供预检和提示使用。"""
    p = provider()
    if p == "claude":
        return ["claude（由 Claude Code 直接处理）"]
    if p == "ollama":
        base = get("models.ollama.base_url", "http://localhost:11434").rstrip("/")
        raw = _http_json(f"{base}/api/tags", timeout=10)
        return [m.get("name", "") for m in json.loads(raw).get("models", [])]
    if p == "openai":
        base = str(get("models.openai.base_url", "")).rstrip("/")
        key_env = get("models.openai.api_key_env", "TOME_API_KEY")
        key = os.environ.get(key_env, "")
        if not key:
            raise ModelError(f"找不到 API Key（环境变量 {key_env}）")
        req = urllib.request.Request(
            f"{base}/models", headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        return [m.get("id", "") for m in obj.get("data", [])]
    return []


def model_summary():
    p = provider()
    if p == "claude":
        return "claude（Claude Code 直接处理，无需 API Key）"
    if p == "ollama":
        return f"ollama · {model_name()} · {get('models.ollama.base_url')}"
    return f"openai · {model_name()} · {get('models.openai.base_url')}"
