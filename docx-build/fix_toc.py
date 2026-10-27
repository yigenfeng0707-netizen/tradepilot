# -*- coding: utf-8 -*-
"""重建 TOC 域缓存内容：
- 从渲染 PDF 实测每个标题的正文页码（正文节阿拉伯页码 = PDF 页 - 3）
- 整体替换 <w:sdtContent> 内的段落：首条携带 TOC begin/instr/separate，末条携带 end
- 条目 = 标题 + 右对齐点线制表位 + 实测页码；保留 w:dirty 令 Word 打开时自动刷新
"""
import json, re, shutil, zipfile
import pypdfium2 as pdfium

DOCX = "D:/Apps/电商/电商Agent安全评审Checklist.docx"
PDF = "D:/Apps/电商/docx-build/电商Agent安全评审Checklist.pdf"
FRONT_PAGES = 3  # 封面 1 页 + 目录 2 页

def norm(s):
    return re.sub(r"\s+", "", s)

# ── 1. 收集标题（与 generate.js 同序）─────────────────────
content = json.load(open("D:/Apps/电商/docx-build/content.json", encoding="utf-8"))
headings = []
for ch in content["chapters"]:
    headings.append((1, ch["title"]))
    for b in ch["blocks"]:
        if b["type"] == "h2":
            headings.append((2, b["text"]))

# ── 2. 从 PDF 实测页码 ────────────────────────────────────
pdf = pdfium.PdfDocument(PDF)
page_texts = [norm(p.get_textpage().get_text_range()) for p in pdf]
body_start = FRONT_PAGES  # 0-based：正文从第 4 页起
entries, cursor = [], body_start
for level, title in headings:
    key = norm(title)
    found = None
    for pi in range(cursor, len(pdf)):
        if key in page_texts[pi]:
            found = pi
            break
    if found is None:
        raise SystemExit(f"heading not found in PDF: {title}")
    cursor = found  # 单调推进（同页可有多个标题）
    entries.append((level, title, found + 1 - FRONT_PAGES))
print(f"measured {len(entries)} headings; body pages {entries[0][2]}..{entries[-1][2]}")

# ── 3. 重建 sdtContent ───────────────────────────────────
RPR = '<w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="SimSun"/><w:color w:val="000000"/><w:sz w:val="24"/></w:rPr>'

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def entry_xml(level, title, page, first, last):
    style = "TOC1" if level == 1 else "TOC2"
    ind = '<w:ind w:left="360"/>' if level == 2 else ""
    p = ['<w:p><w:pPr><w:pStyle w:val="%s"/>%s' % (style, ind)]
    p.append('<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8788"/></w:tabs>')
    p.append('<w:spacing w:before="60" w:after="40"/></w:pPr>')
    if first:
        p.append('<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>')
        p.append('<w:r><w:instrText xml:space="preserve">TOC \\h \\o "1-2"</w:instrText></w:r>')
        p.append('<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
    p.append("<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (RPR, esc(title)))
    p.append("<w:r>%s<w:tab/></w:r>" % RPR)
    p.append("<w:r>%s<w:t>%d</w:t></w:r>" % (RPR, page))
    if last:
        p.append('<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    p.append("</w:p>")
    return "".join(p)

new_inner = "".join(
    entry_xml(lv, t, pg, i == 0, i == len(entries) - 1)
    for i, (lv, t, pg) in enumerate(entries)
)

with zipfile.ZipFile(DOCX, "r") as zin:
    names = zin.namelist()
    data = {n: zin.read(n) for n in names}
doc = data["word/document.xml"].decode("utf-8")

a = doc.find("<w:sdtContent>")
b = doc.find("</w:sdtContent>")
assert a != -1 and b != -1 and doc.count("<w:sdtContent>") == 1, "sdtContent not unique"
a += len("<w:sdtContent>")
doc = doc[:a] + new_inner + doc[b:]
data["word/document.xml"] = doc.encode("utf-8")

TMP = DOCX + ".tmp"
with zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED) as zout:
    for n in names:
        zout.writestr(n, data[n])
shutil.move(TMP, DOCX)
print("TOC rebuilt: 36 clean entries with measured page numbers")
