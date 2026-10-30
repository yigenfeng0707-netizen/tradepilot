# Copyright 2026 TradePilot project. Apache-2.0.
"""market_scan：市场扫描工具——多源适配，真实商品卡全部带来源存证。"""
from __future__ import annotations

import json
from typing import Any

from .. import provenance
from .executor import ToolOutcome
from .sources import is_book, relevant, scan


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名同价去重：搜索结果常见同一商品多条（不同店铺/活动），会重复计入价格带。"""
    seen: set[tuple[str, float]] = set()
    out = []
    for item in items:
        key = (item["title"].strip()[:40], round(float(item["price"]), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    keyword = str(tool_input.get("keyword", "") or tool_input.get("query", "")).strip()
    if not keyword:
        return ToolOutcome(False, "error", None, "缺少 keyword（目标品类关键词）")
    source = str(tool_input.get("source", "dangdang")).strip().lower()
    category = str(tool_input.get("category", "")).strip().lower()
    keyword_cn = str(tool_input.get("keyword_cn", "") or "").strip()
    limit = max(3, min(int(tool_input.get("limit", 8)), 12))

    try:
        result = await scan(source, keyword, limit, keyword_cn=keyword_cn)
    except ValueError as exc:
        return ToolOutcome(False, "error", None, str(exc), {"source": source})
    except Exception as exc:  # noqa: BLE001 — 执行器兜底之外的网络异常统一口径
        return ToolOutcome(False, "error", None,
                           f"{source} 抓取失败：{type(exc).__name__}: {exc}")

    raw_items = result["items"]
    kept = [i for i in raw_items if not is_book(i["title"]) and relevant(i["title"], category)]
    items = _dedupe(kept)
    filtered = len(raw_items) - len(items)
    if len(items) < 3:
        return ToolOutcome(
            False, "error", None,
            f"「{result['keyword_used']}」仅取得 {len(items)} 条可用商品"
            f"（原始 {len(raw_items)} 条，剔除书籍/跨品类/重复 {filtered} 条）——"
            "样本不足 3 条，宁可报错也不凑数。建议改用该站惯用的中文检索词（keyword_cn）。",
            {"source": source, "keyword_used": result["keyword_used"]})
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
        f"真实采集 {len(items)} 条「{result['keyword_used']}」（{result['source_label']}，"
        f"已剔除 {filtered} 条书籍/跨品类/重复结果），"
        f"价格带 {band['min']:.2f}–{band['max']:.2f} {result['currency']}，全部带来源存证",
        {"keyword": keyword, "keyword_used": result["keyword_used"], "source": source,
         "source_url": result["source_url"], "channel": result["channel"],
         "currency": result["currency"], "robots": result.get("robots"),
         "filtered_noise": filtered, "items": items, "price_band": band},
    )
