# Copyright 2026 TradePilot project. Apache-2.0.
"""Listing 工坊：从真实数据组织上架文案包。

- 有 LLM：模型只做英文文案组织（facts 由服务端给定，禁编造）；
- 无 LLM：结构化模板生成，逐条复用真实竞品价格带与合规要点；
- 输出中 AI 生成内容显式标注（generated_by: llm/template），一切数字仍服务端回填。
"""
from __future__ import annotations

from typing import Any

from .. import llm, provenance
from .executor import ToolOutcome

TEMPLATE_TITLE = "{keyword} | Durable Everyday {category_cap} for Home & Travel"
TEMPLATE_BULLET = "Positioned against {n} live market listings priced {sym}{band_min:.2f}-{sym}{band_max:.2f}"


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    item_ids = [int(i) for i in (tool_input.get("item_ids") or [])]
    if not item_ids:
        return ToolOutcome(False, "error", None, "缺少 item_ids（须来自 market_scan）")
    items = [p["payload"] for p in (provenance.get(i) for i in item_ids)
             if p and p["kind"] == "item"]
    if not items:
        return ToolOutcome(False, "error", None, "存证中无可用商品数据点")
    currency = items[0].get("currency", "USD")
    sym = "US$" if currency == "USD" else "¥"
    prices = sorted(float(i["price"]) for i in items)
    band = {"min": prices[0], "max": prices[-1], "median": prices[len(prices) // 2]}

    keyword = str(tool_input.get("keyword", "")).strip() or items[0]["title"][:40]
    goal = str(tool_input.get("goal", "list this product overseas"))

    facts = {
        "keyword": keyword,
        "reference_price_band": {**band, "currency": currency},
        "item_n": len(items),
        "sample_titles": [i["title"] for i in items[:3]],
    }

    generated_by = "template"
    copy = None
    if llm.available():
        copy = await llm.write_listing(goal, facts)
        if copy:
            generated_by = "llm(基于存证事实；价格等数字以服务端回填为准)"
    if copy is None:
        cat = keyword.split()[0].capitalize() if keyword.split() else "Product"
        copy = {
            "title": TEMPLATE_TITLE.format(keyword=keyword[:60], category_cap=cat),
            "bullets": [
                TEMPLATE_BULLET.format(band_min=band["min"], band_max=band["max"],
                                       n=len(items), sym=sym),
                "Median reference price {sym}{:.2f} — see pricing sandbox for the costed floor.".format(band["median"], sym=sym),
                "Compliance checklist attached separately (authorities cited per item).",
            ],
            "description": (
                f"Everyday {cat.lower()} positioned against {len(items)} live market listings "
                f"priced {sym}{band['min']:.2f}-{sym}{band['max']:.2f}. "
                "Costs, compliance items and sources are documented in the mission report."
            ),
        }

    export = {
        "keyword": keyword,
        "copy": copy,
        "generated_by": generated_by,
        "facts_used": facts,
        "provenance_ids": item_ids,
    }
    return ToolOutcome(
        True, "done", None,
        f"Listing 包生成（{generated_by}）：标题 + {len(copy['bullets'])} 卖点，"
        f"事实字段全部回填自 {len(item_ids)} 个存证数据点",
        export,
    )
