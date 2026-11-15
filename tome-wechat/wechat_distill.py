#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_distill.py — 给微信公众号笔记生成「阅读卡片」

每篇生成：
  一句话摘要 / 核心观点速览 / 案例速查表 / 金句摘录 / 学习记录(6类) / 标签
并插入到正文之前；原文一字不改，放在「## 正文（原文）」下。

同时基于主题键 + 标题/正文相似度找重复候选，再逐对确认，输出 _dedup.md。
只标记、不删除任何笔记。

用法：
  python3 wechat_distill.py --topic <专题名>
  python3 wechat_distill.py --topic <专题名> --limit 1 --no-dedup   # 冒烟
  python3 wechat_distill.py --topic <专题名> --force                # 全部重做
  python3 wechat_distill.py --topic <专题名> --dedup-only           # 只重新去重
  python3 wechat_distill.py --topic <专题名> --plan                 # claude 档位：导出任务
  python3 wechat_distill.py --topic <专题名> --apply _distill-plan.json  # 回写结果

模型档位见 ~/.config/tome/config.json（claude / openai / ollama）。
退出码：0 全成功 / 1 没有笔记 / 2 模型不可用 / 3 部分失败 / 4 全失败 / 5 需要 Claude 处理 / 130 Ctrl-C
"""
import sys, os, re, json, time, glob, shutil, argparse, difflib
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "tome" / "lib" / "tome_common.py").is_file():
        sys.path.insert(0, str(_parent / "tome" / "lib"))
        break

import tome_common as tc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = None


def note_ref(text):
    """笔记之间的引用：Obsidian 用双链，普通文件夹用纯文本。"""
    return f"[[{text}]]" if tc.is_obsidian() else text

AI_START = "<!-- wechat-ai:start -->"
AI_END = "<!-- wechat-ai:end -->"
REPORT_START = "<!-- wechat-ai-report:start -->"
REPORT_END = "<!-- wechat-ai-report:end -->"

CONTENT_TYPES = {"方法", "观点", "案例", "资讯", "政策", "工具"}
LEARNING_KEYS = [
    ("核心观点", "core"), ("论证结构", "structure"), ("案例", "cases"),
    ("表达手法", "expression"), ("争议点", "controversy"), ("思维地图", "map"),
]

SYSTEM_PROMPT = """你是中文内容编辑，为微信公众号文章制作「阅读卡片」。只依据原文，不编造。只输出一个 JSON 对象，不要 Markdown 代码块。

JSON 结构：
{
  "summary": "一句话摘要，30-60字，说清文章解决什么问题/给出什么结论",
  "key_points": ["3-5条核心观点，每条20-50字"],
  "cases": [{"name":"案例/品牌/事件","story":"一句话故事，保留起因-过程-结果","point":"支撑的观点","reuse":"可复用场景"}],
  "quotes": ["3-5条原文金句，必须逐字摘录，不改写"],
  "learning": {
    "core": "核心观点，50字内",
    "structure": "论证结构，如：现象→原因→方法→案例→结论",
    "cases": "案例如何支撑观点，50字内",
    "expression": "表达手法，50字内",
    "controversy": "争议点或局限，50字内；没有写「无明显争议」",
    "map": "思维导图式一句话：A → B → C"
  },
  "tags": ["5-10个中文名词标签，2-8字，不带#"],
  "topic_key": "文章主题的规范短名，6-15字，用于跨文章去重",
  "content_type": "方法/观点/案例/资讯/政策/工具 之一"
}

