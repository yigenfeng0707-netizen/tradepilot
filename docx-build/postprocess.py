# -*- coding: utf-8 -*-
"""WPS 兼容后处理：
1) 删除封面节的空 <w:pgNumType/>
2) 按节顺序定位页脚文件：第 2 节（目录）补 \\* ROMAN，第 3 节（正文）补 \\* arabic
"""
import re, shutil, sys, zipfile

DOCX = sys.argv[1]
TMP = DOCX + ".tmp"

with zipfile.ZipFile(DOCX, "r") as zin:
    names = zin.namelist()
    data = {n: zin.read(n) for n in names}

doc = data["word/document.xml"].decode("utf-8")

# 1) 删除空 pgNumType（docx-js 对未设置页码的节也会输出）
before = doc.count("<w:pgNumType/>")
doc = doc.replace("<w:pgNumType/>", "")

# 2) 按文档顺序取每个 sectPr 的 footerReference r:id
sect_footers = []  # [(sect_index, rid), ...]
for si, sect in enumerate(re.findall(r"<w:sectPr\b.*?</w:sectPr>", doc, re.S)):
    m = re.search(r'<w:footerReference[^>]*w:type="default"[^>]*r:id="([^"]+)"', sect)
    if not m:
        m = re.search(r'<w:footerReference[^>]*r:id="([^"]+)"', sect)
    sect_footers.append((si, m.group(1) if m else None))

rels = data["word/_rels/document.xml.rels"].decode("utf-8")
rid_to_target = dict(re.findall(r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels))

def patch_footer(fname, fmt):
    key = "word/" + fname
    xml = data[key].decode("utf-8")
    xml2, n = re.subn(
        r"(<w:instrText[^>]*>)([^<]*PAGE[^<]*)(</w:instrText>)",
        r"\g<1> PAGE \\* " + fmt + r" \\* MERGEFORMAT \g<3>",
        xml,
    )
    data[key] = xml2.encode("utf-8")
    return n

# 节顺序：0=封面(无页脚) 1=目录(ROMAN) 2=正文(arabic)
plan = {1: "ROMAN", 2: "arabic"}
report = []
for si, rid in sect_footers:
    if rid and si in plan:
        target = rid_to_target.get(rid, "")
        if target.startswith("footer"):
            n = patch_footer(target, plan[si])
            report.append(f"sect{si} -> {target} [{plan[si]}] patched={n}")

data["word/document.xml"] = doc.encode("utf-8")

with zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED) as zout:
    for n in names:
        zout.writestr(n, data[n])
shutil.move(TMP, DOCX)
print(f"removed empty pgNumType: {before}; footers: {report}")
