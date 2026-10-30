# Copyright 2026 TradePilot project. Apache-2.0.
"""定价沙盘：确定性计算器（零 LLM、零内嵌汇率、零猜测）。

数据流：market_scan 的真实商品卡（item_ids，过来源闸门）+ fx_rate 的现场汇率
（fx_id，CNY 计价时必填）→ 到岸成本 → 报价下限 → 对照价格带。

诚实口径：
- 价格带来源分两种，输出里明确标注：us_retail（真实美国站竞品价）/
  domestic_reference（现场汇率折算的国内零售参考价——美国站数据在当前网络
  不可达时的保守基准，部署态自动升级）；
- 平台佣金/关税是参数不是事实声明；
- 每个数字可复算，汇率引用存证。
"""
from __future__ import annotations

from typing import Any

from .. import provenance
from .executor import ToolOutcome


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    item_ids = [int(i) for i in (tool_input.get("item_ids") or [])]
    if not item_ids:
        return ToolOutcome(False, "error", None, "缺少 item_ids（须来自 market_scan）")
    items = [p["payload"] for p in (provenance.get(i) for i in item_ids)
             if p and p["kind"] == "item"]
    if len(items) < 2:
        return ToolOutcome(False, "error", None, "存证中的可用商品数据点不足 2 条")

    usd_prices = [float(i["price"]) for i in items if i.get("currency") == "USD"]
    cny_prices = [float(i["price"]) for i in items if i.get("currency") == "CNY"]

    rate: float | None = None
    fx_id = tool_input.get("fx_id")
    explicit_cost = tool_input.get("unit_cost_cny") is not None
    # CNY 计价数据 或 显式 CNY 采购价，都必须有现场汇率——绝不内嵌经验汇率
    if cny_prices or explicit_cost:
        if not fx_id:
            return ToolOutcome(
                False, "error", None,
                "涉及 CNY 采购成本但缺少 fx_id——折算必须引用现场汇率存证，拒绝内嵌汇率。")
        fx_rec = provenance.get(int(fx_id))
        if not fx_rec or fx_rec["kind"] != "fx":
            return ToolOutcome(False, "error", None, f"fx_id {fx_id} 不在存证库（来源闸门拦截）")
        rate = float(fx_rec["payload"]["rate"])

    freight = float(tool_input.get("freight_usd", 2.5))       # 头程 USD/件
    last_mile = float(tool_input.get("last_mile_usd", 5.5))   # 尾程 USD/件
    duty_pct = float(tool_input.get("duty_pct", 7.0))         # 关税 %
    commission_pct = float(tool_input.get("commission_pct", 15.0))
    margin_pct = float(tool_input.get("target_margin_pct", 20.0))

    # 采购价：显式参数 > 国内零售参考价中位数 × 成本率（参数假设）
    # 注意：竞品零售价里含对手毛利，直接当采购成本会系统性高估成本、
    # 得出"永远打不出毛利"的错误结论。因此用可调成本率折算，并明确标注是假设。
    unit_cost_cny = tool_input.get("unit_cost_cny")
    cost_ratio = float(tool_input.get("cost_ratio", 0.35))
    cost_basis_note = "参数指定采购价（事实）"
    if unit_cost_cny is None:
        if not cny_prices:
            unit_cost_cny, cost_basis_note = 0.0, "未提供采购价且无 CNY 数据点"
        else:
            median_cny = sorted(cny_prices)[len(cny_prices) // 2]
            unit_cost_cny = median_cny * cost_ratio
            cost_basis_note = (f"零售参考价中位数 ¥{median_cny:.2f} × 成本率 "
                               f"{cost_ratio:.0%} = ¥{unit_cost_cny:.2f}"
                               f"（成本率为参数假设，非事实；建议用真实采购价覆盖）")
    unit_cost_cny = float(unit_cost_cny)
    # rate = 每美元兑 CNY 数（USD/CNY），CNY→USD 折算必须除以汇率
    unit_cost_usd = unit_cost_cny / rate if rate else 0.0

    landed = unit_cost_usd + freight + last_mile + unit_cost_usd * duty_pct / 100.0
    floor = landed / max(0.05, 1 - commission_pct / 100.0 - margin_pct / 100.0)

    if usd_prices:
        band = {"min": min(usd_prices), "median": sorted(usd_prices)[len(usd_prices) // 2],
                "max": max(usd_prices)}
        band_source = "us_retail（真实美国站竞品价）"
        band_currency_note = f"{len(usd_prices)} 条 USD 数据点"
    else:
        band = {"min": min(cny_prices) / rate, "median": sorted(cny_prices)[len(cny_prices) // 2] / rate,
                "max": max(cny_prices) / rate}
        band_source = "domestic_reference（现场汇率折算的国内零售参考价）"
        band_currency_note = (f"{len(cny_prices)} 条 CNY 数据点 × 现场汇率 {rate:.4f}"
                              "——美国站竞品价在当前网络不可达，部署态自动升级为真实竞品带")

    sensitivity = []
    for m in (10.0, 15.0, 20.0, 25.0, 30.0):
        p = landed / max(0.05, 1 - commission_pct / 100.0 - m / 100.0)
        sensitivity.append({"margin_pct": m, "floor_price": round(p, 2),
                            "net_per_unit": round(p * (1 - commission_pct / 100.0) - landed, 2),
                            "below_band": p < band["min"],
                            "in_band": band["min"] <= p <= band["max"]})
    competitive = [s for s in sensitivity if s["in_band"]]
    pick = competitive[0] if competitive else sensitivity[0]
    if competitive:
        positioning = (f"进价格带（对照 {band_source}），"
                       f"净利率 {pick['margin_pct']:.0f}% 切入")
    elif floor > band["max"]:
        positioning = ("报价下限高于价格带上限——该品类在目标站打不出目标毛利，"
                       "需降采购/物流成本或换品，别硬上架")
    else:
        positioning = ("报价下限低于价格带下限——有价格优势，可上探定价换取更高毛利")

    result = {
        "fx_rate": rate, "fx_provenance_id": fx_id,
        "cost_basis_cny": round(unit_cost_cny, 2), "cost_basis_note": cost_basis_note,
        "cost_ratio": cost_ratio,
        "unit_cost_usd": round(unit_cost_usd, 2),
        "landed_cost": round(landed, 2),
        "cost_breakdown": {
            "unit_cost_usd": round(unit_cost_usd, 2), "freight_usd": freight,
            "duty_usd": round(unit_cost_usd * duty_pct / 100.0, 2), "last_mile_usd": last_mile,
            "duty_pct": duty_pct, "commission_pct": commission_pct,
        },
        "price_band": {k: round(v, 2) for k, v in band.items()},
        "band_source": band_source, "band_currency_note": band_currency_note,
        "floor_price": round(floor, 2),
        "suggestion": {
            "price": pick["floor_price"], "margin_pct": pick["margin_pct"],
            "in_band": bool(competitive),
            "positioning": positioning,
        },
        "sensitivity": sensitivity,
        "assumptions": "平台佣金/关税为可调参数；采购价与汇率来自存证数据点；每个数字可复算",
    }
    return ToolOutcome(
        True, "done", None,
        f"定价沙盘：到岸 ${landed:.2f}，报价下限 ${floor:.2f}；对照 {band_source}"
        f"（${band['min']:.2f}–${band['max']:.2f}）→ {result['suggestion']['positioning']}",
        result,
    )