硬性要求：
- 原文没有的案例、金句返回空数组，不要补。
- 金句必须是原文连续片段，不能概括或改写。
- 标签不要只写大词，要和主题组合，如「平台运营」「爆款笔记」「政策解读」。
- topic_key 要能概括同一主题的多篇文章；同主题文章应尽量给相同的 topic_key。
- 所有字段必填。"""


# ---------------------------------------------------------------- 基础工具
def q(s):
    return json.dumps(str(s), ensure_ascii=False)


def one_line(s, limit=0):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = s.replace("**", "").replace("【】", "")
    return s[:limit].strip() if limit else s


def unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        try:
            return json.loads(v)
        except Exception:
            return v[1:-1].replace('\\"', '"')
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def fm_scalar(fm, key):
    m = re.search(r"^%s:\s*(.*)$" % re.escape(key), fm, re.M)
    return unquote(m.group(1)) if m else ""


def fm_author(fm):
    m = re.search(r"^author:\s*\n((?:[ \t]+-.*\n?)+)", fm, re.M)
    if m:
        names = re.findall(r"\[\[(.*?)\]\]", m.group(1))
        if names:
            return "、".join(names)
        vals = re.findall(r"^\s*-\s*(.+)$", m.group(1), re.M)
        if vals:
            return "、".join(unquote(x) for x in vals)
    return fm_scalar(fm, "author")


def fm_ai_tags(fm):
    m = re.search(r"^ai_tags:\s*\n((?:[ \t]+-.*\n?)+)", fm, re.M)
    if not m:
        return []
    return [unquote(x) for x in re.findall(r"^\s*-\s*(.+)$", m.group(1), re.M)]


def split_note(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def split_existing(after):
    """把「已有 AI 区块 + 出处块 + 正文」拆开；兼容首次运行与 --force。"""
    after = re.sub(re.escape(AI_START) + r".*?" + re.escape(AI_END) + r"\n?",
                   "", after, flags=re.S).strip("\n")
    if "## 正文（原文）" in after:
        head, body = after.split("## 正文（原文）", 1)
        head = "\n".join(l for l in head.splitlines() if l.strip() != "---").strip()
        return head, body.lstrip("\n")
    lines = after.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    j = i
    while j < len(lines) and lines[j].startswith(">"):
        j += 1
    if j > i:
        return "\n".join(lines[i:j]).strip(), "\n".join(lines[j:]).lstrip("\n")
    return "", after


def clean_for_model(body, limit=24000):
    text = re.sub(r"!\[\[.*?\]\]", "", body)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[Eagle\]\(eagle://[^)]*\)", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"^>\s*⚠️.*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit - 4000] + "\n\n……（中略）……\n\n" + text[-4000:]
    return text


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 模型
def ollama_models():
    """返回当前档位可用的模型名列表（claude 档位返回占位说明）。"""
    return tc.list_models()


def call_ollama(model, system, user, timeout=900, temperature=0.2, num_predict=1800):
    return tc.chat(system, user, model=model, timeout=timeout,
                   temperature=temperature, json_mode=True, stream=True,
                   num_ctx=32768, num_predict=num_predict)


def parse_json(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    raise ValueError("模型输出不是合法 JSON")


# ---------------------------------------------------------------- 结构化
def build_user(meta, body):
    return (f"标题：{meta['title']}\n作者：{meta['author'] or '未知'}\n"
            f"发布时间：{meta['published'] or '未知'}\n专辑：{meta['album'] or '无'}\n"
            f"正文：\n{body}\n")


def normalize_data(data, meta, body):
    if not isinstance(data, dict):
        data = {}
    out = {}
    out["summary"] = one_line(data.get("summary"), 200) or meta.get("description") or meta["title"]
    out["key_points"] = [one_line(x, 120) for x in (data.get("key_points") or [])
                         if one_line(x)][:5]
    cases = []
    for c in (data.get("cases") or [])[:5]:
        if not isinstance(c, dict):
            continue
        item = {
            "name": one_line(c.get("name"), 40),
            "story": one_line(c.get("story"), 160),
            "point": one_line(c.get("point"), 100),
            "reuse": one_line(c.get("reuse"), 100),
        }
        if item["name"] or item["story"]:
            cases.append(item)
    out["cases"] = cases

    body_norm = re.sub(r"\s+", "", body)
    quotes = []
    for x in (data.get("quotes") or [])[:6]:
        x = one_line(x, 200)
        if x and re.sub(r"\s+", "", x) in body_norm:
            quotes.append(x)
    out["quotes"] = quotes[:5]

    learning = data.get("learning") or {}
    if not isinstance(learning, dict):
        learning = {}
    out["learning"] = {k: one_line(learning.get(k), 120) for _, k in LEARNING_KEYS}

    tags, seen = [], set()
    for t in (data.get("tags") or []):
        t = re.sub(r"^#+", "", one_line(t)).strip()
        t = re.sub(r"[，,。；;、|]+$", "", t)
        if 1 < len(t) <= 12 and t not in seen:
            seen.add(t)
            tags.append(t)
    out["tags"] = tags[:10]

    tk = one_line(data.get("topic_key"), 30) or "".join(out["tags"][:2]) or meta["title"][:15]
    out["topic_key"] = tk
    ct = one_line(data.get("content_type"), 10)
    out["content_type"] = ct if ct in CONTENT_TYPES else "观点"
    return out


def cell(s):
    return one_line(s).replace("|", "\\|") or "—"


def render_block(meta, data):
    lines = [AI_START, f"# {meta['title']}", "", f"> {data['summary']}", ""]
    lines += ["## 核心观点速览", ""]
    if data["key_points"]:
        lines += [f"- {p}" for p in data["key_points"]]
    else:
        lines.append("- （模型未提炼出明确观点）")

    lines += ["", "## 案例速查表", ""]
    if data["cases"]:
        lines += ["| 案例 | 一句话故事 | 支撑观点 | 可复用场景 |",
                  "| --- | --- | --- | --- |"]
        for c in data["cases"]:
            lines.append(f"| {cell(c['name'])} | {cell(c['story'])} | "
                         f"{cell(c['point'])} | {cell(c['reuse'])} |")
    else:
        lines.append("（本文无独立案例）")

    lines += ["", "## 金句摘录", ""]
    if data["quotes"]:
        for x in data["quotes"]:
            lines.append(f"> {x}")
    else:
        lines.append("（本文无适合逐字摘录的金句）")

    lines += ["", "## 学习记录", ""]
    for label, key in LEARNING_KEYS:
        val = data["learning"].get(key) or ("无明显争议" if key == "controversy" else "（无）")
        lines += [f"### {label}", "", val, ""]

    lines += ["## 标签", ""]
    lines.append(" · ".join(data["tags"]) if data["tags"] else "（无）")
    lines += ["", AI_END]
    return "\n".join(lines)


def update_fm(fm, data, model):
    fm = re.sub(r"^ai_(?:distilled|model|summary|topic_key|content_type):.*\n", "", fm, flags=re.M)
    fm = re.sub(r"^ai_tags:\n(?:[ \t]+-.*\n)*", "", fm, flags=re.M)
    add = [
        "ai_distilled: true",
        f"ai_model: {q(model)}",
        f"ai_summary: {q(data['summary'])}",
        f"ai_topic_key: {q(data['topic_key'])}",
        f"ai_content_type: {q(data['content_type'])}",
        "ai_tags:",
    ] + [f"  - {q(t)}" for t in data["tags"]]
    return fm.rstrip() + "\n" + "\n".join(add)


def compose(fm, block, head, body):
    parts = ["---", fm.strip(), "---", "", block]
    if head.strip():
        parts += ["", "---", "", head.strip()]
    parts += ["", "## 正文（原文）", "", body.strip(), ""]
    return "\n".join(parts)


def is_distilled(path):
    try:
        head = open(path, encoding="utf-8").read(6000)
    except Exception:
        return False
    return bool(re.search(r"^ai_distilled:\s*true\s*$", head, re.M))


def note_meta(path):
    text = open(path, encoding="utf-8").read()
    fm, after = split_note(text)
    meta = {
        "path": path,
        "stem": os.path.basename(path)[:-3],
        "title": fm_scalar(fm, "title") or os.path.basename(path)[:-3],
        "author": fm_author(fm),
        "published": fm_scalar(fm, "published"),
        "album": fm_scalar(fm, "album"),
        "description": fm_scalar(fm, "description"),
        "summary": fm_scalar(fm, "ai_summary"),
        "topic_key": fm_scalar(fm, "ai_topic_key"),
        "content_type": fm_scalar(fm, "ai_content_type"),
        "tags": fm_ai_tags(fm),
    }
    head, body = split_existing(after)
    meta["body"] = body
    return fm, after, meta


def prepare(path):
    """读取并清洗一篇笔记，返回可交给模型或 Claude 的任务包。"""
    text = open(path, encoding="utf-8").read()
    fm, after = split_note(text)
    if not fm:
        raise ValueError("没有 frontmatter，跳过")
    meta = note_meta(path)[2]
    head, body = split_existing(after)
    clean = clean_for_model(body)
    if len(clean) < 250:
        raise ValueError("正文过短（清洗后 <250 字）")
    return {
        "path": path, "fm": fm, "after": after, "meta": meta,
        "head": head, "body": body, "clean": clean,
    }


def commit(task, data, model_label):
    """把模型返回的结构化数据写回笔记。"""
    data = normalize_data(data, task["meta"], task["clean"])
    block = render_block(task["meta"], data)
    new_text = compose(update_fm(task["fm"], data, model_label),
                       block, task["head"], task["body"])
    atomic_write(task["path"], new_text)
    return data


def distill_one(path, model):
    task = prepare(path)
    system, user = SYSTEM_PROMPT, build_user(task["meta"], task["clean"])
    raw = call_ollama(model, system, user)
    try:
        data = parse_json(raw)
    except Exception:
        raw = call_ollama(model, system, user, temperature=0.0)
        data = parse_json(raw)
    return commit(task, data, model or "claude")


# ---------------------------------------------------------------- claude 档位：任务导出 / 回写
def plan_tasks(note_dir, paths, force=False):
    tasks, skipped = [], []
    for path in paths:
        if not force and is_distilled(path):
            skipped.append(path)
            continue
        try:
            task = prepare(path)
        except Exception as exc:  # noqa: BLE001
            skipped.append(path)
            continue
        tasks.append({
            "path": os.path.abspath(task["path"]),
            "title": task["meta"].get("title", ""),
            "author": task["meta"].get("author", ""),
            "url": fm_scalar(task["fm"], "source"),
            "body": task["clean"],
        })
    return tasks, skipped


USER_TEMPLATE_HINT = (
    "按 SYSTEM_PROMPT 的 JSON 结构输出，字段名固定。"
    "只输出一个 JSON 对象，不要 Markdown 代码块。"
)


def write_plan(note_dir, tasks, out_path):
    payload = {
        "schema": "tome-distill-plan/v1",
        "note_dir": os.path.abspath(note_dir),
        "prompt_system": SYSTEM_PROMPT,
        "prompt_user_template": USER_TEMPLATE_HINT,
        "tasks": tasks,
        "results_schema": {
            "path": "<与 tasks[].path 相同>",
            "data": "与 prompt_system 输出结构一致的对象",
        },
    }
    atomic_write(out_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return out_path


def apply_results(results_path, model_label, note_dir=None):
    payload = json.loads(open(results_path, encoding="utf-8").read())
    results = payload.get("results") or payload.get("tasks") or []
    stats = {"ok": 0, "failed": 0}
    failures = []
    for item in results:
        path = item.get("path")
        data = item.get("data")
        if not path or not isinstance(data, dict):
            stats["failed"] += 1
            failures.append((str(path), "缺少 path 或 data"))
            continue
        try:
            task = prepare(path)
            commit(task, data, model_label)
            stats["ok"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            failures.append((path, str(exc)))
    return stats, failures


# ---------------------------------------------------------------- 去重
def shingles(text, n=3, limit=6000):
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\s\W_]+", "", text)[:limit]
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


def norm_title(t):
    return re.sub(r"[\s\W_]+", "", t or "").lower()


def load_for_dedup(note_dir):
    items = []
    for p in sorted(glob.glob(os.path.join(note_dir, "*.md"))):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            fm, after, meta = note_meta(p)
        except Exception:
            continue
        head, body = split_existing(after)
        text = (meta.get("summary") or "") + "\n" + body[:5000]
        items.append({
            "path": p, "stem": meta["stem"], "title": meta["title"],
            "published": meta["published"], "topic_key": meta["topic_key"],
            "summary": meta.get("summary") or "", "body": body,
            "shingles": shingles(text),
        })
    return items


def candidate_pairs(items, threshold=0.35, cap=40):
    pairs = []
    for i in range(len(items)):
        a = items[i]
        for j in range(i + 1, len(items)):
            b = items[j]
            ts = difflib.SequenceMatcher(None, norm_title(a["title"]), norm_title(b["title"])).ratio()
            sa, sb = a["shingles"], b["shingles"]
            cs = len(sa & sb) / max(1, len(sa | sb))
            same_key = bool(a["topic_key"] and a["topic_key"] == b["topic_key"])
            if ts >= 0.72 or cs >= threshold or (same_key and cs >= 0.18):
                pairs.append((max(ts, cs), ts, cs, i, j, same_key))
    pairs.sort(reverse=True)
    return pairs[:cap]


def judge_pair(model, a, b):
    user = f"""文章A
