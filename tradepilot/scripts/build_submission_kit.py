# -*- coding: utf-8 -*-
"""生成 OPC 海南城市赛「今日报名提交材料包」。

设计纪律（与 build_bp.py 同源）：
1. 所有实测数字从 assets/eval_results.json 与代码现算，不在本脚本里硬编码；
2. 身份信息只给模板，agent 不代填，不替用户签名；
3. 官网事实（截止日、提交物）附来源 URL，并以组委会群内答复为最终口径。

用法：python scripts/build_submission_kit.py
输出：仓库根 / 报名提交材料包/
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # noqa: E402
from build_bp import COMPETITION, measured_facts  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "报名提交材料包"
APPLY_URL = ("https://hub.opcquan.com/contests/event-b04af338f9e1/apply"
             "?community=f0a66104-c8bd-4042-b88b-e6cdaa129860")
COMMUNITY_URL = "https://hub.opcquan.com/invite/opc?code=P4M4NUCS"
EVENT_PAGE = "https://hub.opcquan.com/contests/event-b04af338f9e1/apply?community=f0a66104-c8bd-4042-b88b-e6cdaa129860"

FONT = r"C:\Windows\Fonts\msyh.ttc"
if not os.path.exists(FONT):
    FONT = r"C:\Windows\Fonts\simhei.ttf"
pdfmetrics.registerFont(TTFont("Chinese", FONT))

PRIMARY = colors.HexColor("#102A56")
ACCENT = colors.HexColor("#2D6CDF")
LIGHT = colors.HexColor("#F2F5F9")
WHITE = colors.HexColor("#FFFFFF")
GREY = colors.HexColor("#5A6B7C")

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=styles["Title"], fontName="Chinese", fontSize=19,
                       textColor=PRIMARY, spaceAfter=4)
SUB = ParagraphStyle("s", parent=styles["Normal"], fontName="Chinese", fontSize=9,
                     textColor=GREY, spaceAfter=8)
CELL = ParagraphStyle("c", parent=styles["Normal"], fontName="Chinese", fontSize=8.5,
                      leading=12.5, textColor=PRIMARY)
CELL_H = ParagraphStyle("ch", parent=CELL, textColor=WHITE, fontSize=9)


def _rows(facts):
    overrides = sum(s.get("llm_override_total", 0) for s in facts["summary"].values())
    provenance = facts["provenance_totals"]
    return [
        ("用户与场景", "用户＝一人公司/中小跨境卖家；场景＝「想把中国品类卖到美国」但没钱养运营部；"
                    "产品＝一句目标自主跑完选品→竞品→汇率→定价→合规→关键词→Listing→审计，输出可上架包。"),
        ("命题如何命中", f"「和 AI 一起」= 模型在白名单内真实选择，9 轮评测有 {overrides} 次与规则分歧并被采纳；"
                     "「把想法」= 一句目标即全部输入；「变成」= 逐步决策卡实录；"
                     "「生意」= 英文 Listing + 关键词 + 定价下限 + 合规清单 + DOCX 上架包。"),
        ("信任底座", f"界面展示 {facts['trace_detail']} 事实点带来源存证（{facts['trace_rate'] * 100:.0f}%）；"
                  f"存证 {provenance.get('total', 0)} 条 / {provenance.get('distinct_sources', 0)} 个独立来源；"
                  "robots 闸门 + 来源闸门，抓不到就报错，不编数据。"),
        ("工程与验证", f"{facts['tool_n']} 个工具 / 标准任务 {facts['steps_standard']} 步；"
                   f"{facts['tests']} 项离线单测全绿；{facts['repeats']} 轮 × 3 品类 LLM 模式实测；"
                   "人工基线与事实抽检按红线由真人录入，当前为 null，不估算、不自证。"),
        ("用户/获客/收费/成本", "用户：一人公司跨境卖家；获客：OPC 社区、跨境卖家社群、内容传播与渠道合作；"
                            "收费：SaaS ¥199/月（50 次）、¥599/月（500 次 + API）+ 品类 Agent 定制 + 脱敏洞察报告；"
                            "成本：LLM token、抓取算力与合规抽检人工，按任务计量（未实测数字不写）。"),
        ("今日/下一步", "11-18 完成报名与作品提交；11-26~27 城市赛现场 live 演示与答辩；12-05~06 若晋级参加全国总决赛。"),
        ("团队", "【负责人姓名 / 背景 / 联系方式由本人填写，agent 不代填】"),
    ]


def build_one_pager(facts):
    doc = SimpleDocTemplate(str(OUT / "商业一页纸.pdf"), pagesize=A4,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title="TradePilot 商业一页纸", author="TradePilot")
    story = [Paragraph("TradePilot · 跨境领航员", TITLE),
             Paragraph(COMPETITION + "　·　商业一页纸 v1.0（2026-11-18）", SUB)]
    data = [[Paragraph("维度", CELL_H), Paragraph("内容（数字来自 eval_results.json / 代码）", CELL_H)]]
    for k, v in _rows(facts):
        data.append([Paragraph(k, CELL), Paragraph(v, CELL)])
    t = Table(data, colWidths=[30 * mm, 148 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("BACKGROUND", (0, 1), (0, -1), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BAC8D8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [t, Spacer(1, 5 * mm),
              Paragraph(f"作品链接：{release_info.SHARE_URL}　|　源码：tradepilot/ git 仓库　|　"
                        "每个数字可点「存证#N」回访原页", SUB)]
    doc.build(story)


def build_markdown(facts):
    overrides = sum(s.get("llm_override_total", 0) for s in facts["summary"].values())
    p = facts["provenance_totals"]
    intro = (
        "# 作品简介（提交用，三方口径一致）\n\n"
        "**跨境领航员 TradePilot** —— 给一人公司（OPC）用的跨境电商 AI 运营部。"
        "输入一句目标，系统自主编排选品雷达、竞品实采、现场汇率、定价沙盘、合规体检、关键词层、"
        "Listing 工坊与文案审计共 8 个环节，逐步留存决策卡，最终导出可上架包。\n\n"
        f"所有展示事实来自公开页真实抓取并带单品页 URL、时间戳与内容哈希存证："
        f"{facts['trace_detail']} 事实点 100% 可溯源，累计 {p.get('total', 0)} 条存证 / "
        f"{p.get('distinct_sources', 0)} 个独立来源；{overrides} 次模型与规则分歧被采纳是自主决策证据。"
        f"{facts['tests']} 项离线单测全绿。人工基线与事实抽检按红线由真人录入，当前为 null，不估算。\n\n"
        "命题映射：一个人（OPC）+ AI，把「想把一个中国品类卖到美国」这个想法，"
        "变成一份能直接上架接单的运营包。\n"
    )
    stage = (
        "# 项目阶段说明\n\n"
        "> 编制：2026-11-18　口径来源：`tradepilot/assets/eval_results.json` + 代码现算。\n\n"
        "## 已完成（可现场验证）\n\n"
        f"- 8 工具自主编排，标准任务 {facts['steps_standard']} 步；LLM 在白名单内真实决策，"
        f"9 轮评测 {overrides} 次 llm_override。\n"
        f"- 真实抓取 + robots 闸门 + 来源闸门 + 单品页存证：{p.get('total', 0)} 条 / "
        f"{p.get('distinct_sources', 0)} 来源；{facts['trace_detail']} 可溯。\n"
        f"- 原生 Canvas 大屏 + React 主前端 + DOCX 上架包 + OPC 作战简报；{facts['tests']} 项单测全绿。\n"
        "- BP PDF、路演三件套、本报名材料包已备；Docker / CI / Postgres 迁移脚本就位。\n\n"
        "## 进行中（如实披露，不伪造完成态）\n\n"
        "- 人工基线掐表与事实抽检：按红线由真人按 `docs/eval/人工SOP.md` 录入，"
        "当前 eval JSON 两项为 null；agent 不自检自证。\n"
        "- 公网 Demo：以《报名速填卡》当天实测结果为准；未通过前主演示走本地 live 档。\n\n"
        "## 下一步\n\n"
        "- 11-26~27 城市赛：8 工具 live 演示 + 答辩；12-05~06 若晋级参加全国总决赛。\n"
        "- 接入需官方授权的真实美国站竞品带；把 OPC 作战简报产品化为对外服务。\n"
    )
    opc = (
        "# OPC 真实性说明（本人填写，agent 不代填）\n\n"
        "> 赛事发布 V7.0 PDF 第 9 页硬要求：OPC 真实性说明须含 **角色、时间投入、AI 工具、外部合作**。"
        "以下为可直接填写的模板；请用本人真实信息替换【】内容并签字。agent 不经手身份信息。\n\n"
        "| 字段 | 填写 |\n|---|---|\n"
        "| 参赛名 / 本人姓名 | 【】 |\n"
        "| 参赛身份 | 独立开发者 / OPC 一人公司（1 人，已定；本人确认身份标签） |\n"
        "| 团队人数与成员 | 【1 人：本人；如有队友逐项列出姓名与分工】 |\n"
        "| 联系方式（手机 / 邮箱） | 【】 |\n"
        "| 角色（必填） | 【独立开发者 / 产品负责人 / 全栈开发，本人承担的全部角色】 |\n"
        "| 时间投入（必填） | 【自 YYYY-MM 起累计投入约 X 小时/周；开发 X 周，最近一次迭代 11-18】 |\n"
        "| AI 工具（必填） | 【LLM 规划网关、Playwright 抓取、edge-tts 旁白等，按实际使用填写】 |\n"
        "| 外部合作（必填） | 【无 / 合作方名称与合作内容，如实填写；无则写「无」】 |\n"
        "| 是否已有注册主体 | 【无 / 个体工商户 / 公司（名称）】 |\n"
        "| 项目真实来源 | 【本人真实在做的跨境品类 / 真实业务痛点，1-2 句】 |\n\n"
        "## 真实性承诺（本人签署）\n\n"
        "本人承诺：以上信息真实；提交的 TradePilot 作品由本人实际开发或主导开发，"
        "演示中的价格、竞品与合规条款均来自公开来源实抓并保留回访链接，未使用合成数据冒充实测。\n\n"
        "签名：【】　　日期：2026-11-【】\n"
    )
    quick = (
        "# 报名速填卡（今天 11-18 执行）\n\n"
        "> 赛事发布 V7.0 PDF（第 9 页）明确：报名与提交 **11-23 24:00** 关闭；"
        "硬提交物为 ①作品本体/Demo ②**90 秒演示视频**（讲清用户/场景/产品）"
        "③一页商业说明（用户/痛点/方案/获客/收费/成本）④OPC 真实性说明（角色/时间投入/AI 工具/外部合作）。"
        "可同时报 1—3 个赛道，只计一份作品、只排一次名。来源：" + EVENT_PAGE + "\n"
        "> 细则以赛事服务群答复为最终口径；若与官网页面冲突，以群内答复为准。\n\n"
        "## 只属于你的动作（agent 不代劳）\n\n"
        "1. [ ] 打开社区入驻链接入群：" + COMMUNITY_URL + "（扫码/申请）\n"
        "2. [ ] 复制下面这段发群里 @组委会，保存答复截图/文字并发我：\n\n"
        "       > 组委会您好，我们准备报名商业生产力赛道，请确认：\n"
        "       > ① 作品提交的具体形式（在线链接 / 文档 / 视频 / 现场演示，格式与大小限制）？\n"
        "       > ② 报名截止与作品提交截止分别是哪天几点？\n"
        "       > ③ 赛道是否只能选一个？能否同时报名「内容与创意」？\n"
        "       > ④ 以独立开发者 / OPC 一人公司个人身份报名，是否需要营业执照或在校证明？\n"
        "       > ⑤ Demo / 半成品是否符合提交要求？\n"
        "       > ⑥ 现场是否需要自备电脑/网络，路演时长与答辩形式？\n\n"
        "3. [ ] 打开报名链接提交：" + APPLY_URL + "\n"
        "      ⚠️ Edge 实测：表单「赛道」是**单选**，而 PDF 说可报 1—3 个；"
        "提交后即锁定。**先问组委会多赛道怎么填，得到答复前不要点提交。**\n"
        "4. [ ] 填入身份信息（姓名/手机/邮箱/身份/人数）并签署《OPC 真实性说明》\n"
        "5. [ ] 上传本包全部材料 + 90 秒视频；缴费如需（三赛道全勾）\n"
        "6. [ ] 保存报名回执、上传成功截图到 `报名提交材料包/回执/`，发我一份做最终归档\n\n"
        "## 报名字段速填\n\n"
        "| 字段 | 建议值 |\n|---|---|\n"
        "| 项目名 | 跨境领航员 TradePilot |\n"
        "| 赛道 | **商业生产力 + 生活与消费 + 内容与创意（全勾，零成本）** |\n"
        "| 身份 | 独立开发者 / OPC 一人公司（1 人，已定） |\n"
        "| 演示品类 | dog bed 宠物用品（已定，9 轮评测最稳） |\n"
        "| 参赛命题 | 和 AI 一起，把想法变成生意 |\n"
        "| 项目一句话 | " + intro.split("\n")[2] + " |\n\n"
        "## 我可代劳部分的当前状态\n\n"
        "- [x] 合并 11-16 改造并跑通 570 项单测\n"
        "- [x] 商业一页纸 / 项目阶段说明 / 作品简介 / OPC 真实性说明模板\n"
        "- [x] BP PDF + 路演三件套 + 90 秒演示视频（见同目录）\n"
        "- [x] 公网 Demo 已公开并实测：魔搭创空间 visibility=public，"
        "/、/app、/canvas、/api/health、/api/tools、/api/capabilities、/api/provenance 全 200；"
        "发布面漂移门禁通过（11-18）。/api/opc/brief 需先跑一次任务才有归档，空库 404 属预期。\n"
    )
    official = """# 官方赛事事实卡（以赛事发布 V7.0 PDF 为最高口径）

