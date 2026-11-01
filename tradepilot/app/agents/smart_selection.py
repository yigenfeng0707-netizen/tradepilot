# Copyright 2026 TradePilot project. Apache-2.0.
"""智能选品 Agent（M2）：把选品雷达的真实样本算成可解释的候选排序。

**这个模块存在的意义是回答"先调研哪个候选词"，不是回答"哪个品类好卖"。**
两者的区别决定它能不能上生产：前者只需要本批真实样本，后者需要销量/搜索量/
市场规模数据源——本作品一个都没接，所以一个都不写。

数据路径（全部经工具层，不读任何静态文件）：
1. `opportunity_scan` → 多候选词真实抓取，输出五分量机会分 + 价格带 + 存证号；
2. `fx_rate` → 涉及 CNY 折算时的现场汇率（绝不内嵌经验汇率）；
3. `pricing_sandbox` → 到岸成本 / 报价下限 / 价格带对照（确定性计算器，零 LLM）。

**五个字段的诚实口径**（随输出返回，前端必须展示）：

| 字段 | 口径 |
|---|---|
| overall_score | 机会分（五分量加权），衡量**样本分析价值与定位空间**，不是需求量 |
| market_size / growth_rate | **未接入数据源**，返回"未接入"，不编数字 |
| competition_level | 样本口径代理：噪声率 + 标题去重数，明示是代理 |
| entry_difficulty | 由定价沙盘「报价下限 vs 价格带」真实对照得出 |
| supply_recommendations | **空列表**（1688/阿里国际站需企业账号），不编造供应商 |

预算档位**不参与打分**——没有采购价与 MOQ 数据，拿预算调权重就是凭空造信号。
它只用于标注候选在本批中的价格位置（budget_fit）。
"""
from __future__ import annotations

from typing import Any

from ..tools import competitor as market_scan
from ..tools import fx, opportunity, pricing
from . import base
from .sample_market import CATEGORIES_WITH_RULES, MARKET_SOURCES, tool_category

AGENT = "智能选品Agent"

# 五分量 → 界面标签。顺序即展示顺序（样本质量在前，价格信号在后）。
_COMPONENT_LABELS: tuple[tuple[str, str], ...] = (
    ("yield", "样本充足度"),
    ("noise", "样本干净度"),
    ("depth", "价格水位"),
    ("spread", "定位空间"),
    ("terms", "表达丰富度"),
)

_SUPPLY_NOTE = (
    "供应链推荐未接入：1688 / 阿里国际站 / 义乌小商品城均需企业账号与签约，"
    "当前无可用数据源。此处返回空列表而非编造供应商、MOQ 与报价——"
    "编造的供应商名单会直接导致采购决策错误，比空列表危险得多。"
)

# 中文品类 → 工具层品类键。工具层的 CATEGORY_RULES 与 BUILTIN_HYPOTHESES 都用
# 英文键（pet/home/electronics），传中文会导致两件事同时失效：
# ① 内置候选词取不到（get 返回空）；② relevant() 找不到规则，**整层过滤被跳过**，
# 噪声商品全部放行。实测踩过：传中文时 5 个候选词退化成 1 个且全是噪声。
# 映射表与 market_insight 共用（sample_market.CATEGORY_TOOL_KEY），避免两份口径漂移。
_HAS_BUILTIN = CATEGORIES_WITH_RULES

_METHOD = (
    "综合评分 = 选品雷达机会分（yield 0.20 / noise 0.15 / depth 0.25 / spread 0.25 / "
    "terms 0.15 加权），全部由本批真实抓取样本算出，分位只在本次候选集内可比。"
    "评分用于排序调研优先级，不构成采购、备货或上架决策依据。"
)


def _money(value: Any) -> float:
    """价格一律保留两位：抓取值是 float，直接格式化会把 31.499999999999996
    漏进界面文案。展示层的舍入必须在服务端做一次，避免每个前端各舍一遍。"""
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _level(score: float) -> str:
    """分数档位：仅用于界面呈现，边界是设计选择而非数据结论。"""
    if score >= 75:
        return "优先调研"
    if score >= 55:
        return "值得关注"
    if score >= 35:
        return "信息不足"
    return "样本过薄"


def _entry_difficulty(profit: dict[str, Any] | None) -> str:
    """进入难度：由「报价下限 vs 价格带」真实对照，不是主观评级。"""
    if not profit:
        return "未评估（定价沙盘未跑通：缺采购价或汇率）"
    band = profit.get("price_band") or {}
    floor = profit.get("floor_price")
    if floor is None or not band:
        return "未评估（缺价格带）"
    if floor > band.get("max", 0):
        return (f"高——报价下限 ${floor:.2f} 高于价格带上限 "
                f"${band['max']:.2f}，按当前成本打不出目标毛利")
    if floor < band.get("min", 0):
        return (f"低——报价下限 ${floor:.2f} 低于价格带下限 "
                f"${band['min']:.2f}，有价格优势可上探定价")
    return (f"中——报价下限 ${floor:.2f} 落在价格带 "
            f"${band['min']:.2f}–${band['max']:.2f} 内")


