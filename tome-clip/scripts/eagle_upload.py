#!/usr/bin/env python3
"""把 tome-clip 的视频帧 / 图文原图上传到 Eagle，并输出 eagle:// 溯源映射。

和 tome-wechat 共用 ~/.config/tome/config.json 的 media.eagle 配置：
  {"media": {"store": "eagle", "eagle": {"enabled": true,
     "base_url": "http://localhost:41595/api",
     "folder": "obsidian文章素材"}}}

用法：
  # 视频：只上传 Claude 筛中的帧
  python3 eagle_upload.py --workdir <workdir> \
      --files f0002.jpg,f0003.jpg --folder obsidian文章素材

  # 视频：上传全部候选帧（不推荐，通常只传入选帧）
  python3 eagle_upload.py --workdir <workdir> --all

  # 图文：上传 note.json 里的全部原图
  python3 eagle_upload.py --workdir <workdir>

  # 顺便把 source.mp4 也传进 Eagle
  python3 eagle_upload.py --workdir <workdir> --files f0002.jpg --video

产物：<workdir>/eagle_uploads.json，字段 item_id / eagle_url，供笔记组装使用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 复用 tome 技能族的配置（tome/lib/tome_common.py）
SKILLS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS_ROOT / "tome" / "lib"))
try:
    import tome_common as tc
except Exception:  # 兜底：独立运行时只读 media.eagle
    tc = None


def fallback_settings() -> dict:
    path = Path(os.environ.get("TOME_CONFIG", Path.home() / ".config" / "tome" / "config.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    eagle = (((data.get("media") or {}).get("eagle")) or {})
    return {
        "enabled": bool(eagle.get("enabled", False)),
        "base_url": eagle.get("base_url", "http://localhost:41595/api"),
        "folder": eagle.get("folder", "tome素材"),
    }


def settings() -> dict:
    if tc is not None:
        return tc.eagle_settings()
    return fallback_settings()


def eagle_get(base: str, path: str) -> dict:
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def eagle_post(base: str, path: str, body: dict, timeout: int = 600) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def flatten_folders(nodes: list, prefix: str = "") -> list:
    out = []
    for f in nodes or []:
        path = f"{prefix}/{f['name']}" if prefix else f["name"]
        out.append((path, f.get("id"), f.get("name")))
        out.extend(flatten_folders(f.get("children") or [], path))
    return out


def resolve_folder(base: str, name: str) -> str:
    d = eagle_get(base, "/folder/list")
    if d.get("status") != "success":
        raise RuntimeError("无法列出 Eagle 文件夹，请确认 Eagle 正在运行")
    for path, fid, fname in flatten_folders(d.get("data") or []):
        if path == name or fname == name:
            return fid
    c = eagle_post(base, "/folder/create", {"folderName": name})
    if c.get("status") == "success" and isinstance(c.get("data"), dict):
        return c["data"]["id"]
    raise RuntimeError(f"无法创建 Eagle 文件夹：{name}")


def upload_file(base: str, path: Path, name: str, folder_id: str, tags: list) -> str:
    body = {"path": str(path), "name": name}
    if folder_id:
        body["folderId"] = folder_id
    if tags:
        body["tags"] = tags
    d = eagle_post(base, "/item/addFromPath", body)
    if d.get("status") != "success" or not isinstance(d.get("data"), str):
        raise RuntimeError(f"Eagle 上传失败：{d.get('message') or d}")
    return d["data"]


def resolve_inputs(workdir: Path, files_arg: str | None, upload_all: bool) -> list[Path]:
    if files_arg:
        paths = []
        for raw in files_arg.split(","):
            name = raw.strip()
            if not name:
                continue
            candidate = Path(name).expanduser()
            if candidate.is_absolute() and candidate.is_file():
                paths.append(candidate)
                continue
            for base in (workdir / "frames", workdir / "images", workdir):
                p = base / name
                if p.is_file():
                    paths.append(p)
                    break
            else:
                raise SystemExit(f"找不到文件：{name}（已查 frames/、images/、workdir/）")
        return paths

    work_json = workdir / "work.json"
    note_json = workdir / "note.json"
    if work_json.exists():
        data = json.loads(work_json.read_text(encoding="utf-8"))
        frames = [Path(f["file"]) for f in data.get("frames", []) if f.get("file")]
        if not upload_all:
            raise SystemExit("work.json 里有多张候选帧；请用 --files 只传入选帧，或加 --all 全部上传。")
        return [p for p in frames if p.is_file()]
    if note_json.exists():
        data = json.loads(note_json.read_text(encoding="utf-8"))
        paths = []
        for raw in data.get("local_images", []):
            if not raw:
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = workdir / p
            paths.append(p)
        return [p for p in paths if p.is_file()]
    raise SystemExit("workdir 里没有 work.json 或 note.json；请用 --files 指定文件。")


def main() -> None:
    ap = argparse.ArgumentParser(description="上传 tome-clip 媒体到 Eagle")
    ap.add_argument("--workdir", required=True, help="tome-clip 工作目录（含 work.json 或 note.json）")
    ap.add_argument("--files", help="逗号分隔的文件名或绝对路径；视频只传入选帧时用它")
    ap.add_argument("--all", action="store_true", help="work.json 里全部候选帧都上传")
    ap.add_argument("--folder", help="Eagle 文件夹名（默认取 media.eagle.folder）")
    ap.add_argument("--tags", default="", help="逗号分隔的 Eagle 标签")
    ap.add_argument("--video", action="store_true", help="同时上传 workdir/source.mp4")
    ap.add_argument("--dry-run", action="store_true", help="只列出将上传的文件，不调用 Eagle")
    args = ap.parse_args()

    cfg = settings()
    if not cfg.get("enabled") and not args.dry_run:
        raise SystemExit("Eagle 未启用：在 ~/.config/tome/config.json 设置 media.eagle.enabled=true")
    base = str(cfg.get("base_url") or "http://localhost:41595/api").rstrip("/")
    folder_name = args.folder or cfg.get("folder") or "tome素材"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    workdir = Path(args.workdir).expanduser().resolve()
    if not workdir.is_dir():
        raise SystemExit(f"workdir 不存在：{workdir}")
    paths = resolve_inputs(workdir, args.files, args.all)
    if args.video:
        video = workdir / "source.mp4"
        if video.is_file():
            paths.append(video)
    if not paths:
        raise SystemExit("没有可上传的文件。")

    if args.dry_run:
        print(json.dumps({"folder": folder_name, "files": [str(p) for p in paths]},
                         ensure_ascii=False, indent=2))
        return

    folder_id = resolve_folder(base, folder_name)
    items = []
    for path in paths:
        stem = path.stem
        name = f"{workdir.name}-{stem}" if path.name.startswith(("f", "0")) else stem
        item_id = upload_file(base, path, name, folder_id, tags)
        items.append({"file": path.name, "path": str(path),
                      "item_id": item_id, "eagle_url": f"eagle://item/{item_id}"})
        print(f"  ✓ {path.name} → eagle://item/{item_id}")

    out = workdir / "eagle_uploads.json"
    out.write_text(json.dumps({"folder": folder_name, "folder_id": folder_id,
                               "items": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已上传 {len(items)} 个文件到 Eagle「{folder_name}」")
    print(f"映射：{out}")
    print("\n笔记里在每张图下加一行：")
    print("  [在 Eagle 查看](eagle://item/<item_id>)")


if __name__ == "__main__":
    main()
