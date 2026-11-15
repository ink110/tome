#!/usr/bin/env python3
"""tome-clip: 单篇采集统一入口。

按链接自动路由到现有引擎：
- mp.weixin.qq.com 文章 → tome-wechat/wechat_article.py（直接写 Obsidian 笔记）
- 小红书图文 / 视频 → xhs_note.py（图文取图+OCR；视频自动转 video_note.py）
- 微信视频号 → sph_meta.js 抓元数据，mp4 手动投送后再用 --file
- 本地 mp4/mov → video_note.py

用法:
  python3 clip.py --url <链接> [--topic 专题] [--dry-run]
  python3 clip.py --file <本地视频> [--title 标题] [--sph-meta meta.json]
"""
import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = SKILL_DIR.parent
WECHAT = SKILLS_DIR / "tome-wechat" / "wechat_article.py"


def detect(url: str) -> str:
    if "mp.weixin.qq.com" in url:
        return "wechat"
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return "xhs"
    if "weixin.qq.com/sph" in url or "channels.weixin.qq.com" in url:
        return "sph"
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--file")
    ap.add_argument("--topic", help="公众号专题名（可选）")
    ap.add_argument("--title", help="本地视频标题（可选）")
    ap.add_argument("--author", help="本地视频作者（可选）")
    ap.add_argument("--sph-meta", help="视频号元数据 json（sph_meta.js 产物）")
    ap.add_argument("--workdir", help="工作目录（可选）")
    ap.add_argument("--no-ocr", action="store_true", help="小红书图文只取图，不做 OCR")
    ap.add_argument("--meta-only", action="store_true", help="只解析元数据，不下载/不转写")
    ap.add_argument("--dry-run", action="store_true", help="只打印路由，不执行")
    args = ap.parse_args()

    if args.file:
        kind = "video"
        cmd = [sys.executable, str(SKILL_DIR / "scripts" / "video_note.py"),
               "--file", args.file]
        for key, val in (("--title", args.title), ("--author", args.author),
                         ("--sph-meta", args.sph_meta), ("--workdir", args.workdir)):
            if val:
                cmd += [key, val]
    else:
        kind = detect(args.url)
        if kind == "wechat":
            cmd = [sys.executable, str(WECHAT), "--url", args.url]
            if args.topic:
                cmd += ["--topic", args.topic]
        elif kind == "xhs":
            cmd = [sys.executable, str(SKILL_DIR / "scripts" / "xhs_note.py"),
                   "--url", args.url]
            for key, val in (("--workdir", args.workdir),):
                if val:
                    cmd += [key, val]
            if args.no_ocr:
                cmd.append("--no-ocr")
            if args.meta_only:
                cmd.append("--meta-only")
        elif kind == "sph":
            base = Path(args.workdir) if args.workdir else Path.home() / ".cache" / "tome-clip"
            base.mkdir(parents=True, exist_ok=True)
            out = base / "sph_meta.json"
            cmd = ["node", str(SKILL_DIR / "scripts" / "sph_meta.js"), args.url, str(out)]
        else:
            raise SystemExit(
                "不认识的链接。目前支持：\n"
                "  - mp.weixin.qq.com 公众号文章\n"
                "  - xiaohongshu.com / xhslink.com 小红书图文或视频\n"
                "  - weixin.qq.com/sph 视频号（先抓元数据，再手动投送 mp4）\n"
                "  - 本地 mp4/mov（用 --file）"
            )

    print(f"[tome-clip] 路由: {kind}")
    print("[tome-clip] 执行:", " ".join(cmd), flush=True)
    if args.dry_run:
        return

    rc = subprocess.run(cmd, cwd=str(SKILL_DIR / "scripts")).returncode

    if kind == "sph" and rc == 0:
        print(f"\n[tome-clip] 视频号元数据已写入：{out}")
        print("下一步：用桌面微信 / res-downloader 保存 mp4，或手机保存后 AirDrop 到 Mac；")
        print(f"然后：python3 clip.py --file <mp4> --sph-meta {out}")
    if kind == "wechat" and rc != 0:
        print("\n[tome-clip] 公众号抓取失败。若提示「js_content 为空」，可能是微信图片消息，")
        print("按 tome-wechat/references/image-post.md 的图片 OCR 流程处理。")
    sys.exit(rc)


if __name__ == "__main__":
    main()