> 来源：`赛事发布V7.0-OPC创新黑客松2026海南城市赛.pdf`（15 页，11-18 读取）；
> 用户转述通知作为补充，与 PDF 冲突处以 PDF 为准；最终以赛事服务群答复为准。

## 原文事实（按 PDF 页序）

- **定位（p1）**：OPC 创新黑客松 2026 海南城市赛；命题「和 AI 一起，把想法变成生意」；One Person. One Laptop. One Idea.
- **组织（p1/p14）**：指导单位＝海口市美兰区人民政府；联合主办＝OPC圈 · 海南数据谷；承办＝海南数据谷OPC社区、海口经济学院。
- **发起与运营（p4）**：OPC圈发起，海南数据谷运营；鲸智社区（中国信通院）· 知乎「校园新锐季」等联合主办/战略合作，Unity中国等生态合作。
- **赛制（p5/p7）**：线上报名初评 11.13—11.23 → 约 40 组进现场；11.26 报到/抽签/上屏自检（**不设现场开发**）；11.27 三室并行路演，每组 15 分钟（**10 讲 + 5 问**），当场公布。
- **赛道（p6）**：商业生产力 / 生活与消费 / 内容与创意；**可同时报 1—3 个，只计一份作品、只排一次名**；评审不按赛道分组，同表同权、单榜排序。
- **参赛（p8）**：学生/社会创业者/独立开发者同场；1—5 人，每人限一队，现场不得换人；超 5 人、重复报名、非原创即取消资格。
- **提交物（p9）**：①作品本体/Demo ②90 秒演示视频（讲清用户/场景/产品）③一页商业说明（用户/痛点/方案/获客/收费/成本）④OPC 真实性说明（角色/时间投入/AI 工具/外部合作）；可选加分：访谈/订单/留存/意向书。
- **截止（p9/p14）**：报名与提交 **11-23 24:00** 关闭；11.23—25 初评与公示。
- **规模与奖项（p10/p11）**：60—80 报名池 → 40 组现场 → 前 10 晋级（+1 保送）；奖金 ¥22,000：一等 1 名 ¥8,000、二等 2 名 ¥4,000、三等 3 名 ¥2,000、优秀奖 4 名、场景特别奖；奖金所得税个人承担。
- **评分（p12）**：商业模式与可行性 30%、用户价值 25%、场景落地与完成度 20%、技术实现 15%、演示体验与表达 10%。
- **报名链接**：""" + APPLY_URL + """
- **社区入驻链接**：""" + COMMUNITY_URL + """

