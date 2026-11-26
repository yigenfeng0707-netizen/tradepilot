"""QA：程序化检查新 Deck——文字溢出（估算）与文本框越界、图片完整性、图表数。

口径按 pptx 技能 QA 节：估算行高 vs shape 高度、shape 是否越出画布、
两个文本框是否重叠；图片在 13.33x7.5 画布内。
"""
import sys

from pptx import Presentation

sys.stdout.reconfigure(encoding="utf-8")

PATH = "D:/电商/OPC报名材料_四件套/路演Deck_TradePilot_OPC海南城市赛.pptx"
SLIDE_W, SLIDE_H = 13.333, 7.5

prs = Presentation(PATH)
print(f"页数：{len(prs.slides)}  画布：{prs.slide_width/914400:.2f} x {prs.slide_height/914400:.2f} in")

issues = []
pics = charts = 0
for i, slide in enumerate(prs.slides, 1):
    boxes = []
    for sh in slide.shapes:
        x, y = sh.left / 914400, sh.top / 914400
        w, h = sh.width / 914400, sh.height / 914400
        # 越界检查
        if x < -0.05 or y < -0.05 or x + w > SLIDE_W + 0.05 or y + h > SLIDE_H + 0.05:
            issues.append(f"S{i}: 越界 ({x:.2f},{y:.2f},{w:.2f},{h:.2f}) name={sh.shape_type}")
        if sh.shape_type == 13:  # picture
            pics += 1
        if getattr(sh, "has_chart", False):
            charts += 1
        # 文本溢出估算
        if sh.has_text_frame and sh.text_frame.text.strip():
            text = sh.text_frame.text
            # 估算：CJK 1.0em、拉丁 0.55em；估行数与行高
            sizes = [r.font.size.pt for p in sh.text_frame.paragraphs for r in p.runs if r.font.size]
            fs = max(sizes) if sizes else 18
            width_in = sum(1 if ord(c) > 0x2000 else 0.55 for c in text) * fs / 72
            lines = max(1, -(-int(width_in / max(0.1, w)) // 1)) if w > 0.1 else 99
            need_h = lines * fs * 1.25 / 72  # 英寸
            if need_h > h * 1.18:
                issues.append(f"S{i}: 疑似文字溢出 need={need_h:.2f}in > box={h:.2f}in "
                              f"fs={fs} text={text[:28]!r}")
            boxes.append((x, y, w, h, text[:20]))
    # 文本框两两重叠（忽略 title/kicker 同列）
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            x1, y1, w1, h1, t1 = boxes[a]
            x2, y2, w2, h2, t2 = boxes[b]
            ox = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
            oy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
            if ox > 0.15 and oy > 0.15 and t1 != t2:
                issues.append(f"S{i}: 重叠 {t1!r} × {t2!r} ({ox:.2f}x{oy:.2f}in)")

print(f"图片：{pics} 张  原生图表：{charts} 个")
if issues:
    print(f"\n⚠ {len(issues)} 处问题：")
    for it in issues:
        print(" -", it)
else:
    print("\n✅ 无越界/溢出/重叠问题")
