#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化一个「公众人物解构」项目目录。

用法:
  python3 new_project.py --person "<人物>" \
      --collection "https://www.bilibili.com/video/BVxxxx/" \
      --root "<信息源根目录>"

不给 --root 时，项目目录由共享配置决定（tc.project_dir）。
不覆盖已有文件；要重建加 --force。
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

PROGRESS = """# {person} · 进度

> 项目：{person}
> 主信息源：{collection}
> 开始日期：{today}
> 用法：每完成一步勾掉一项，并在后面写产物路径。中断后从这里继续。

- [ ] 0 初始化目录
- [ ] 1a 采集主信息源（raw-only，全量落盘）
- [ ] 1b 生成清单与覆盖度（manifest.py）
- [ ] 1c 本地模型蒸馏（分批 distill-only）
- [ ] 2 来源地图（10-画像/来源地图.md）
- [ ] G1 人工确认补充范围
- [ ] 3 外部补充（00-收集-外部/）
- [ ] 4 分片调研（10-画像/_调研/）
- [ ] 5 综合画像（10-画像/{person}·思维操作系统2.0.md）
- [ ] G2 人工确认画像结构
- [ ] 6 核验表（10-画像/{person}·核验表.md）
- [ ] 7 成书 md（20-输出/{person}·思维操作系统2.0（全书）.md）
- [ ] 7b 成书 epub（{output_dir}/{person}·思维操作系统2.0（阅读版）.epub）
- [ ] 8 传播复盘（可选：传播案例库 + 选题清单）
- [ ] G3 最终验收

## 备注

- 缺口：
- 外部材料未完成维度：
- 已知问题：
"""

SOURCE_MAP = """# {person}·来源地图

> 生成日期：{today}
> 主信息源：{collection}
> 语料截止：
> 已采集：一手 0 集｜外部 0 篇｜访谈 0 篇

## 一、主信息源清单

| 集号 | 标题 | 日期 | 时长 | 字幕/ASR | 播放 | 点赞 | 收藏 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 二、来源结构

| 类型 | 数量 | 说明 |
| --- | --- | --- |
| 一手自述（口播 / 演讲 / 长文） | | |
| 一手对话（访谈 / 播客 / 对谈） | | |
| 二手整理（媒体转述 / 合集搬运） | | |
| 外部评价（批评 / 诉讼 / 监管 / 同行） | | |

## 三、覆盖矩阵

| 年份 | 钱与投资 | 选择与职业 | 关系与边界 | 规则与风险 | 内容与表达 | 自我与叙事 | 其它 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 四、缺口

1. 年份缺口：
2. 主题缺口：
3. 场合缺口：
4. 反证缺口：

## 五、补充清单

| 优先级 | 搜什么 | 去哪搜 | 为什么 | 预期产出 |
| --- | --- | --- | --- | --- |

## 六、判断

1. 这套语料能支撑什么：
2. 这套语料不能支撑什么：
3. 这套语料偏向什么：
"""


def write_if_missing(path, content, force=False):
    if os.path.exists(path) and not force:
        print(f"skip  {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"write {path}")


def main():
    p = argparse.ArgumentParser(description="初始化公众人物解构项目")
    p.add_argument("--person", required=True, help="人物名")
    p.add_argument("--root", default=None,
                   help="信息源根目录；给了则用 <root>/<人物>，否则由共享配置决定")
    p.add_argument("--collection", default="", help="主信息源链接（可空）")
    p.add_argument("--prefix", default=None, help="一手文件名前缀，默认 <人物>")
    p.add_argument("--force", action="store_true", help="覆盖已有模板文件")
    args = p.parse_args()

    person = args.person.strip()
    if args.root:
        root = Path(args.root).expanduser() / person
    else:
        root = tc.project_dir(person)
    prefix = args.prefix or person
    today = date.today().isoformat()
    print(f"项目目录：{os.path.abspath(root)}")

    for d in ["00-收集", "00-收集-外部", "10-画像/_调研",
              "10-画像/_调研-外部", "20-输出", "_meta"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    if not tc.is_obsidian():
        os.makedirs(os.path.join(root, "assets"), exist_ok=True)

    write_if_missing(os.path.join(root, "_meta", "progress.md"),
                     PROGRESS.format(person=person, collection=args.collection or "（待填）",
                                     today=today, output_dir=tc.output_dir()), args.force)
    write_if_missing(os.path.join(root, "10-画像/来源地图.md"),
                     SOURCE_MAP.format(person=person, collection=args.collection or "（待填）",
                                       today=today), args.force)

    meta_path = os.path.join(root, "_meta/project.json")
    if not os.path.exists(meta_path) or args.force:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "person": person,
                "prefix": prefix,
                "collection": args.collection,
                "root": os.path.abspath(root),
                "created": today,
                "schema": "tome-portrait/v1",
                "storage": tc.is_obsidian(),
            }, f, ensure_ascii=False, indent=2)
        print(f"write {meta_path}")
    else:
        print(f"skip  {meta_path}")

    print("\n下一步：")
    print(f"  1. 采集（先 raw-only）：")
    print(f"     python3 <skills>/tome-bili/crawl_collection.py \"<BV>\" \\")
    print(f"       --out \"{root}/00-收集\" --prefix \"{prefix}\" --raw-only")
    print(f"  2. 生成清单：")
    print(f"     python3 <skills>/tome-bili/manifest.py \\")
    print(f"       --dir \"{root}/00-收集\" --out \"{root}/00-收集/_manifest.json\" \\")
    print(f"       --coverage \"{root}/00-收集/_coverage.md\"")
    print(f"  3. 读 references/source-map.md，填来源地图。")
    print(f"\n注：<skills> 是技能安装目录（默认 ~/.claude/skills）。")


if __name__ == "__main__":
    main()