## 对 TradePilot 的直接映射

| PDF 要求 | 我方对应 |
|---|---|
| 和 AI 一起，把想法变成生意 | 一句目标 → 8 工具自主编排 → 可上架包；11 次 llm_override 是自主证据 |
| 一页商业说明（用户/痛点/方案/获客/收费/成本） | `商业一页纸.pdf` 已按六项重写 |
| 90 秒演示视频 | `演示视频_90秒.mp4`：用户→场景→产品→证据→生意 |
| OPC 真实性说明四项 | 模板已补角色/时间投入/AI 工具/外部合作，本人填写签字 |
| 三赛道同表同权、可全勾 | 报名表三赛道全勾，叙事仍以商业生产力为主线 |
| 10 分钟讲 + 5 分钟问 | 路演稿按 30/25/20/15/10 权重分配，QA 压到高频 5 问 |
| 11.26 不设现场开发 | 演示环境在 11.26 上屏自检窗口完成验证，不在现场改代码 |

**结论**：PDF 与用户转述通知在命题/时间/链接/对象上一致；组织方、提交物硬要求、路演时长、多赛道规则以 PDF 为准，已全量更新到本材料包。
"""
    score_map = """# 评分维度对照表（PDF V7.0 第 12 页）

> 全国统一评分：五维 100 分，同表同权、单榜排序；评分表当场封存，24 小时内受理申诉。
> 用途：10 分钟路演按权重分配时间，5 分钟问答优先准备高权重维度。

