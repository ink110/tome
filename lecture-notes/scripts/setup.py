#!/usr/bin/env python3
"""lecture-notes 配置向导。

用法：
  python3 setup.py                 # 交互配置
  python3 setup.py --show          # 只看当前配置
  python3 setup.py --output-dir ~/Documents/lecture-notes --asr-provider openai ...
  python3 setup.py --reset         # 从默认值开始重新配置
"""
import argparse
import json
import os
import re
from pathlib import Path

import config


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def ask_choice(label, choices, default):
    print(f"{label}（{' / '.join(choices)}）")
    raw = input(f"  选择 [{default}]: ").strip().lower()
    return raw if raw in choices else default


def ask_yes(label, default=False):
    d = "Y/n" if default else "y/N"
    raw = input(f"{label} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def default_output():
    return str(Path.home() / "Documents" / "lecture-notes")


def derive_media(output_dir):
    p = Path(output_dir).expanduser()
    parts = list(p.parts)
    vault = None
    for i, part in enumerate(parts):
        if part == "notes" or re.match(r"^\d+-notes$", part):
            vault = Path(*parts[:i]) if i > 0 else p.parent
            break
    if vault is None:
        return str(p / "assets"), "assets"
    eagle = vault / "eagle-cache"
    for child in vault.iterdir() if vault.is_dir() else []:
        if child.is_dir() and re.match(r"^\d+-eagle-cache$", child.name):
            eagle = child
            break
    return str(eagle / "lectures"), f"{eagle.name}/lectures"


def show(cfg, path):
    print(f"\n配置文件：{path}")
    print(f"  笔记目录   : {cfg['output_dir'] or '（未设置，首次运行请先配置）'}")
    print(f"  ASR        : {cfg['asr']['provider']}  language={cfg['asr']['language']}")
    print(f"  Vision     : {cfg['vision']['provider']}")
    print(f"  媒体根     : {cfg['media']['local_root'] or '（自动，跟随笔记目录）'}")
    print(f"  链接前缀   : {cfg['media']['link_prefix']}")
    print(f"  PDF        : {'启用（可选）' if cfg['pdf']['enabled'] else '未启用（默认）'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--output-dir")
    ap.add_argument("--asr-provider", choices=["auto", "local", "openai"])
    ap.add_argument("--asr-model")
    ap.add_argument("--asr-binary")
    ap.add_argument("--asr-base-url")
    ap.add_argument("--asr-api-key-env")
    ap.add_argument("--vision-provider", choices=["auto", "ollama", "openai", "claude"])
    ap.add_argument("--vision-model")
    ap.add_argument("--vision-base-url")
    ap.add_argument("--vision-api-key-env")
    ap.add_argument("--media-root")
    ap.add_argument("--link-prefix")
    ap.add_argument("--pdf", choices=["on", "off"])
    args = ap.parse_args()

    cfg = config.load()
    cfg = json.loads(json.dumps(cfg)) if args.reset else cfg
    path = config.config_path()

    if args.show:
        show(cfg, path)
        return

    # 非交互参数优先；有任何参数时只更新指定项
    changed = any(v is not None for v in [
        args.output_dir, args.asr_provider, args.asr_model, args.asr_binary,
        args.asr_base_url, args.asr_api_key_env, args.vision_provider,
        args.vision_model, args.vision_base_url, args.vision_api_key_env,
        args.media_root, args.link_prefix, args.pdf,
    ])
    if not changed:
        print("首次运行需要确认笔记保存地址。")
        cfg["output_dir"] = ask("笔记保存目录（绝对路径）", cfg.get("output_dir") or default_output())
        cfg["output_dir"] = str(Path(cfg["output_dir"]).expanduser().resolve())
        print(f"  → 笔记将保存到：{cfg['output_dir']}")

        cfg["asr"]["provider"] = ask_choice("转写 provider", ["auto", "local", "openai"], cfg["asr"]["provider"])
        if cfg["asr"]["provider"] == "local":
            cfg["asr"]["local"]["binary"] = ask("mlx_whisper 路径", cfg["asr"]["local"].get("binary", ""))
            cfg["asr"]["local"]["model"] = ask("whisper 模型目录", cfg["asr"]["local"].get("model", ""))
        elif cfg["asr"]["provider"] == "openai":
            cfg["asr"]["openai"]["base_url"] = ask("OpenAI 兼容 base_url", cfg["asr"]["openai"]["base_url"])
            cfg["asr"]["openai"]["model"] = ask("转写模型", cfg["asr"]["openai"]["model"])
            cfg["asr"]["openai"]["api_key_env"] = ask("API Key 环境变量", cfg["asr"]["openai"]["api_key_env"])

        cfg["vision"]["provider"] = ask_choice("OCR/视觉 provider", ["auto", "ollama", "openai", "claude"], cfg["vision"]["provider"])
        if cfg["vision"]["provider"] == "ollama":
            cfg["vision"]["ollama"]["base_url"] = ask("Ollama 地址", cfg["vision"]["ollama"]["base_url"])
            cfg["vision"]["ollama"]["model"] = ask("视觉模型", cfg["vision"]["ollama"]["model"])
        elif cfg["vision"]["provider"] == "openai":
            cfg["vision"]["openai"]["base_url"] = ask("OpenAI 兼容 base_url", cfg["vision"]["openai"]["base_url"])
            cfg["vision"]["openai"]["model"] = ask("视觉模型", cfg["vision"]["openai"]["model"])
            cfg["vision"]["openai"]["api_key_env"] = ask("API Key 环境变量", cfg["vision"]["openai"]["api_key_env"])

        default_media, default_prefix = derive_media(cfg["output_dir"])
        cfg["media"]["local_root"] = ask("媒体资源根目录", cfg["media"].get("local_root") or default_media)
        cfg["media"]["link_prefix"] = ask("笔记里的媒体链接前缀", cfg["media"].get("link_prefix") or default_prefix)
        cfg["pdf"]["enabled"] = ask_yes("同时导出 PDF？（可选）", cfg["pdf"].get("enabled", False))
    else:
        if args.output_dir is not None:
            cfg["output_dir"] = str(Path(args.output_dir).expanduser().resolve())
            if not args.media_root:
                cfg["media"]["local_root"], cfg["media"]["link_prefix"] = derive_media(cfg["output_dir"])
        if args.asr_provider:
            cfg["asr"]["provider"] = args.asr_provider
        if args.asr_model:
            cfg["asr"]["local"]["model"] = args.asr_model
        if args.asr_binary:
            cfg["asr"]["local"]["binary"] = args.asr_binary
        if args.asr_base_url:
            cfg["asr"]["openai"]["base_url"] = args.asr_base_url
        if args.asr_api_key_env:
            cfg["asr"]["openai"]["api_key_env"] = args.asr_api_key_env
        if args.vision_provider:
            cfg["vision"]["provider"] = args.vision_provider
        if args.vision_model:
            if cfg["vision"]["provider"] == "openai":
                cfg["vision"]["openai"]["model"] = args.vision_model
            else:
                cfg["vision"]["ollama"]["model"] = args.vision_model
        if args.vision_base_url:
            if cfg["vision"]["provider"] == "openai":
                cfg["vision"]["openai"]["base_url"] = args.vision_base_url
            else:
                cfg["vision"]["ollama"]["base_url"] = args.vision_base_url
        if args.vision_api_key_env:
            cfg["vision"]["openai"]["api_key_env"] = args.vision_api_key_env
        if args.media_root:
            cfg["media"]["local_root"] = str(Path(args.media_root).expanduser().resolve())
        if args.link_prefix:
            cfg["media"]["link_prefix"] = args.link_prefix
        if args.pdf:
            cfg["pdf"]["enabled"] = args.pdf == "on"

    written = config.save(cfg)
    show(config.load(), written)
    print("\n下一步：python3 scripts/doctor.py    # 检查依赖")
    print("如果笔记目录还没确认，请把上面「笔记目录」的绝对路径复述给用户。")


if __name__ == "__main__":
    main()
