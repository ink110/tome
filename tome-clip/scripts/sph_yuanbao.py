#!/usr/bin/env python3
"""视频号 → mp4：腾讯元宝官方登录态解析（一次扫码，之后自动）。

依赖外部开源项目 download-wechat-channels（MIT）：
  https://github.com/Oneideals/canghe-download-wechat-channels

流程：
  1. channels.py prepare        → 创建一次性 profile 文件
  2. sph_yuanbao.cjs            → 打开元宝登录页；首次扫码；解析官方 playable_url
  3. channels.py download       → 下载 MP4（官方 channels 链路）
  4. sph_fetch.py --file        → 抓元数据 + 跑 video_note.py

用法：
  python3 sph_yuanbao.py --url <sph链接>
  python3 sph_yuanbao.py --url <sph链接> --login-timeout 300
  python3 sph_yuanbao.py --url <sph链接> --prepare-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")
DEFAULT_SKILLS = [
    os.environ.get("WECHAT_CHANNELS_SKILL"),
    Path.home() / "tools" / "download-wechat-channels" / "download-wechat-channels",
    Path.home() / ".claude" / "skills" / "download-wechat-channels",
    HERE.parent / "vendor" / "download-wechat-channels",
]


def log(msg: str) -> None:
    print(f"[sph_yuanbao] {msg}", flush=True)


def find_skill() -> Path | None:
    for candidate in DEFAULT_SKILLS:
        if not candidate:
            continue
        root = Path(candidate).expanduser()
        if (root / "scripts" / "channels.py").is_file() and (root / "scripts" / "yuanbao_bridge.mjs").is_file():
            return root
    return None


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def parse_json(stdout: str) -> dict:
    text = (stdout or "").strip()
    start = text.find("{")
    if start < 0:
        return {}
    try:
        value = json.loads(text[start:])
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="视频号 → 腾讯元宝官方解析 → MP4 → tome-clip")
    ap.add_argument("--url", required=True, help="weixin.qq.com/sph/... 分享链接")
    ap.add_argument("--output-dir", help="MP4 保存目录（默认 ~/Downloads/视频号）")
    ap.add_argument("--quality", choices=["auto", "original", "compatible", "h265"], default="auto")
    ap.add_argument("--login-timeout", type=int, default=300, help="等待扫码秒数，默认 300")
    ap.add_argument("--headless", action="store_true", help="已登录后可用无头模式")
    ap.add_argument("--prepare-only", action="store_true", help="只创建 profile，不打开浏览器/不下载")
    ap.add_argument("--no-pipeline", action="store_true", help="只下载 MP4，不跑转写/抽帧")
    args = ap.parse_args()

    skill = find_skill()
    if not skill:
        log("没有找到 download-wechat-channels。先安装：")
        print("  git clone https://github.com/Oneideals/canghe-download-wechat-channels.git \\\n"
              "    ~/tools/download-wechat-channels\n"
              "  或设置 WECHAT_CHANNELS_SKILL=<项目根目录>")
        sys.exit(2)
    channels = skill / "scripts" / "channels.py"
    bridge = skill / "scripts" / "yuanbao_bridge.mjs"
    log(f"后端：{skill}")

    r = run([sys.executable, str(channels), "--json", "prepare", args.url], 60)
    prepared = parse_json(r.stdout)
    if not prepared.get("ok"):
        log(f"prepare 失败：{(prepared.get('message') or r.stderr or r.stdout)[-400:]}")
        sys.exit(2)
    share_url = prepared["share_url"]
    profile_file = prepared["profile_file"]
    log(f"一次性解析文件：{profile_file}")
    if args.prepare_only:
        return

    if not NODE:
        log("没有 node，无法驱动元宝登录页")
        sys.exit(2)
    log("打开腾讯元宝登录页；首次请用微信扫码…")
    r = run([NODE, str(HERE / "sph_yuanbao.cjs"), share_url, profile_file,
             str(bridge), str(args.login_timeout), "1" if args.headless else "0"],
            args.login_timeout + 90)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        if "LOGIN_TIMEOUT" in out:
            log("等待扫码超时；重新运行即可，登录态会保留。")
        else:
            log(f"元宝解析失败：{(r.stderr or out)[-400:]}")
        sys.exit(2)
    log("官方解析完成，开始下载…")

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path.home() / "Downloads" / "视频号"
    output_dir.mkdir(parents=True, exist_ok=True)
    # auto 会为了「原画」把媒体 URL 裁剪成 encfilekey+token；部分视频这个裁剪地址
    # 返回 HTTP 400。失败时自动降级到 compatible（完整 URL，H.264）。
    qualities = [args.quality]
    if args.quality == "auto":
        qualities.append("compatible")
    downloaded: dict = {}
    for quality in qualities:
        r = run([sys.executable, str(channels), "--json", "download", share_url,
                 "--official-profile", profile_file, "--quality", quality,
                 "--output-dir", str(output_dir)], 900)
        downloaded = parse_json(r.stdout)
        if downloaded.get("path"):
            if quality != args.quality:
                log(f"{args.quality} 失败，已降级到 {quality}")
            break
        log(f"{quality} 下载失败：{(downloaded.get('message') or r.stderr or r.stdout)[-400:]}")
    path = downloaded.get("path")
    if not path:
        log("下载失败（已尝试 quality 降级）")
        sys.exit(2)
    path = Path(path)
    if not path.is_file():
        log(f"下载结果不存在：{path}")
        sys.exit(2)
    log(f"MP4：{path}（{downloaded.get('size_human') or ''}）")

    if args.no_pipeline:
        return
    log("交给 sph_fetch.py：抓元数据 + 转写 + 抽帧…")
    r = subprocess.run([sys.executable, str(HERE / "sph_fetch.py"), "--url", args.url,
                        "--file", str(path)], cwd=str(HERE))
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