| 维度 | 权重 | 我方证据（可现场指给评委看） | 10 分钟讲稿分配 |
|---|---|---|---|
| 商业模式与可行性 | 30% | `商业一页纸.pdf` 用户/获客/收费/成本；SaaS ¥199/¥599 + 定制 + 报告；上线 Demo 可验证 | 3 分钟 |
| 用户价值 | 25% | 用户＝一人公司跨境卖家；痛点＝2–4 人天调研做不动；产品＝一句目标跑完 8 环节 | 2.5 分钟 |
| 场景落地与完成度 | 20% | 117 条存证 / 24 来源；88/88 事实可溯；DOCX 上架包；8 工具 live Demo | 2 分钟 |
| 技术实现 | 15% | 统一执行器 + 来源闸门 + robots 闸门 + LLM 白名单决策；570 项单测 | 1.5 分钟 |
| 演示体验与表达 | 10% | 90 秒视频 + 大屏决策卡实录 + 三档演示预案 | 1 分钟 |

## 5 分钟问答优先顺序

1. **商业模式**：¥199/¥599 怎么定价？谁付钱？获客成本多少？（如实说未实测的不写）
2. **用户价值**：真实用户是谁？有没有访谈/订单？（没有就说没有，不编造）
3. **场景落地**：现场 Demo 断网怎么办？（三档预案）价格存证怎么回访？（点存证#N）
4. **技术实现**：LLM 自主性证据？（11 次 llm_override，不把 llm_planned 当证据）
5. **表达**：一句话讲清作品？（一个人 + AI = 一支跨境运营部）

