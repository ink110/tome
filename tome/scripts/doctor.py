#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — tome 环境预检

逐项检查并告诉用户「缺什么、怎么补」，不猜测、不自动安装。

用法：
  python3 tome/scripts/doctor.py
  python3 tome/scripts/doctor.py --json
退出码：0 没有阻塞项 / 1 有阻塞项
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
for parent in HERE.parents:
    if (parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✓", WARN: "!", FAIL: "✗"}


def add(results, name, status, detail, fix=""):
    results.append({"name": name, "status": status, "detail": detail, "fix": fix})


def check(results):
    add(results, "Python", OK if sys.version_info >= (3, 9) else FAIL,
        f"{sys.version.split()[0]}（需要 3.9+）",
        "升级 Python：brew install python@3.11")

    path = tc.config_path()
    if path.is_file():
        add(results, "配置文件", OK, str(path))
    else:
        add(results, "配置文件", FAIL, f"还没有 {path}",
            "运行：python3 tome/scripts/setup.py")

    cfg = tc.load_config()
    meta = cfg.get("_meta", {})
    if tc.get("storage") == "obsidian" and not tc.get("obsidian.vault"):
        add(results, "Obsidian 库", FAIL, "storage=obsidian 但没设置 vault 路径",
            "python3 tome/scripts/setup.py --vault <库根目录>")

    workspace = Path(os.path.expanduser(tc.get("workspace", "")))
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / ".tome-write-test"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        add(results, "工作目录", OK, str(workspace))
    except OSError as exc:
        add(results, "工作目录", FAIL, f"{workspace} 不可写：{exc}",
            "改成有写权限的目录：python3 tome/scripts/setup.py")

    pandoc = tc.pandoc_bin()
    if pandoc:
        version = ""
        try:
            version = subprocess.run(
                [pandoc, "--version"], capture_output=True, text=True, timeout=10
            ).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
        add(results, "pandoc（成书）", OK, f"{pandoc} {version}".strip())
    else:
        add(results, "pandoc（成书）", FAIL, "没找到 pandoc",
            "brew install pandoc，或下载后放到 ~/tools/obsidian-epub/pandoc")

    chrome = tc.chrome_bin()
    if chrome:
        add(results, "Chrome（生成封面）", OK, chrome)
    else:
        add(results, "Chrome（生成封面）", WARN, "没找到 Chrome，无法自动生成封面",
            "安装 Google Chrome，或用 --cover 指定现成封面")

    cookie = tc.cookie_file()
    if cookie.is_file():
        size = cookie.stat().st_size
        add(results, "B站 cookie", OK, f"{cookie}（{size} 字节）")
    else:
        add(results, "B站 cookie", WARN, f"没有 {cookie}",
            "只影响 B站采集。装 Chrome 扩展 Get cookies.txt LOCALLY 导出 Netscape 格式")

    provider = tc.provider()
    if provider == "claude":
        add(results, "模型", OK, "claude · 由 Claude Code 直接处理，无需 API Key")
    elif provider == "ollama":
        try:
            models = tc.list_models()
            want = tc.model_name()
            hit = any(m.split(":")[0] == want.split(":")[0] for m in models)
            add(results, "模型", OK if hit else WARN,
                f"ollama 可用，{len(models)} 个模型；当前 {want}"
                + ("" if hit else "（还没 pull）"),
                "" if hit else f"ollama pull {want}")
        except Exception as exc:  # noqa: BLE001
            add(results, "模型", FAIL, f"连不上 Ollama：{exc}",
                "先启动 Ollama：ollama serve，再 ollama pull qwen2.5:14b")
    elif provider == "openai":
        key_env = tc.get("models.openai.api_key_env", "TOME_API_KEY")
        if not os.environ.get(key_env):
            add(results, "模型", FAIL, f"没有环境变量 {key_env}",
                f"export {key_env}=sk-...（不要写进配置文件或仓库）")
        else:
            try:
                models = tc.list_models()
                add(results, "模型", OK,
                    f"openai 兼容接口可用，{len(models)} 个模型；当前 {tc.model_name()}")
            except Exception as exc:  # noqa: BLE001
                add(results, "模型", FAIL, f"接口不可用：{exc}",
                    "检查 base_url、模型名和 API Key")
    else:
        add(results, "模型", FAIL, f"未知档位 {provider}",
            "python3 tome/scripts/setup.py --provider claude|openai|ollama")

    eagle = tc.eagle_settings()
    if eagle["enabled"]:
        try:
            with urllib.request.urlopen(
                f"{eagle['base_url'].rstrip('/')}/library/info", timeout=5
            ) as resp:
                json.loads(resp.read().decode("utf-8", "replace"))
            add(results, "Eagle", OK, f"{eagle['base_url']}（文件夹 {eagle['folder']}）")
        except Exception as exc:  # noqa: BLE001
            add(results, "Eagle", WARN, f"启用了但连不上：{exc}",
                "打开 Eagle，或关掉 Eagle：编辑配置文件 media.eagle.enabled=false")
    else:
        add(results, "Eagle", OK, "未启用（媒体存项目内 assets/）")

    if shutil.which("ffmpeg"):
        add(results, "ffmpeg（无字幕转写）", OK, shutil.which("ffmpeg"))
    else:
        add(results, "ffmpeg（无字幕转写）", WARN, "没找到，只影响 B站无字幕视频",
            "brew install ffmpeg（可选）")

    if meta:
        add(results, "额外", WARN, f"配置文件里有未识别字段 {list(meta)}", "")
    return results


def main():
    ap = argparse.ArgumentParser(description="tome 环境预检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    results = check([])
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("tome 环境预检\n")
        for item in results:
            print(f"  {ICON[item['status']]} {item['name']}：{item['detail']}")
            if item["fix"] and item["status"] != OK:
                print(f"     → {item['fix']}")
        fail = [r for r in results if r["status"] == FAIL]
        warn = [r for r in results if r["status"] == WARN]
        print()
        if fail:
            print(f"有 {len(fail)} 项阻塞，先按上面的提示修好。")
        elif warn:
            print(f"没有阻塞项；{len(warn)} 项可选依赖缺失，按需补。")
        else:
            print("全部就绪。")
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
