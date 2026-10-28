# -*- coding: utf-8 -*-
"""TradePilot BP PDF 生成脚本（按通用 BP 评分维度组织，无本地截图依赖）。"""
import os
import sys

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
if not os.path.exists(FONT_PATH):
    FONT_PATH = r"C:\Windows\Fonts\simhei.ttf"
pdfmetrics.registerFont(TTFont("Chinese", FONT_PATH))

PRIMARY = HexColor("#102A56")
ACCENT = HexColor("#2D6CDF")
GREY = HexColor("#5A6B7C")
LIGHT = HexColor("#F2F5F9")
WHITE = HexColor("#FFFFFF")

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=styles["Title"], fontName="Chinese", fontSize=22,
                       textColor=PRIMARY, spaceAfter=6)
SUB_ST = ParagraphStyle("sub", parent=styles["Normal"], fontName="Chinese", fontSize=10,
                        textColor=GREY, spaceAfter=18)
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Chinese", fontSize=15,
                    textColor=PRIMARY, spaceBefore=14, spaceAfter=8)
BODY = ParagraphStyle("body", parent=styles["Normal"], fontName="Chinese", fontSize=10,
                      leading=16, spaceAfter=6)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=10, bulletIndent=2, spaceAfter=3)
QUOTE = ParagraphStyle("quote", parent=BODY, fontSize=9, textColor=GREY,
                       leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=10)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9, spaceAfter=0)
CELL_H = ParagraphStyle("cell_h", parent=CELL, textColor=WHITE, fontSize=9.5)


def heading(text):
    return [Paragraph(text, H1), Spacer(1, 2 * mm)]


def para(text):
    return [Paragraph(text, BODY)]


def bullets(items):
    return [Paragraph("• " + item, BULLET) for item in items]