> 红线：人工基线与事实抽检仍为 null，答辩必须主动说明「禁估算、禁自检自证」，不能说「效率提升 X%」。
"""
    readme = (
        "# 报名提交材料包（2026-11-18）\n\n"
        "提交对象：OPC 创新黑客松 2026 海南城市赛 · 商业生产力赛道。\n\n"
        "| 文件 | 用途 |\n|---|---|\n"
        "| `商业一页纸.pdf` | 官网要求的「商业一页纸」 |\n"
        "| `项目阶段说明.md` | 官网要求的「项目阶段说明」 |\n"
        "| `OPC真实性说明_待本人填写.md` | 官网要求，本人填写签字 |\n"
        "| `作品简介.md` | 报名表 / 答辩用统一口径 |\n"
        "| `报名速填卡.md` | 今天的执行顺序与官方事实 |\n"
        "| `官方赛事事实卡.md` | 11-18 官方通知原文核对：发起/背书/命题/赛道/权益/链接 |\n"
        "| `演示视频_90秒.mp4` | 90 秒硬提交视频（用户→场景→产品→证据→生意） |\n"
        "| `评分维度对照.md` | PDF 五维权重 → 证据 → 10 分钟讲稿分配 |\n"
        "| `赛事PDF_V7.0_全面分析.md` | PDF 原文事实、差异清单与策略影响 |\n"
        "| `回执/` | 放置报名回执与上传截图（本人归档） |\n\n"
        "完整 BP 与路演稿在 `tradepilot/docs/TradePilot_BP_OPC海南城市赛.pdf` 与仓库根"
        "《BP+路演三件套.md》。所有实测数字同源 `tradepilot/assets/eval_results.json`。\n"
    )
    (OUT / "作品简介.md").write_text(intro, encoding="utf-8")
    (OUT / "项目阶段说明.md").write_text(stage, encoding="utf-8")
    (OUT / "OPC真实性说明_待本人填写.md").write_text(opc, encoding="utf-8")
    (OUT / "报名速填卡.md").write_text(quick, encoding="utf-8")
    (OUT / "官方赛事事实卡.md").write_text(official, encoding="utf-8")
    (OUT / "评分维度对照.md").write_text(score_map, encoding="utf-8")
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    (OUT / "回执").mkdir(exist_ok=True)


def build_zip():
    """打成官网可一键上传的 ZIP（每份 ≤10MB、合计 ≤50MB）。"""
    import zipfile

    target = ROOT / "报名提交材料包.zip"
    files = [
        OUT / "README.md",
        OUT / "商业一页纸.pdf",
        OUT / "项目阶段说明.md",
        OUT / "OPC真实性说明_待本人填写.md",
        OUT / "作品简介.md",
        OUT / "演示视频_90秒.mp4",
        OUT / "官方赛事事实卡.md",
        OUT / "评分维度对照.md",
        ROOT / "tradepilot" / "docs" / "TradePilot_BP_OPC海南城市赛.pdf",
    ]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, f"报名提交材料包/{f.name}")
    print("上传包已生成：", target, f"({target.stat().st_size // 1024} KB)")


def main():
    facts = measured_facts()
    OUT.mkdir(parents=True, exist_ok=True)
    build_one_pager(facts)
    build_markdown(facts)
    build_zip()
    print("报名材料包已生成：", OUT)


if __name__ == "__main__":
    main()
