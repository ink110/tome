#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 epub：结构、XML、链接、表格、封面、扉页、篇章页。

用法:
  python3 validate.py "/path/book.epub"
"""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET


def fail(errors, msg):
    errors.append(msg)
    print(f"❌ {msg}")


def ok(msg):
    print(f"✅ {msg}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法: python3 validate.py book.epub")
    path = os.path.expanduser(sys.argv[1])
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}")

    errors = []
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if not infos or infos[0].filename != "mimetype":
            fail(errors, "mimetype 不是第一个文件")
        elif infos[0].compress_type != zipfile.ZIP_STORED:
            fail(errors, "mimetype 不是存储（stored）")
        else:
            mt = z.read("mimetype").decode("utf-8", "replace")
            if mt.strip() != "application/epub+zip":
                fail(errors, f"mimetype 内容错误：{mt!r}")
            else:
                ok("mimetype 正确（第一个、stored）")

        # container
        try:
            container = z.read("META-INF/container.xml").decode("utf-8")
        except KeyError:
            fail(errors, "缺少 META-INF/container.xml")
            container = ""
        opf_path = None
        if container:
            m = re.search(r'full-path="([^"]+)"', container)
            if m:
                opf_path = m.group(1)
                if opf_path not in names:
                    fail(errors, f"OPF 不存在：{opf_path}")
            else:
                fail(errors, "container.xml 里没有 full-path")

        if not opf_path or opf_path not in names:
            print("\n结论：结构不完整，无法继续。")
            return 1

        opf = z.read(opf_path).decode("utf-8")
        try:
            ET.fromstring(opf)
            ok("OPF XML 可解析")
        except ET.ParseError as e:
            fail(errors, f"OPF XML 解析失败：{e}")

        # manifest / spine
        items = {m.group(1): m.group(2) for m in
                 re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf)}
        items2 = {m.group(2): m.group(1) for m in
                  re.finditer(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)}
        items.update({v: k for k, v in items2.items()})
        opf_dir = os.path.dirname(opf_path)
        missing = []
        for iid, href in items.items():
            full = os.path.normpath(os.path.join(opf_dir, href))
            if full not in names:
                missing.append(f"{iid} -> {href}")
        if missing:
            fail(errors, f"manifest 里有 {len(missing)} 个文件不存在：{missing[:5]}")
        else:
            ok(f"manifest 文件齐全（{len(items)} 项）")

        spine = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
        bad_spine = [x for x in spine if x not in items]
        if bad_spine:
            fail(errors, f"spine 引用了不存在的 id：{bad_spine}")
        else:
            ok(f"spine 引用有效（{len(spine)} 项）")

        if "cover_xhtml" in items:
            if spine and spine[0] != "cover_xhtml":
                fail(errors, "spine 第一项不是封面")
            else:
                ok("封面在 spine 第一位")
        else:
            fail(errors, "manifest 里没有 cover_xhtml")

        if "title_page_xhtml" in items:
            if "title_page_xhtml" not in spine:
                fail(errors, "扉页不在 spine 里")
            else:
                ok("扉页在 spine 里")
        else:
            fail(errors, "manifest 里没有 title_page_xhtml")

        # cover image
        m = re.search(r'<item\b[^>]*properties="cover-image"[^>]*href="([^"]+)"', opf)
        if not m:
            m = re.search(r'<item\b[^>]*href="([^"]+)"[^>]*properties="cover-image"', opf)
        if m:
            full = os.path.normpath(os.path.join(opf_dir, m.group(1)))
            if full not in names:
                fail(errors, f"封面图不存在：{m.group(1)}")
            else:
                ok(f"封面图存在（{m.group(1)}）")
        else:
            fail(errors, "OPF 里没有 cover-image")

        # XML parse all xhtml
        xhtml = [n for n in names if n.endswith(".xhtml") or n.endswith(".ncx") or n.endswith(".opf")]
        parse_errors = []
        for n in xhtml:
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                parse_errors.append(f"{n}: {e}")
        if parse_errors:
            fail(errors, f"{len(parse_errors)} 个 XML 解析失败：{parse_errors[:3]}")
        else:
            ok(f"全部 XML 可解析（{len(xhtml)} 个）")

        # ids per xhtml
        ids = {}
        for n in names:
            if n.endswith(".xhtml"):
                s = z.read(n).decode("utf-8", "replace")
                ids[n] = set(re.findall(r'\bid="([^"]+)"', s))

        # nav links（相对于 OPF 所在目录）
        nav_path = os.path.normpath(os.path.join(opf_dir, "nav.xhtml"))
        ncx_path = os.path.normpath(os.path.join(opf_dir, "toc.ncx"))
        nav_links = []
        if nav_path in names:
            nav = z.read(nav_path).decode("utf-8")
            nav_links = re.findall(r'<a\b[^>]*href="([^"]+)"', nav)
            ok(f"nav.xhtml 存在（{len(nav_links)} 个链接）")
        else:
            fail(errors, f"缺少 {nav_path}")

        # ncx links
        ncx_links = []
        if ncx_path in names:
            ncx = z.read(ncx_path).decode("utf-8")
            ncx_links = re.findall(r'<content\b[^>]*src="([^"]+)"', ncx)
            ok(f"toc.ncx 存在（{len(ncx_links)} 个链接）")
        else:
            fail(errors, f"缺少 {ncx_path}")

        broken = []
        for base, links in ((nav_path, nav_links), (ncx_path, ncx_links)):
            for href in links:
                if href.startswith("http://") or href.startswith("https://"):
                    continue
                filepart, _, anchor = href.partition("#")
                target = os.path.normpath(os.path.join(os.path.dirname(base), filepart)) if filepart else base
                if target not in names:
                    broken.append(f"{base} -> {href}（文件不存在）")
                    continue
                if anchor and anchor not in ids.get(target, set()):
                    broken.append(f"{base} -> {href}（锚点不存在）")
        if broken:
            fail(errors, f"{len(broken)} 个目录链接失效：{broken[:5]}")
        else:
            ok("目录链接全部有效")

        # in-book anchors
        broken2 = []
        for n in names:
            if not n.endswith(".xhtml"):
                continue
            s = z.read(n).decode("utf-8", "replace")
            for href in re.findall(r'<a\b[^>]*href="(#[^"]+)"', s):
                anchor = href[1:]
                if anchor not in ids.get(n, set()):
                    broken2.append(f"{n} -> {href}")
        if broken2:
            fail(errors, f"{len(broken2)} 个书内锚点失效：{broken2[:5]}")
        else:
            ok("书内锚点全部有效")

        # chapters
        chapters = sorted(n for n in names if re.match(r"^EPUB/text/ch\d+\.xhtml$", n))
        no_title = []
        tables = []
        part_pages = 0
        for n in chapters:
            s = z.read(n).decode("utf-8", "replace")
            t = re.search(r"<title>(.*?)</title>", s, re.S)
            if not t or not t.group(1).strip():
                no_title.append(n)
            if "<table" in s:
                tables.append(n)
            if 'class="level1 part-page"' in s:
                part_pages += 1
        if no_title:
            fail(errors, f"{len(no_title)} 章没有 title：{no_title[:5]}")
        else:
            ok(f"{len(chapters)} 章都有 title")
        if tables:
            fail(errors, f"{len(tables)} 章仍有表格：{tables[:5]}")
        else:
            ok("所有表格已转成卡片/定义列表")
        if part_pages:
            ok(f"篇章页 {part_pages} 个")
        else:
            print("⚠️ 没有篇章页（如果这本书不需要分篇，可忽略）")

        # title page / cover content
        for n in ("EPUB/text/title_page.xhtml", "EPUB/text/cover.xhtml"):
            if n not in names:
                fail(errors, f"缺少 {n}")
        if "EPUB/text/cover.xhtml" in names:
            s = z.read("EPUB/text/cover.xhtml").decode("utf-8", "replace")
            if "<img" not in s:
                fail(errors, "cover.xhtml 里没有 <img>")
            else:
                ok("封面页使用 <img>")
        if "EPUB/text/title_page.xhtml" in names:
            s = z.read("EPUB/text/title_page.xhtml").decode("utf-8", "replace")
            if "book-title" not in s:
                fail(errors, "扉页缺少书名")
            else:
                ok("扉页结构正确")

        size = os.path.getsize(path) / 1024 / 1024
        print(f"\n文件：{path}")
        print(f"大小：{size:.2f} MB｜章节：{len(chapters)}｜目录链接：{len(nav_links)}")

    if errors:
        print(f"\n结论：{len(errors)} 个问题，见上。")
        return 1
    print("\n结论：全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
