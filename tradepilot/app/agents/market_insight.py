# Copyright 2026 TradePilot project. Apache-2.0.
"""市场洞察 Agent（M1：从海丝迁入并重写数据来源）。

数据路径（按优先级）：
1. 真实抓取：market_scan 工具按市场装配源（US → dangdang/newegg），
   产出带 provenance_id 的商品与价格带 → data_quality="real"；
2. 样本兜底：全部源失败且 allow_sample=True → 读 sample_market（行业报告口径），
   data_quality="sample" 并在 note 里写明失败原因——绝不把样本伪装成实时数据；
3. 严格模式：allow_sample=False 时直接失败（供对外 API 默认值使用）。

LLM 只做组织与措辞：summary/trend_analysis/recommendation 三个字段，
事实（价格、条数、来源）全部来自工具层回填，prompt 里不给编造机会。
"""
from __future__ import annotations

from typing import Any

from .. import llm
from ..tools import competitor
from . import base
from .sample_market import (CATEGORY_KEYWORDS, MARKET_SOURCES, SAMPLE_MARKET_DATA,
                            SAMPLE_NOTE, tool_category)

AGENT = "市场洞察Agent"


def _tiers(prices: list[float], currency: str) -> list[dict[str, Any]]:
    """把真实价格切成三档（按条数三等分），成交量占比用条数近似。

    小样本（<3 条）不硬切三档：每条一档，避免"1 条数据也编出三段结构"。
    """
    if not prices:
        return []
    ordered = sorted(prices)
    if len(ordered) < 3:
        return [{"tier": "全部样本", "price_range": f"{currency} {ordered[0]:.2f}–{ordered[-1]:.2f}",
                 "volume_share": "100%", "count": len(ordered)}]
    groups = [ordered[: len(ordered) // 3],
              ordered[len(ordered) // 3: (2 * len(ordered)) // 3],
              ordered[(2 * len(ordered)) // 3:]]
    labels = ("大众价位", "中端价位", "高端价位")
    out = []
    for label, g in zip(labels, groups):
        if not g:
            continue
        out.append({"tier": label,
                    "price_range": f"{currency} {g[0]:.2f}–{g[-1]:.2f}",
                    "volume_share": f"{len(g) / len(ordered) * 100:.0f}%",
                    "count": len(g)})
    return out


def _template_summary(category: str, marketplace: str, band: dict[str, float],
                      currency: str, count: int, source: str) -> dict[str, str]:
    """无 LLM 时的模板摘要：只陈述工具层事实，不添加任何推测。"""
    return {
        "summary": f"{category}在{marketplace}市场实时抓取 {count} 条竞品，"
                   f"价格带 {currency} {band['min']:.2f}–{band['max']:.2f}"
                   f"（中位 {currency} {band['median']:.2f}），来源 {source}。",
        "trend_analysis": f"价格分布见 price_tiers（按真实条数三等分）；"
                          f"中位价 {currency} {band['median']:.2f} 可作为定价锚点参考。",
        "recommendation": "建议结合合规核查与定价沙盘确定最终报价；"
                          "竞品条目均带存证号，可逐条回访原始页面。",
    }


async def _llm_summary(category: str, marketplace: str, band: dict[str, float],
                       currency: str, items: list[dict[str, Any]]) -> dict[str, str] | None:
    """LLM 组织摘要：只喂真实事实，要求返回三段 JSON。"""
    if not llm.available():
        return None
    facts = "\n".join(
        f"- {i['title'][:60]} | {currency} {i['price']:.2f} | {i.get('source', '')}"
        for i in items[:6])
    text = await llm.chat(
        "你是跨境电商市场分析助手。只根据用户给出的事实组织语言，"
        "禁止编造事实中没有的数字或来源。返回 JSON："
        '{"summary": "2-3句", "trend_analysis": "2-3句", "recommendation": "1-2句"}，只返回JSON。',
        f"品类：{category}\n市场：{marketplace}\n"
        f"实时抓取 {len(items)} 条竞品，价格带 {currency} {band['min']:.2f}–{band['max']:.2f}，"
        f"中位 {currency} {band['median']:.2f}\n竞品样本：\n{facts}",
        # 推理模型（Atria-Dawn）会把 max_tokens 先花在 reasoning 上：实测 500 时
        # 正文仅 50 字符（预算被推理吃光）→ JSON 解析失败 → 摘要白白降级为模板；
        # 1200 实测正文 309 字符稳定通过。同款坑见 customer_service.py 注释
        max_tokens=1200,
    )
    parsed = llm._extract_json(text or "")
    if parsed and isinstance(parsed.get("summary"), str):
        return {"summary": parsed.get("summary", ""),
                "trend_analysis": parsed.get("trend_analysis", ""),
                "recommendation": parsed.get("recommendation", "")}
    return None


async def run(category: str, marketplace: str = "US", keyword: str = "",
              keyword_cn: str = "", allow_sample: bool = True,
              limit: int = 8) -> dict[str, Any]:
    """市场洞察：真实抓取优先，样本兜底（带明示），严格模式可直接失败。"""
    category = (category or "").strip()
    if not category:
        return base.fail(AGENT, "缺少 category（品类名）")

    kw, kw_cn = CATEGORY_KEYWORDS.get(category, ("", ""))
    keyword = (keyword or kw).strip()
    keyword_cn = (keyword_cn or kw_cn).strip()
    sources = MARKET_SOURCES.get(marketplace, ())
    # 品类必须换成工具层英文键：传中文时 relevant() 找不到 CATEGORY_RULES 规则，
    # 会走"未知品类放行"分支——不报错，但噪声商品全部计入价格带，结果偏脏。
    cat_key = tool_category(category)

    errors: list[str] = []
    for source in sources:
        outcome = await competitor.run({
            "keyword": keyword, "keyword_cn": keyword_cn,
            "category": cat_key, "source": source, "limit": limit,
        })
        if not outcome.ok:
            errors.append(f"{source}: {outcome.summary}")
            continue

        d = outcome.data or {}
        items = d.get("items") or []
        band = d.get("price_band") or {}
        currency = d.get("currency", "")
        tiers = _tiers([i["price"] for i in items], currency)
        summary = await _llm_summary(category, marketplace, band, currency, items)
        generated_by = "llm" if summary else "template"
        if summary is None:
            summary = _template_summary(category, marketplace, band, currency,
                                        len(items), source)
        return base.wrap(AGENT, {
            "category": category,
            "marketplace": marketplace,
            "keyword_used": d.get("keyword_used"),
            "data_quality": base.QUALITY_REAL,
            "price_band": band,
            "price_tiers": tiers,
            "competitors": [
                {"title": i["title"], "price": i["price"], "currency": currency,
                 "source": i.get("source"), "url": i.get("source_url"),
                 "provenance_id": i.get("provenance_id")}
                for i in items],
            "summary": summary,
            "generated_by": generated_by,
            "provenance_ids": ([d.get("scan_provenance_id")] if d.get("scan_provenance_id")
                               else []) + [i["provenance_id"] for i in items],
            "sources_tried": list(sources),
        }, base.QUALITY_REAL,
            f"真实采集 {len(items)} 条竞品（{source}），价格带 "
            f"{currency} {band.get('min', 0):.2f}–{band.get('max', 0):.2f}，全部带存证")

    # ---- 真实数据不可得：样本兜底或严格失败 ----
    if not allow_sample:
        return base.fail(AGENT, "真实抓取全部失败，严格模式拒绝返回样本数据",
                         {"errors": errors, "sources_tried": list(sources)})
    sample = SAMPLE_MARKET_DATA.get(category)
    if sample is None:
        return base.fail(AGENT, f"真实抓取失败且无该品类样本：{category}",
                         {"errors": errors, "sources_tried": list(sources)})

    return base.wrap(AGENT, {
        "category": sample["name"],
        "marketplace": marketplace,
        "data_quality": base.QUALITY_SAMPLE,
        "note": SAMPLE_NOTE + (f"；抓取失败原因：{' / '.join(errors)}" if errors else ""),
        "market_size": sample["market_size"],
        "market_growth": sample["market_growth"],
        "hot_categories": sample["hot_categories"],
        "trends": sample["trends"],
        "price_tiers": sample["price_tiers"],
        "risks": sample["risks"],
        "summary": {
            "summary": sample["market_size"],
            "trend_analysis": sample["trends"][0]["description"] if sample["trends"] else "",
            "recommendation": "样本口径，仅作参考；需真实数据请配置可抓取的源或官方 API。",
        },
        "generated_by": "template",
        "provenance_ids": [],
        "sources_tried": list(sources),
        "errors": errors,
    }, base.QUALITY_SAMPLE,
        "真实抓取不可得，返回样本数据（已明示 data_quality=sample）")