标题：{a['title']}
摘要：{a['summary'] or '（无）'}
正文节选：{clean_for_model(a['body'], 3500)}

文章B
标题：{b['title']}
摘要：{b['summary'] or '（无）'}
正文节选：{clean_for_model(b['body'], 3500)}

判断两篇的关系，只输出 JSON：
{{"relation": "duplicate|series|related|different", "confidence": 0.0-1.0, "reason": "30字内理由"}}

判定标准（严格）：
- duplicate：核心内容基本相同（具体方法、数据、结论、案例高度重合），读一篇就不需要再读另一篇。
- series：同一系列/同一栏目的不同期、不同月份、不同版本（如月度热搜词、系列教程第1篇 vs 第3篇），主题延续但内容不同。
- related：主题相同，但方法、案例、数据或结论明显不同。
- different：主题或内容不同。
- 标题相似、开头套话相似都不算 duplicate；必须看正文核心信息。
- 同一作者也可能重复发文；判断只看正文核心内容是否重合，不看作者是否相同。"""
    raw = call_ollama(model,
                      "你是内容去重判断器。严格区分「内容重复」与「同主题系列」。只输出 JSON。",
                      user, timeout=300, temperature=0.0, num_predict=400)
    d = parse_json(raw)
    rel = one_line(d.get("relation")).lower()
    if rel not in ("duplicate", "series", "related", "different"):
        rel = "duplicate" if d.get("duplicate") else "related"
    return {
        "relation": rel,
        "duplicate": rel == "duplicate",
        "confidence": float(d.get("confidence") or 0),
        "reason": one_line(d.get("reason"), 80),
    }


def write_dedup(note_dir, items, pairs, model):
    confirmed, series, candidates = [], [], []
    for score, ts, cs, i, j, same_key in pairs:
        a, b = items[i], items[j]
        if len(a["body"]) >= len(b["body"]):
            keep, drop = a, b
        else:
            keep, drop = b, a
        try:
            verdict = judge_pair(model, a, b)
        except Exception as exc:
            verdict = {"relation": "related", "duplicate": False,
                       "confidence": 0, "reason": f"判断失败：{exc}"}
        row = {
            "keep": keep, "drop": drop, "score": score, "title_sim": ts,
            "content_sim": cs, "same_key": same_key, **verdict,
        }
        if verdict["relation"] == "duplicate" and verdict["confidence"] >= 0.7:
            confirmed.append(row)
        elif verdict["relation"] == "series":
            series.append(row)
        else:
            candidates.append(row)

    # 把逐对重复合并成簇，避免同一组文章在报告里重复出现多次
    clusters = []
    if confirmed:
        parent, members = {}, {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for r in confirmed:
            union(r["keep"]["stem"], r["drop"]["stem"])
            members[r["keep"]["stem"]] = r["keep"]
            members[r["drop"]["stem"]] = r["drop"]
        grouped = {}
        for stem, item in members.items():
            grouped.setdefault(find(stem), []).append(item)
        for group in grouped.values():
            group.sort(key=lambda x: len(x["body"]), reverse=True)
            clusters.append(group)
        clusters.sort(key=len, reverse=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# AI 去重报告", "",
             f"> 模型：{model} · 生成：{now}",
             f"> 检查 {len(items)} 篇；候选 {len(pairs)} 对；"
             f"确认重复 {len(confirmed)} 对；同主题系列 {len(series)} 对。",
             f"> 重复簇 {len(clusters)} 组，涉及 {sum(len(g) for g in clusters)} 篇。",
             "> 规则：每组建议保留正文最长的一篇；**只标记，不删除任何文件**。", ""]
    if clusters:
        lines += ["## 重复簇（建议每组只保留 1 篇）", "",
                  "| 建议保留 | 同组笔记 | 篇数 |", "| --- | --- | ---: |"]
        for group in clusters:
            keep = group[0]
            others = "<br>".join(note_ref(x["stem"]) for x in group[1:]) or "—"
            lines.append(f"| {note_ref(keep['stem'])} | {others} | {len(group)} |")
        lines += ["", "## 确认重复（逐对证据）", "",
                  "| 保留 | 疑似重复 | 相似度 | 模型理由 |", "| --- | --- | ---: | --- |"]
        for r in confirmed:
            lines.append(f"| {note_ref(r['keep']['stem'])} | {note_ref(r['drop']['stem'])} | "
                         f"{r['score']:.2f} | {cell(r['reason'])} |")
    else:
        lines.append("## 确认重复\n\n未发现内容高度重复的文章。")

    if series:
        lines += ["", "## 同主题系列（不视为重复）", "",
                  "| 较早/更长 | 同系列另一篇 | 相似度 | 模型理由 |",
                  "| --- | --- | ---: | --- |"]
        for r in series:
            lines.append(f"| {note_ref(r['keep']['stem'])} | {note_ref(r['drop']['stem'])} | "
                         f"{r['score']:.2f} | {cell(r['reason'])} |")

    if candidates:
        lines += ["", "## 主题相关但内容不同", "",
                  "| A | B | 相似度 | 模型理由 |", "| --- | --- | ---: | --- |"]
        for r in candidates:
            lines.append(f"| {note_ref(r['keep']['stem'])} | {note_ref(r['drop']['stem'])} | "
                         f"{r['score']:.2f} | {cell(r['reason'])} |")

    path = os.path.join(note_dir, "_dedup.md")
    atomic_write(path, "\n".join(lines).rstrip() + "\n")
    return path, confirmed, candidates


# ---------------------------------------------------------------- 报告 / 索引
def write_report(note_dir, model, stats):
    items = []
    for p in sorted(glob.glob(os.path.join(note_dir, "*.md"))):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            _, _, meta = note_meta(p)
        except Exception:
            continue
        items.append(meta)
    from collections import Counter
    types = Counter(m["content_type"] or "未分类" for m in items)
    tags = Counter(t for m in items for t in m["tags"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# AI 提炼报告", "",
             f"> 模型：{model} · 生成：{now}",
             f"> 笔记 {len(items)} 篇；本次提炼成功 {stats['ok']}，跳过 {stats['skipped']}，失败 {stats['failed']}。",
             "> 阅读卡片在每篇正文之前；原文保持不变。", "",
             "## 内容类型分布", "", "| 类型 | 数量 |", "| --- | ---: |"]
    for k, v in types.most_common():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 高频标签 Top 20", "", "| 标签 | 篇数 |", "| --- | ---: |"]
    for k, v in tags.most_common(20):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 去重", "", f"见 {note_ref('_dedup')}。只标记、不删除。", ""]
    path = os.path.join(note_dir, "_ai-report.md")
    atomic_write(path, "\n".join(lines))
    return path


def update_index(topic_dir, note_dir):
    idx = os.path.join(topic_dir, "index.md")
    block = "\n".join([
        REPORT_START,
        "## AI 提炼",
        "",
        f"- 阅读卡片与标签统计：{note_ref('_ai-report')}",
        f"- 去重报告：{note_ref('_dedup')}",
        REPORT_END,
    ])
    text = open(idx, encoding="utf-8").read() if os.path.exists(idx) else \
        f"# {os.path.basename(topic_dir)}\n"
    pat = re.compile(re.escape(REPORT_START) + r".*?" + re.escape(REPORT_END), re.S)
    if pat.search(text):
        text = pat.sub(block, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    atomic_write(idx, re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="给微信笔记生成阅读卡片 / 标签 / 去重")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic", help="专题名，处理 <专题>/00-收集")
    g.add_argument("--dir", help="直接指定笔记目录")
    ap.add_argument("--model", default=None, help="覆盖配置里的模型名")
    ap.add_argument("--limit", type=int, default=None, help="最多处理 N 篇（冒烟）")
    ap.add_argument("--force", action="store_true", help="重做已有 ai_distilled 的笔记")
    ap.add_argument("--no-dedup", action="store_true", help="只提炼，不去重")
    ap.add_argument("--dedup-only", action="store_true", help="只重新生成去重报告")
    ap.add_argument("--interval", type=float, default=0.3, help="篇间间隔秒（默认 0.3）")
    ap.add_argument("--plan", action="store_true",
                    help="claude 档位：导出待提炼任务到 _distill-plan.json")
    ap.add_argument("--apply", metavar="RESULTS",
                    help="把 Claude 写好的结果 JSON 回写到笔记")
    args = ap.parse_args()

    if args.dir:
        note_dir = args.dir
    else:
        note_dir = os.path.join(str(tc.project_dir(args.topic)), "00-收集")
    topic_dir = os.path.dirname(note_dir)
    model = args.model or tc.model_name()

    if args.apply:
        stats, failures = apply_results(args.apply, "claude")
        if stats["ok"]:
            write_report(note_dir, "claude", {**stats, "skipped": 0})
            update_index(topic_dir, note_dir)
        print(f"[回写] 成功 {stats['ok']}，失败 {stats['failed']}")
        for path, err in failures:
            print(f"       ✗ {path} — {err}", file=sys.stderr)
        return 0 if not stats["failed"] else 3

    paths = [p for p in sorted(glob.glob(os.path.join(note_dir, "*.md")))
             if not os.path.basename(p).startswith("_")]
    if not paths:
        print(f"[错误] 没有笔记：{note_dir}", file=sys.stderr)
        return 1

    if tc.provider() == "claude":
        if args.plan:
            tasks, skipped = plan_tasks(note_dir, paths, force=args.force)
            if args.limit is not None:
                tasks = tasks[:max(0, args.limit)]
            out = os.path.join(note_dir, "_distill-plan.json")
            write_plan(note_dir, tasks, out)
            print(f"[任务] {len(tasks)} 篇 → {out}")
            print(f"       跳过 {len(skipped)} 篇（已提炼 / 正文过短 / 无 frontmatter）")
            print("       由 Claude 读取每个 task 的 prompt_system + body，"
                  "把结果写进 results 数组后 --apply。")
            return 0
        print(tc.CLAUDE_HINT, file=sys.stderr)
        print("\n或导出任务：--plan", file=sys.stderr)
        return 5

    try:
        models = ollama_models()
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    if tc.provider() == "ollama" and model not in models:
        print(f"[错误] 模型 {model} 不在 Ollama 里。已有：{', '.join(models)}",
              file=sys.stderr)
        return 2

    stats = {"ok": 0, "skipped": 0, "failed": 0}
    failures = []

    if not args.dedup_only:
        todo = [p for p in paths if args.force or not is_distilled(p)]
        stats["skipped"] = len(paths) - len(todo)
        if args.limit is not None:
            todo = todo[:max(0, args.limit)]
        if todo:
            backup = os.path.join(str(tc.backup_dir()),
                                  "wechat-distill-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            os.makedirs(backup, exist_ok=True)
            for p in todo:
                shutil.copy2(p, os.path.join(backup, os.path.basename(p)))
            print(f"[备份] {len(todo)} 篇 → {backup}", flush=True)

        print(f"[提炼] 待处理 {len(todo)} 篇，跳过 {stats['skipped']} 篇，模型 {model}",
              flush=True)
        try:
            for n, p in enumerate(todo, 1):
                title = note_meta(p)[2]["title"]
                t0 = time.time()
                try:
                    data = distill_one(p, model)
                    stats["ok"] += 1
                    print(f"[{n}/{len(todo)}] ✓ {title} "
                          f"· {data['content_type']} · {len(data['tags'])} 标签 "
                          f"({time.time() - t0:.1f}s)", flush=True)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    stats["failed"] += 1
                    failures.append((p, str(exc)))
                    print(f"[{n}/{len(todo)}] ✗ {title} — {exc}", file=sys.stderr, flush=True)
                if n < len(todo):
                    time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[中断] 已完成的笔记保留，重跑会跳过。", file=sys.stderr)
            write_report(note_dir, model, stats)
            return 130

    dedup_path = None
    if not args.no_dedup:
        items = load_for_dedup(note_dir)
        pairs = candidate_pairs(items)
        print(f"[去重] {len(items)} 篇，候选 {len(pairs)} 对，逐对确认…", flush=True)
        try:
            dedup_path, confirmed, _ = write_dedup(note_dir, items, pairs, model)
            print(f"[去重] 确认 {len(confirmed)} 对 → {dedup_path}", flush=True)
        except KeyboardInterrupt:
            print("\n[中断] 去重未完成。", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"[去重失败] {exc}", file=sys.stderr)
            stats["failed"] += 1

    report_path = write_report(note_dir, model, stats)
    update_index(topic_dir, note_dir)
    print(f"\n[总结] 提炼成功 {stats['ok']}，跳过 {stats['skipped']}，失败 {stats['failed']}")
    print(f"       报告：{report_path}")
    if dedup_path:
        print(f"       去重：{dedup_path}")
    for p, err in failures:
        print(f"       ✗ {os.path.basename(p)} — {err}", file=sys.stderr)

    if stats["failed"]:
        return 3 if (stats["ok"] or stats["skipped"]) else 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