def make_table(header, rows, col_widths):
    data = [[Paragraph(h, CELL_H) for h in header]]
    for row in rows:
        data.append([Paragraph(c, CELL) for c in row])
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.25, HexColor("#D0D8E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def kv_table(rows):
    data = [[Paragraph(k, CELL), Paragraph(v, CELL)] for k, v in rows]
    t = Table(data, colWidths=[48 * mm, 118 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("TEXTCOLOR", (0, 0), (0, -1), PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.25, HexColor("#D0D8E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_bp(output):
    doc = SimpleDocTemplate(
        output, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="TradePilot 商业计划书", author="TradePilot 团队",
    )
    E = []

    # 封面
    E.append(Spacer(1, 35 * mm))
    E.append(Paragraph("TradePilot · 跨境领航员", TITLE))
    E.append(Paragraph("跨境电商全链路智能运营 OPC 智能体", SUB_ST))
    E.append(Paragraph("第二届全国人工智能应用创新大赛 · 通用赛道（AI Agent 赋能千行百业 · 出海）", SUB_ST))
    E.append(Spacer(1, 12 * mm))
    E.append(kv_table([
        ("项目定位", "给一人公司（OPC）用的跨境电商 AI 运营部"),
        ("核心能力", "真实市场扫描 → 现场汇率折算 → 定价沙盘 → 合规体检 → LLM Listing"),
        ("信任底座", "多源真实抓取 + 来源闸门 + 存证可溯源 + 诚实口径（可复算）"),
        ("实测验证", "3 品类 × 2 轮 × 5 步全成功；云端魔搭部署 5/5 步端到端"),
    ]))
    E.append(Spacer(1, 5 * mm))
    E.append(Paragraph("日期：2026-10-28　·　版本：v1.0", SUB_ST))
    E.append(PageBreak())

    # 一、项目概述
    E += heading("一、项目概述")
    E += para("中小跨境电商卖家（尤其一人公司、OPC）的出海前置调研是重人工、慢流程的：打开多个市场站点比价、查汇率、算成本、查合规、写 Listing，一个新品从选品到准备上架往往要花 2–4 人天。TradePilot 用 AI 智能体把这一链路压缩为「下达一个目标，系统自主完成 5 步全链路」。")
    keywords = "、".join(["多源真实抓取", "来源闸门防幻觉", "现场汇率", "定价沙盘", "合规体检",
                          "LLM 规划", "岸基运营大屏", "存证可溯源"])
    E += para("关键词：" + keywords)
    E.append(Paragraph("一句话：真实数据打底，Agent 自主决策，人只看结果。", QUOTE))

    # 二、问题与痛点
    E += heading("二、问题与痛点")
    E += bullets([
        "数据源分散：电商、汇率、合规各自孤立，卖家需反复切换站点；",
        "价格幻觉风险：AI 常被要求「看起来专业」而编造价格，真实抓取与来源标注是底线；",
        "汇率口径混乱：内嵌经验汇率无法复算，现场汇率必须可溯源；",
        "合规靠经验：CPSC/FDA/FCC 等官方来源不熟，引用标注与现场验证难保证；",
        "文案与事实脱节：Listing 生成容易编造参数，缺乏事实回填约束。",
    ])

    # 三、解决方案
    E += heading("三、解决方案")
    E += para("TradePilot 采用「统一执行器 + 工具白名单 + 来源闸门」的单 agent 模式，每个任务 5 张决策卡，全程留痕：")
    E.append(make_table(
        ["决策", "工具", "产出（实测）"],
        [
            ["1", "market_scan", "真实抓取 8 条商品，价格带 ¥36.70–259.00 CNY，逐条带来源存证"],
            ["2", "fx_rate", "现场 USD/CNY = 6.6675（欧洲央行来源，存证）"],
            ["3", "pricing_sandbox", "到岸 $23.84 → 报价下限 $36.68，5 档净利率敏感性"],
            ["4", "compliance_check", "品类合规清单，官方来源现场验证 + 不可达如实标注"],
            ["5", "listing_draft", "英文标题 + 卖点，事实字段全部回填自存证数据点"],
        ],
        [18 * mm, 38 * mm, 122 * mm],
    ))
    E.append(Spacer(1, 3 * mm))

    # 四、核心产品与技术
    E += heading("四、核心产品与技术")
    E += para("TradePilot 不是一个大模型 App，而是「工具执行器 + 来源存证 + LLM 规划」的确定性系统：")
    E += bullets([
        "统一执行器：所有工具走同一入口，异常永不抛出（failure ladder），保证回合不中断；",
        "来源闸门：定价/Listing 只消费存证在册的数据点，编造的 id 一律 held 拦截；",
        "双通道抓取：httpx 快路径 + Playwright 浏览器强路径，按域名策略与限速保护；",
        "多源适配：当当（国内 CNY 参考）、Walmart（美国站 USD 竞品，部署态启用）、Frankfurter 汇率 API；",
        "LLM 规划 + 规则兜底：LLM 可用时自主规划，不可用时固定 pipeline 照常闭环；",
        "反幻觉信任状：每个数字标注来源 URL + 时间戳，页面数值服务端回填。",
    ])
    E.append(Spacer(1, 2 * mm))
    E.append(kv_table([
        ("代码规模", "约 1000 行（app/ + web/ + scripts/）"),
        ("来源存证", "SQLite 持久化，页面可点溯源"),
        ("岸基大屏", "原生 Canvas，无构建依赖，决策时间线实时流"),
    ]))

    # 五、数据验证
    E += heading("五、数据验证（可复算）")
    E += para("实测指标来自 assets/eval_results.json，可复算：")
    E.append(make_table(
        ["品类", "结果"],
        [
            ["pet/dog bed", "5/5 步 · 8 商品 · 合规 3/4 · LLM 规划 90% · 平均 219.9s"],
            ["home/wall clock", "5/5 步 · 8 商品 · 合规 3/4 · LLM 规划 100% · 平均 148.6s"],
            ["electronics/usb c cable", "5/5 步 · 8 商品 · 合规 2/4 · LLM 规划 100% · 平均 201.4s"],
        ],
        [48 * mm, 118 * mm],
    ))
    E.append(Spacer(1, 2 * mm))
    E += bullets([
        "全部 6 轮 5/5 步成功，步骤成功率 100%；",
        "事实抽检：assets/audit_sample.json 30 条存证样本供人工回访原页核对；",
        "来源可溯率：每条价格带来源 URL + 抓取时间戳，Agent 侧 100%（人工通常不保证）；",
        "部署态：魔搭创空间 fygrobert-tradepilot.ms.show，云端 WebSocket 端到端 5/5 步跑通。",
    ])

    # 六、市场与竞争
    E += heading("六、市场与竞争分析")
    E += para("目标市场：跨境电商中小卖家出海调研前置市场。公开资料显示，仅速卖通 + Amazon 美国站的类目定价与合规信息分散在官方规则站、海关 HTS、各品类测评等多源；卖家从选品到定价至少 2–4 人天。")
    E += bullets([
        "竞品一（大模型工具）：输出快但易幻觉，缺少来源闸门与存证；",
        "竞品二（ERP 插件）：绑定单一平台，多源适配差，无 LLM 规划层；",
        "竞品三（人工咨询）：质量高但成本高、无法规模化；",
        "TradePilot 差异：真实数据 + 来源闸门 + 可复算定价 + 全链路留痕。",
    ])

    # 七、商业模式
    E += heading("七、商业模式")
    E += bullets([
        "SaaS 订阅：基础版 ¥199/月（50 次任务），专业版 ¥599/月（500 次 + API）；",
        "Agent 定制：按品类深度定制合规规则与数据源适配，一次性部署费 + 年费；",
        "数据增值：沉淀的真实市场数据经脱敏后提供行业洞察报告（订阅制）。",
    ])

    # 八、团队
    E += heading("八、团队")
    E.append(kv_table([
        ("项目负责人", "【待填写】"),
        ("技术负责人", "【待填写】"),
        ("指导教师", "【待填写】"),
    ]))

    # 九、落地计划
    E += heading("九、落地计划")
    E.append(make_table(
        ["阶段", "内容", "时间"],
        [
            ["W1-W2", "后端引擎 + 3 品类实测 + LLM 网关", "10 月下旬"],
            ["W3", "魔搭创空间部署 + 云端 5/5 验证", "10 月 28 日（已完成）"],
            ["W4", "人工基线 SOP 计时 + 事实抽检回访", "11 月上旬"],
            ["赛后", "品类扩展 + Walmart 美国站实测（云上自动重验）", "12 月"],
        ],
        [22 * mm, 110 * mm, 44 * mm],
    ))

    E.append(Spacer(1, 8 * mm))
    E.append(Paragraph("附：作品链接 https://fygrobert-tradepilot.ms.show", QUOTE))

    doc.build(E)
    print("BP 已生成:", output)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else r"D:\Apps\电商\tradepilot\docs\TradePilot_BP.pdf"
    build_bp(out)
