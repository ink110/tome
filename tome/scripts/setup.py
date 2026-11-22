#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup.py — tome 首次配置向导

问三个问题，写 ~/.config/tome/config.json：
  1. 项目放在哪（信息源根目录）
  2. 用不用 Obsidian 管理信息源
  3. 用哪个模型（Claude Code 直接做 / 在线 API / 本地 Ollama）

用法：
  python3 tome/scripts/setup.py             # 交互式
  python3 tome/scripts/setup.py --show      # 只显示当前配置
  python3 tome/scripts/setup.py --reset     # 重新问一遍
非交互：
  python3 tome/scripts/setup.py --workspace ~/Documents/tome --storage folder --provider claude
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
for parent in HERE.parents:
    if (parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or default


def choose(prompt, options, default_index=0):
    print(f"\n{prompt}")
    for i, (label, hint) in enumerate(options, 1):
        mark = "（默认）" if i - 1 == default_index else ""
        print(f"  {i}. {label}{mark}")
        if hint:
            print(f"     {hint}")
    while True:
        raw = input(f"选择 1-{len(options)} [{default_index + 1}]: ").strip()
        if not raw:
            return default_index
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("  输入序号。")


def show(cfg, path):
    print(f"\n配置文件：{path}")
    print(f"  信息源根目录 : {cfg['workspace']}")
    print(f"  信息源模式   : {cfg['storage']}"
          + (f"（vault: {cfg['obsidian']['vault']}）" if cfg['storage'] == 'obsidian' else ""))
    print(f"  模型档位     : {tc.model_summary()}")
    print(f"  媒体存储     : {'Eagle + 本地副本' if cfg['media']['eagle']['enabled'] else '本地素材库'}")
    if cfg.get("storage") == "obsidian" and cfg.get("obsidian", {}).get("vault"):
        media_root = Path(os.path.expanduser(cfg["obsidian"]["vault"])) / "eagle-cache"
    else:
        media_root = Path(os.path.expanduser(cfg["workspace"])) / "<专题>/assets"
    print(f"  本地素材根   : {media_root}")
    print(f"  B站 cookie   : {cfg['bilibili']['cookie_file']}")
    print(f"  成书输出     : {cfg['output']['dir']}")


def main():
    ap = argparse.ArgumentParser(description="tome 配置向导")
    ap.add_argument("--show", action="store_true", help="只显示当前配置")
    ap.add_argument("--reset", action="store_true", help="重新配置")
    ap.add_argument("--workspace")
    ap.add_argument("--storage", choices=["folder", "obsidian"])
    ap.add_argument("--vault", help="Obsidian 库根目录（storage=obsidian 时用）")
    ap.add_argument("--provider", choices=["claude", "openai", "ollama"])
    ap.add_argument("--model", help="覆盖默认模型名")
    ap.add_argument("--base-url", help="OpenAI 兼容接口的 base_url")
    ap.add_argument("--output", help="成书输出目录，默认 ~/Desktop")
    ap.add_argument("--config", help="写入指定配置文件")
    args = ap.parse_args()

    path = Path(args.config).expanduser() if args.config else tc.config_path()
    cfg = tc.load_config(path)

    if args.show:
        show(cfg, path)
        return 0

    interactive = not any([args.workspace, args.storage, args.provider])
    if not interactive and not path.exists() and not args.workspace:
        print("首次配置请先给 --workspace，或直接运行交互式向导。")
        return 2

    if args.workspace:
        cfg["workspace"] = args.workspace
    if args.storage:
        cfg["storage"] = args.storage
    if args.vault:
        cfg["obsidian"]["vault"] = args.vault
        cfg["storage"] = "obsidian"
    if args.provider:
        cfg["models"]["provider"] = args.provider
    if args.model:
        provider = cfg["models"]["provider"]
        if provider == "ollama":
            cfg["models"]["ollama"]["model"] = args.model
        elif provider == "openai":
            cfg["models"]["openai"]["model"] = args.model
    if args.base_url:
        cfg["models"]["openai"]["base_url"] = args.base_url
    if args.output:
        cfg["output"]["dir"] = args.output

    if interactive:
        print("tome 配置向导（回车用默认值）\n")

        current = cfg["workspace"]
        cfg["workspace"] = ask("1/3 项目放在哪？所有专题都存在这个目录下",
                               os.path.expanduser(current))

        idx = choose(
            "2/3 怎么管理信息源？",
            [
                ("普通文件夹", "直接在工作目录下按专题建文件夹，最简单"),
                ("Obsidian 库", "笔记带 [[双链]] 和索引页，适合已有 Obsidian 的人"),
            ],
            0 if cfg["storage"] == "folder" else 1,
        )
        if idx == 0:
            cfg["storage"] = "folder"
        else:
            cfg["storage"] = "obsidian"
            cfg["obsidian"]["vault"] = ask(
                "    Obsidian 库根目录（含 .obsidian 的那一层）",
                os.path.expanduser(cfg["obsidian"]["vault"] or "~/Documents/Obsidian"),
            )

        idx = choose(
            "3/3 用哪个模型做蒸馏、打标签、去重？",
            [
                ("Claude Code 直接做", "零配置，不用 API Key，第一次跑最快"),
                ("在线 API", "任意 OpenAI 兼容接口：DeepSeek / Kimi / GLM / OpenAI"),
                ("本地 Ollama", "内容不出本机，需要先装 Ollama 并拉模型"),
            ],
            0,
        )
        cfg["models"]["provider"] = ["claude", "openai", "ollama"][idx]
        if idx == 1:
            cfg["models"]["openai"]["base_url"] = ask(
                "    接口地址", cfg["models"]["openai"]["base_url"])
            cfg["models"]["openai"]["model"] = ask(
                "    模型名", cfg["models"]["openai"]["model"])
            env_name = cfg["models"]["openai"]["api_key_env"]
            print(f"    API Key 不写进配置文件。跑之前设置：export {env_name}=sk-...")
        if idx == 2:
            cfg["models"]["ollama"]["base_url"] = ask(
                "    Ollama 地址", cfg["models"]["ollama"]["base_url"])
            cfg["models"]["ollama"]["model"] = ask(
                "    模型名（显存 16G 建议 14b，32G 以上可上 32b）",
                cfg["models"]["ollama"]["model"])

        print("\n可选：公众号采集是否用 Eagle 管理图片/视频？")
        print("  不用 Eagle 也会把媒体存到本地素材库（Obsidian 用库根 eagle-cache/，folder 用专题 assets/）。")
        if ask("  启用 Eagle？(y/N)", "N").lower().startswith("y"):
            cfg["media"]["eagle"]["enabled"] = True
            cfg["media"]["eagle"]["folder"] = ask(
                "  Eagle 文件夹名", cfg["media"]["eagle"]["folder"])

    workspace = Path(os.path.expanduser(cfg["workspace"]))
    workspace.mkdir(parents=True, exist_ok=True)

    written = tc.save_config(cfg, path)
    print()
    show(tc.load_config(path), written)
    print("\n下一步：")
    print("  python3 tome/scripts/doctor.py    # 检查环境")
    print("  然后在 Claude Code 里说「用 tome 把这些资料做成书」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
