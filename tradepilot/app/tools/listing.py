# Copyright 2026 TradePilot project. Apache-2.0.
"""Listing 工坊：从真实数据组织上架文案包。

- 有 LLM：模型只做英文文案组织（facts 由服务端给定，禁编造）；
- 无 LLM：输出**合规骨架**——结构合法的标题 + 5 条卖点，规格类信息以
  `[ADD: ...]` 占位符明确标出，绝不编造参数。骨架不是可上架成品，下游
  listing_audit 会把未填占位符判为必改项，形成"生成 → 审计 → 补齐"的闭环；
- 输出中 AI 生成内容显式标注（generated_by: llm/template），一切数字服务端回填。

价格口径：优先用定价沙盘已折算的目标站价格带（USD），而不是原始抓取币种——
美国站文案里出现 ¥ 是本地化错误。
"""
from __future__ import annotations

from typing import Any

from .. import llm, provenance
from .executor import ToolOutcome

# 骨架模板：只写不需要产品参数就能成立的话，参数一律留占位符
SKELETON_BULLETS = [
    "Built for daily use: [ADD: primary material and construction, plus one verifiable "
    "spec such as thickness, load capacity, or fabric composition]",
    "Easy care routine: [ADD: how to clean it — machine wash, spot clean, removable "
    "cover — and how often buyers should do it]",
    "Right size matters: [ADD: dimensions and the pet weight range this size suits; "
    "ask buyers to measure before ordering]",
    "Where it fits: [ADD: rooms, floor types, crate or car use — be specific about the "
    "surfaces it grips]",
    "What's in the box: [ADD: item count, cover, insert, and any care card included]",
]


def _title_case(keyword: str) -> str:
    return " ".join(w.capitalize() if len(w) > 2 else w for w in keyword.split())


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    item_ids = [int(i) for i in (tool_input.get("item_ids") or [])]
    if not item_ids:
        return ToolOutcome(False, "error", None, "缺少 item_ids（须来自 market_scan）")
    items = [p["payload"] for p in (provenance.get(i) for i in item_ids)
             if p and p["kind"] == "item"]
    if not items:
        return ToolOutcome(False, "error", None, "存证中无可用商品数据点")

    keyword = str(tool_input.get("keyword", "")).strip() or items[0]["title"][:40]
    goal = str(tool_input.get("goal", "list this product overseas"))
    marketplace = str(tool_input.get("marketplace") or "US").upper()

    ctx = tool_input.get("context") or {}
    pricing = ctx.get("pricing_sandbox") or {}
    compliance = ctx.get("compliance_check") or {}

    # 价格带：优先定价沙盘的已折算目标站口径（USD），其次原始商品数据点
    priced = pricing.get("price_band") or {}
    if priced and pricing.get("band_source", "").startswith("us_retail"):
        band = {"min": priced.get("min"), "median": priced.get("median"),
                "max": priced.get("max")}
        band_currency = "USD"
    elif priced:
        # domestic_reference 已按现场汇率折算为 USD，可直接用于美国站
        band = {"min": priced.get("min"), "median": priced.get("median"),
                "max": priced.get("max")}
        band_currency = "USD" if marketplace in ("US", "UK") else items[0].get("currency", "USD")
    else:
        prices = sorted(float(i["price"]) for i in items)
        band = {"min": prices[0], "median": prices[len(prices) // 2], "max": prices[-1]}
        band_currency = items[0].get("currency", "USD")

    facts = {
        "keyword": keyword,
        "marketplace": marketplace,
        "reference_price_band": {**band, "currency": band_currency},
        "item_n": len(items),
        "sample_titles": [i["title"] for i in items[:3]],
        "positioning": (pricing.get("suggestion") or {}).get("positioning"),
        "floor_price_usd": pricing.get("floor_price"),
        "compliance_items": [
            {"item": c.get("item"), "authority": c.get("authority")}
            for c in compliance.get("checks", [])
        ][:3] or None,
    }
    facts = {k: v for k, v in facts.items() if v}

    generated_by = "template"
    copy = None
    if llm.available():
        copy = await llm.write_listing(goal, facts)
        if copy:
            generated_by = "llm(基于存证事实；价格等数字以服务端回填为准)"
    if copy is None:
        title_kw = _title_case(keyword)[:120]
        copy = {
            "title": f"{title_kw} — [ADD: core attribute such as size or material]",
            "bullets": list(SKELETON_BULLETS),
            "description": (
                f"{title_kw} for {marketplace} buyers. This draft was generated without "
                "product specifications, so specification fields are marked [ADD] for the "
                "seller to complete; market price references and compliance items come "
                "from the mission report and are not part of the customer-facing copy."
            ),
        }
        generated_by = "template(合规骨架；[ADD] 处需卖家补齐规格)"

    export = {
        "keyword": keyword,
        "marketplace": marketplace,
        "copy": copy,
        "generated_by": generated_by,
        "facts_used": facts,
        "provenance_ids": item_ids,
    }
    return ToolOutcome(
        True, "done", None,
        f"Listing 包生成（{generated_by}）：标题 + {len(copy['bullets'])} 卖点，"
        f"价格带按 {band_currency} 口径回填，事实字段来自 {len(item_ids)} 个存证数据点",
        export,
    )