def _budget_fit(cand_median: float, batch_median: float, currency: str) -> str:
    """预算友好度：候选中位价相对本批中位价的位置（批内相对，不做跨批比较）。"""
    if not batch_median:
        return "本批无可比中位价"
    delta = (cand_median - batch_median) / batch_median
    if delta <= -0.1:
        return f"低于本批中位价 {abs(delta):.0%}（预算友好，{currency} {cand_median:.2f}）"
    if delta >= 0.1:
        return f"高于本批中位价 {delta:.0%}（{currency} {cand_median:.2f}）"
    return f"接近本批中位价（{currency} {cand_median:.2f}）"


def _competition(cand: dict[str, Any]) -> str:
    """竞争程度：**样本口径代理**，不是真实竞争度。"""
    kept = cand.get("sample_size") or 0
    raw = cand.get("raw_count") or 0
    noise = cand.get("noise_rate") or 0.0
    return (f"样本口径代理：{kept}/{raw} 条可用（噪声率 {noise:.0%}），"
            f"标题去重 {cand.get('distinct_terms')} 条；"
            "真实竞争度需卖家数量与 listing 总量数据，未接入")


def _action_plan(top: dict[str, Any], profit_ok: bool) -> dict[str, dict[str, Any]]:
    """行动计划：纯流程模板，不含任何数据断言。"""
    word = top.get("keyword", "")
    tasks1 = [
        f"用「{word}」在目标站复核前 10 条竞品的真实售价与评分",
        "确认目标站该品类是否需要强制认证（如 FCC / CE / 儿童用品 CPSIA）",
        "导出选品雷达存证号，逐条回访原始页面确认抓取时点价格未变",
    ]
    tasks2 = ["向至少 3 家供应商询价，取得真实采购单价与 MOQ",
              "用真实采购价重跑定价沙盘，替换当前成本率假设"]
    if not profit_ok:
        tasks2.insert(0, "补齐采购价后重跑本页，利润分析当前不可用")
    tasks3 = ["小批量试单（不超过 MOQ 的 1/3）验证物流时效与破损率",
              "按定价沙盘的建议价上架，前 2 周只做自然流量观察"]
    return {
        "phase_1": {"name": "数据复核", "tasks": tasks1},
        "phase_2": {"name": "供应链询价", "tasks": tasks2},
        "phase_3": {"name": "小批试单", "tasks": tasks3},
    }


