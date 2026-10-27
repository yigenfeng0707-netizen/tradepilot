# Copyright 2026 TradePilot project. Apache-2.0.
"""fx_rate：现场真实汇率（Frankfurter，欧洲央行参考汇率，免费无 Key）。

汇率作为独立数据点入库存证——定价沙盘做币种折算时必须引用存证的汇率，
不允许模型或代码内嵌"经验汇率"。
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import provenance
from .executor import ToolOutcome

FX_URL = "https://api.frankfurter.dev/v1/latest?base={base}&symbols={quote}"


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    base = str(tool_input.get("base", "USD")).upper()
    quote = str(tool_input.get("quote", "CNY")).upper()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(FX_URL.format(base=base, quote=quote))
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(False, "error", None,
                           f"汇率源不可达（{type(exc).__name__}: {exc}）——"
                           "不使用内嵌经验汇率，宁可不折算。")
    rate = payload.get("rates", {}).get(quote)
    if not rate:
        return ToolOutcome(False, "error", None, f"汇率源未返回 {base}/{quote} 价格")
    rate = float(rate)
    pid = provenance.record("fx", str(resp.url), {"base": base, "quote": quote, "rate": rate})
    return ToolOutcome(
        True, "done", None,
        f"现场汇率 {base}/{quote} = {rate:.4f}（来源已存证 #{pid}）",
        {"base": base, "quote": quote, "rate": rate, "provenance_id": pid},
    )
