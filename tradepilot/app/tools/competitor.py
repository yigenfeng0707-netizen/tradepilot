# Copyright 2026 TradePilot project. Apache-2.0.
"""market_scan：市场扫描工具——多源适配，真实商品卡全部带来源存证。"""
from __future__ import annotations

import json
from typing import Any

from .. import provenance
from .executor import ToolOutcome
from .sources import SOURCES, scan


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    keyword = str(tool_input.get("keyword", "") or tool_input.get("query", "")).strip()
    if not keyword:
        return ToolOutcome(False, "error", None, "缺少 keyword（目标品类关键词）")
    source = str(tool_input.get("source", "dangdang")).strip().lower()
    limit = max(3, min(int(tool_input.get("limit", 8)), 12))

    try:
        result = await scan(source, keyword, limit)
    except ValueError as exc:
        return ToolOutcome(False, "error", None, str(exc), {"source": source})
    except Exception as exc:  # noqa: BLE001 — 执行器兜底之外的网络异常统一口径
        return ToolOutcome(False, "error", None,
                           f"{source} 抓取失败：{type(exc).__name__}: {exc}")

    items = result["items"]
    for item in items:
        item["currency"] = result["currency"]
        item["source"] = source
        item["provenance_id"] = provenance.record(
            "item", result["source_url"], item,
            raw_text=json.dumps(item, sort_keys=True),
        )
    prices = sorted(i["price"] for i in items)
    band = {"min": prices[0], "median": prices[len(prices) // 2], "max": prices[-1]}
    return ToolOutcome(
        True, "done", None,
        f"真实采集 {len(items)} 条「{keyword}」（{result['source_label']}），"
        f"价格带 {band['min']:.2f}–{band['max']:.2f} {result['currency']}，全部带来源存证",
        {"keyword": keyword, "source": source, "source_url": result["source_url"],
         "channel": result["channel"], "currency": result["currency"],
         "items": items, "price_band": band},
    )