def _profit_block(profit: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    """利润分析块。跑不通时给出**明确原因**，而不是填 0 或占位符冒充结果。"""
    if not profit:
        blank = {"—": "不可用"}
        return {
            "available": False, "reason": reason,
            "cost_breakdown": blank, "revenue": blank,
            "break_even": {"盈亏平衡销量": reason},
            "sensitivity": [],
        }
    cb = profit.get("cost_breakdown") or {}
    sugg = profit.get("suggestion") or {}
    price = sugg.get("price")
    landed = profit.get("landed_cost")
    commission = cb.get("commission_pct", 0)
    net = (price * (1 - commission / 100.0) - landed) if price and landed is not None else None
    margin = (net / price) if net is not None and price else None
    cost_breakdown = {
        "采购成本": f"${cb.get('unit_cost_usd', 0):.2f}",
        "头程物流": f"${cb.get('freight_usd', 0):.2f}",
        f"关税 {cb.get('duty_pct', 0):.0f}%": f"${cb.get('duty_usd', 0):.2f}",
        "尾程配送": f"${cb.get('last_mile_usd', 0):.2f}",
        "到岸总成本": f"${landed:.2f}" if landed is not None else "—",
        "平台佣金": f"{commission:.0f}%",
    }
    revenue = {
        "建议售价": f"${price:.2f}" if price else "—",
        "单件净利": f"${net:.2f}" if net is not None else "—",
        "净利率": f"{margin:.1%}" if margin is not None else "—",
        "价格带来源": profit.get("band_source", "—"),
    }
    return {
        "available": True,
        "reason": None,
        "cost_breakdown": cost_breakdown,
        "revenue": revenue,
        "break_even": {"盈亏平衡销量": "需固定成本（开店/推广/仓储）才能计算，当前未提供"},
        "positioning": sugg.get("positioning"),
        "sensitivity": profit.get("sensitivity") or [],
        "cost_basis_note": profit.get("cost_basis_note"),
        "assumptions": profit.get("assumptions"),
    }


async def run(category: str, marketplace: str = "US", budget: str = "中",
              keyword: str = "", keyword_cn: str = "",
              allow_sample: bool = True, limit: int = 12,
              unit_cost_cny: float | None = None,
              source: str = "") -> dict[str, Any]:
    """智能选品：真实扫描 → 机会分 → 定价沙盘 → 可解释排序。

    `allow_sample` 在本 Agent 里**不生效**（无样本兜底路径）：选品评分若用样本
    数据兜底，等于给用户一个假的优先级排序，比直接报错危险。参数保留是为了
    与其他 Agent 的调用签名一致，显式忽略并在此说明。
    """
    del allow_sample  # 显式忽略：见 docstring
    category = (category or "").strip()
    if not category:
        return base.fail(AGENT, "缺少 category（品类名）")

    sources = MARKET_SOURCES.get(marketplace, ())
    if not sources:
        return base.fail(
            AGENT,
            f"{marketplace} 市场当前没有可抓取的数据源（官方 API 待 M3 接入）。"
            "选品评分需要真实样本，本 Agent 不提供样本兜底——"
            "用样本算出的排序会误导调研优先级，故直接报错。",
            {"marketplace": marketplace, "sources_tried": []})

    src = (source or sources[0]).strip().lower()
    cat_key = tool_category(category)

    scan_input: dict[str, Any] = {
        "category": cat_key, "source": src, "limit": limit,
    }
    # 用户给了词就用用户的（来源标记会变成 user）；没给则靠 cat_key 取内置假设词。
    # 注意工具层的 keywords 参数是**逗号分隔字符串**，传 list 会被 str() 成
    # "['dog bed']" 当成一个词去搜——实测踩过，搜出来的全是无关结果。
    words = (keyword or "").strip()
    if words:
        scan_input["keywords"] = words
    elif cat_key not in _HAS_BUILTIN:
        return base.fail(
            AGENT,
            f"「{category}」没有内置候选词，且未提供 keyword。"
            "内置假设词只覆盖宠物用品 / 家居用品 / 3C 电子三类；"
            "其余品类请显式传入 keyword（英文检索词，如 \"yoga mat\"）再试——"
            "不传就无从扫描，而凭空给一组候选词等于编造。",
            {"category": category, "category_key": cat_key,
             "categories_with_builtin": sorted(_HAS_BUILTIN)})

    outcome = await opportunity.run(scan_input)
    if not outcome.ok:
        return base.fail(AGENT, f"选品扫描失败：{outcome.summary}",
                         {"source": src, "detail": outcome.data})

    scan = outcome.data or {}
    candidates: list[dict[str, Any]] = scan.get("candidates") or []
    if not candidates:
        return base.fail(AGENT, "扫描完成但没有可用候选（样本全部不足）",
                         {"source": src})

    currency = scan.get("currency", "")
    top = candidates[0]
    medians = [_money(c.get("price_median")) for c in candidates]
    batch_median = sorted(medians)[len(medians) // 2] if medians else 0.0

    # ---- 定价沙盘：需要商品级存证 + 现场汇率 ----
    # 关键差异：opportunity_scan 每词只记**一条 observation 级**存证（整个观测结果），
    # 而 pricing_sandbox 要的是**逐条商品**的 item 级存证。直接拿候选的
    # provenance_ids 去调 pricing，会恒报"可用商品数据点不足 2 条"——实测踩过。
    # 因此对 Top1 候选补一次 market_scan，取回逐条商品的存证号。
    profit: dict[str, Any] | None = None
    profit_reason = ""
    item_ids: list[int] = []
    rescan = await market_scan.run({
        "keyword": top.get("keyword_used") or top.get("keyword"),
        "category": cat_key, "source": src, "limit": limit,
    })
    if rescan.ok and rescan.data:
        item_ids = [int(i["provenance_id"])
                    for i in (rescan.data.get("items") or []) if i.get("provenance_id")]

    price_input: dict[str, Any] = {"item_ids": item_ids, "marketplace": marketplace}
    if unit_cost_cny is not None:
        price_input["unit_cost_cny"] = float(unit_cost_cny)
    need_fx = unit_cost_cny is not None or currency == "CNY"
    if need_fx:
        fx_out = await fx.run({"base": "USD", "quote": "CNY"})
        if fx_out.ok and fx_out.data:
            price_input["fx_id"] = fx_out.data.get("provenance_id")
        else:
            profit_reason = f"现场汇率不可达，无法折算成本：{fx_out.summary}"

    if not profit_reason:
        price_out = await pricing.run(price_input)
        if price_out.ok and price_out.data:
            data = price_out.data
            # 成本基数为 0 意味着「没提供采购价且无 CNY 数据点」——
            # 此时到岸成本只有物流，利润分析是残缺的，必须判为不可用。
            if data.get("cost_basis_cny"):
                profit = data
            else:
                profit_reason = ("缺少采购价：定价沙盘只算出物流成本，"
                                 "利润分析不完整（请在参数里提供 unit_cost_cny）")
        else:
            profit_reason = f"定价沙盘未跑通：{price_out.summary}"

    # ---- 产品推荐：一个候选一条，分数全部来自真实分量 ----
    products: list[dict[str, Any]] = []
    for cand in candidates:
        comps = cand.get("components") or {}
        scores = {label: float(comps.get(key, 0.0))
                  for key, label in _COMPONENT_LABELS}
        scores["综合评分"] = float(cand.get("score") or 0)
        products.append({
            "product": cand.get("keyword", ""),
            "predicted_sales": "未接入（无销量数据源）",
            "rating": f"第 {cand.get('rank')} 位",
            "reason": (f"{cand.get('sample_size')} 条真实样本，价格带 "
                       f"{currency} {_money(cand.get('price_min')):.2f}–"
                       f"{_money(cand.get('price_max')):.2f}"
                       f"（中位 {currency} {_money(cand.get('price_median')):.2f}）"),
            "scores": scores,
            "budget_fit": _budget_fit(_money(cand.get("price_median")),
                                      batch_median, currency),
            "suggested_moq": "需供应商数据",
            "estimated_roi": ("净利率见利润分析" if profit
                              else "需采购价"),
            "sample_size": cand.get("sample_size"),
            "price_band": {"min": _money(cand.get("price_min")),
                           "median": _money(cand.get("price_median")),
                           "max": _money(cand.get("price_max")),
                           "currency": currency},
            "provenance_ids": cand.get("provenance_ids") or [],
            "source_url": cand.get("source_url"),
            "components_raw": comps,
        })

    details = {label: float((top.get("components") or {}).get(key, 0.0))
               for key, label in _COMPONENT_LABELS}
    total = float(top.get("score") or 0)

    data = {
        "category": category,
        "target_market": marketplace,
        "budget_level": budget,
        "data_quality": base.QUALITY_REAL,
        "overall_score": {
            "total": total, "details": details, "level": _level(total),
            "basis": _METHOD,
            "top_keyword": top.get("keyword"),
        },
        "market_opportunity": {
            "market_size": "未接入（无市场规模数据源，不编数字）",
            "growth_rate": "未接入（无增长率数据源，不编数字）",
            "competition_level": _competition(top),
            "entry_difficulty": _entry_difficulty(profit),
            "batch_size": len(candidates),
            "currency": currency,
            "data_source": src,
            # 目标市场与实际数据源可能不一致（US 目标但只有国内源可抓）。
            # 不说清楚，用户会以为看到的是美国站竞品价。
            "source_note": (
                f"目标市场 {marketplace}，实际数据源 {src}（{currency} 计价）。"
                + ("" if currency == "USD" else
                   "当前抓取的是国内站价格，不是目标站竞品价——"
                   "目标站可抓源在本机/本网络不可达，这是如实降级，不是数据错误。")
            ),
        },
        "product_recommendations": products,
        "supply_recommendations": [],
        "supply_note": _SUPPLY_NOTE,
        "profit_analysis": _profit_block(profit, profit_reason or "定价沙盘未运行"),
        "action_plan": _action_plan(top, bool(profit)),
        "keyword_origin": scan.get("keyword_origin"),
        "keywords_requested": scan.get("keywords_requested"),
        "scans_failed": scan.get("scans_failed") or [],
        "scans_thin": scan.get("scans_thin") or [],
        "method": _METHOD,
        "basis": scan.get("basis"),
        "budget_note": ("预算档位不参与打分——缺采购价与 MOQ 数据，"
                        "用预算调权重等于凭空造信号；它只用于标注候选在本批中的价格位置"),
        "gaps": [
            "市场规模/增长率：未接入数据源",
            "供应链（供应商/MOQ/报价）：未接入，返回空列表",
            "销量/搜索量：未接入，predicted_sales 显式标注",
            "真实竞争度：仅样本代理，非卖家数量口径",
        ],
    }
    provenance_ids: list[int] = []
    for pid in item_ids:                       # 商品级存证优先（定价用得到）
        if pid not in provenance_ids:
            provenance_ids.append(pid)
    for cand in candidates:
        for pid in cand.get("provenance_ids") or []:
            if pid not in provenance_ids:      # 同一存证可能被多个候选引用，去重
                provenance_ids.append(pid)
    if profit and profit.get("fx_provenance_id"):
        pid = int(profit["fx_provenance_id"])
        if pid not in provenance_ids:
            provenance_ids.append(pid)

    return base.wrap(
        AGENT, {**data, "provenance_ids": provenance_ids}, base.QUALITY_REAL,
        f"真实扫描 {len(candidates)} 个候选（{src}，{currency}），"
        f"Top1「{top.get('keyword')}」机会分 {total}；"
        + ("利润分析已跑通" if profit else f"利润分析不可用：{profit_reason}"))
