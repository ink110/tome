#!/usr/bin/env python3
"""批量抓取后的素材去重后处理。

1. 笔记媒体目录（assets/ 或旧的 eagle-cache/）内按内容哈希去重，
   引用改到保留副本，重复文件移入备份目录（自动执行）。
2. Eagle：只统计重复（按内容哈希），并可选打开 Eagle 自带的
   「查找 > 扫描重复的文件」窗口；绝不自动合并/删除。

用法：
    python3 post_batch_dedupe.py [--dry-run] [--no-eagle] [--no-open-eagle]
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

MEDIA_DIR_NAMES = {"assets", "eagle-cache"}
VAULT = str(tc.source_root())
EAGLE_LIBRARY = tc.eagle_settings().get("library") or ""
BACKUP_DIR = str(tc.backup_dir() / "wechat-dedupe")
MIN_SIZE = 1024  # 小于 1KB 的图标类文件不参与，避免误伤


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_cache_dirs(vault):
    out = []
    for dp, dn, _ in os.walk(vault):
        if os.path.basename(dp) in MEDIA_DIR_NAMES:
            out.append(dp)
            dn[:] = []
    return out


def group_duplicates(paths):
    by_size = collections.defaultdict(list)
    for p in paths:
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz >= MIN_SIZE:
            by_size[sz].append(p)
    groups = []
    for _, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        by_hash = collections.defaultdict(list)
        for p in candidates:
            try:
                by_hash[(md5(p), os.path.splitext(p)[1].lower())].append(p)
            except OSError:
                continue
        groups.extend(v for v in by_hash.values() if len(v) > 1)
    return groups


def count_references(md_files):
    ref = collections.Counter()
    for f in md_files:
        try:
            s = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name in _media_names(s):
            ref[os.path.basename(name)] += 1
    return ref


def _media_names(text):
    import re
    names = []
    for prefix in MEDIA_DIR_NAMES:
        names += re.findall(rf"!\[\[{prefix}/([^\]|#]+)", text)
        names += re.findall(rf"\]\({prefix}/([^)\s]+)", text)
    names += re.findall(r"\[\[([^\]|#]+\.(?:png|jpe?g|gif|webp|mp4|mov|m4a|mp3))\]\]", text)
    return names


def backup_markdown(vault, md_files):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, time.strftime("vault_md_%Y%m%d_%H%M%S.tgz"))
    with tarfile.open(path, "w:gz") as tar:
        for f in md_files:
            tar.add(f, arcname=os.path.relpath(f, vault))
    return path


def dedupe_obsidian(vault, apply):
    cache_dirs = find_cache_dirs(vault)
    # 只在同一个媒体目录内去重：assets/ 是相对路径，跨项目合并会断链
    groups = []
    for cache_dir in cache_dirs:
        media = []
        for dp, _, fn in os.walk(cache_dir):
            media += [os.path.join(dp, f) for f in fn if not f.startswith(".")]
        groups += group_duplicates(media)
    md_files = [f for f in glob.glob(os.path.join(vault, "**", "*.md"), recursive=True)
                if not any(f"/{name}/" in f for name in MEDIA_DIR_NAMES)]
    ref = count_references(md_files)

    plan = []
    for g in groups:
        g = sorted(g, key=lambda p: (-ref[os.path.basename(p)],
                                     p.count(os.sep),  # 同等引用时优先 vault 根缓存
                                     len(os.path.basename(p)),
                                     os.path.basename(p)))
        plan.append((g[0], g[1:]))

    # 改引用前先备份原始笔记，便于整批回滚。
    backup = backup_markdown(vault, md_files) if (apply and plan) else None

    # 先把所有引用改到保留副本，再确认无残留，最后才移文件。
    changed = set()
    dup_names = {os.path.basename(d) for _, dups in plan for d in dups}
    for canon, dups in plan:
        cname = os.path.basename(canon)
        prefix = os.path.basename(os.path.dirname(canon))
        for dup in dups:
            dname = os.path.basename(dup)
            for f in md_files:
                s = open(f, encoding="utf-8", errors="ignore").read()
                orig = s
                s = s.replace(f"{prefix}/{dname}", f"{prefix}/{cname}")
                s = s.replace(f"[[{dname}]]", f"[[{cname}]]")
                s = s.replace(f"[[{dname}|", f"[[{cname}|")
                if s != orig:
                    changed.add(f)
                    if apply:
                        open(f, "w", encoding="utf-8").write(s)

    still = set()
    for f in md_files:
        s = open(f, encoding="utf-8", errors="ignore").read()
        for n in dup_names:
            if (f"assets/{n}" in s or f"eagle-cache/{n}" in s
                    or f"[[{n}]]" in s or f"[[{n}|" in s):
                still.add(n)

    moved = skipped = 0
    for _, dups in plan:
        for dup in dups:
            if os.path.basename(dup) in still:
                skipped += 1
                continue
            if apply:
                trash = shutil.which("trash") or "/usr/bin/trash"
                if os.path.exists(trash):
                    subprocess.run([trash, dup], check=False)
                else:
                    os.makedirs(BACKUP_DIR, exist_ok=True)
                    shutil.move(dup, os.path.join(BACKUP_DIR, os.path.basename(dup)))
            moved += 1

    return {
        "cache_dirs": [os.path.relpath(d, vault) for d in cache_dirs],
        "files_before": len(media),
        "groups": len(plan),
        "duplicates": moved,
        "skipped_still_referenced": skipped,
        "changed_reference_files": len(changed),
        "backup": backup,
    }


def eagle_duplicates(library):
    meta_files = glob.glob(os.path.join(library, "images", "*.info", "metadata.json"))
    items = []
    for mf in meta_files:
        try:
            m = json.load(open(mf))
        except (OSError, ValueError):
            continue
        if m.get("isDeleted"):
            continue
        name, ext = m.get("name", ""), m.get("ext", "")
        original = os.path.join(os.path.dirname(mf), f"{name}.{ext}")
        if not os.path.exists(original):
            continue
        try:
            sz = os.path.getsize(original)
        except OSError:
            continue
        items.append((sz, ext.lower(), original, m.get("id", ""), m.get("folders", [])))

    by_size = collections.defaultdict(list)
    for it in items:
        by_size[(it[0], it[1])].append(it)

    dup_groups, suspect_groups = [], []
    for _, cand in by_size.items():
        if len(cand) < 2:
            continue
        by_hash = collections.defaultdict(list)
        hashes = {}
        for it in cand:
            try:
                h = md5(it[2])
            except OSError:
                continue
            hashes[it[2]] = h
            by_hash[h].append(it)
        exact = [v for v in by_hash.values() if len(v) > 1]
        dup_groups.extend(exact)
        # 同名同大小但哈希不同：并发下载写坏的副本，Eagle 感知扫描可识别。
        by_name = collections.defaultdict(list)
        for it in cand:
            if it[2] in hashes:
                by_name[os.path.basename(it[2])].append(it)
        for v in by_name.values():
            if len(v) > 1 and len({hashes[it[2]] for it in v}) > 1:
                suspect_groups.append(v)

    extras = sum(len(g) - 1 for g in dup_groups)
    reclaim = sum((len(g) - 1) * g[0][0] for g in dup_groups)
    suspect_extra = sum(len(g) - 1 for g in suspect_groups)
    samples = []
    for g in sorted(dup_groups, key=len, reverse=True)[:3]:
        samples.append({"kind": "exact", "name": os.path.basename(g[0][2]),
                        "copies": len(g), "size": g[0][0],
                        "folders": sorted({f for it in g for f in it[4]})})
    for g in sorted(suspect_groups, key=len, reverse=True)[:3]:
        samples.append({"kind": "suspect", "name": os.path.basename(g[0][2]),
                        "copies": len(g), "size": g[0][0],
                        "folders": sorted({f for it in g for f in it[4]})})
    return {
        "items": len(items),
        "duplicate_groups": len(dup_groups),
        "extra_items": extras,
        "reclaim_mb": round(reclaim / 1048576, 1),
        "suspect_groups": len(suspect_groups),
        "suspect_extra_items": suspect_extra,
        "samples": samples,
    }


def open_eagle_scan():
    script = (
        'tell application "Eagle" to activate\n'
        'delay 0.5\n'
        'tell application "System Events" to tell process "Eagle"\n'
        '  click menu item "扫描重复的文件" of menu 1 of menu bar item "查找" of menu bar 1\n'
        'end tell'
    )
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        return r.returncode == 0, (r.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计，不改文件、不移文件")
    ap.add_argument("--no-eagle", action="store_true", help="跳过 Eagle 统计")
    ap.add_argument("--no-open-eagle", action="store_true", help="不自动打开 Eagle 扫描窗口")
    ap.add_argument("--vault", default=VAULT, help="扫描哪个根目录下的媒体目录")
    ap.add_argument("--eagle-library", default=EAGLE_LIBRARY,
                    help="Eagle 素材库路径（配置 media.eagle.library）")
    args = ap.parse_args()

    result = {"obsidian": dedupe_obsidian(args.vault, apply=not args.dry_run)}
    eagle_on = tc.eagle_enabled()
    if not args.no_eagle and eagle_on and args.eagle_library and os.path.isdir(args.eagle_library):
        result["eagle"] = eagle_duplicates(args.eagle_library)
        e = result["eagle"]
        if not args.dry_run and not args.no_open_eagle and (
                e["duplicate_groups"] or e["suspect_groups"]):
            ok, err = open_eagle_scan()
            result["eagle"]["scan_window_opened"] = ok
            if not ok:
                result["eagle"]["open_error"] = err

    print(json.dumps(result, ensure_ascii=False, indent=1))

    o, e = result["obsidian"], result.get("eagle")
    print(f"\n媒体目录：{o['files_before']} 个文件，{o['groups']} 组重复，"
          f"移走 {o['duplicates']} 个，改写 {o['changed_reference_files']} 篇笔记"
          + ("（dry-run 未执行）" if args.dry_run else ""))
    if e:
        print(f"Eagle：{e['items']} 个素材，{e['duplicate_groups']} 组完全重复"
              f"（多余 {e['extra_items']} 个，约 {e['reclaim_mb']}MB），"
              f"{e['suspect_groups']} 组疑似重复（同名同大小，多余 {e['suspect_extra_items']} 个）。")
        if e["duplicate_groups"] or e["suspect_groups"]:
            print("       请用 Eagle 自带的「查找 > 扫描重复的文件 > 扫描全部 > 合并」处理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
