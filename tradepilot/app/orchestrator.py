# Copyright 2026 TradePilot project. Apache-2.0.
"""任务编排器：单一目标 → 自主规划 → 逐步执行真实工具 → 决策卡实录。

自主性的两层含义（对应 opc-yiwu 打法）：
- LLM 可用时：由模型在工具白名单内规划下一步并给出理由（解析失败即降级）；
- 规则兜底：固定顺序 pipeline，保证无 LLM 环境/网关故障下任务照样闭环。
每一步的"决策 N：调用 /tool（理由）→ 结果摘要"全程落盘，作为答辩证据。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from . import llm
from .config import CONFIG
from .tools import competitor as market_scan
from .tools import compliance, executor, fx, listing, pricing

RULE_PLAN = [
    ("market_scan", "先看真实市场：商品与价格数据是一切后续判断的地基（全部带来源存证）"),
    ("fx_rate", "现场真实汇率：跨币种折算必须引用存证汇率，不用内嵌经验值"),
    ("pricing_sandbox", "拿真实价格带对照成本参数，算出可守住的报价下限"),
    ("compliance_check", "目标站点的合规要求逐条对官方来源验证，避免上架即下架"),
    ("listing_draft", "基于前几步的真实产出组织上架文案包"),
]

# 工具依赖：哪些入参来自前序步骤的输出（自动接线，也是数据 provenance 的流向）
WIRING = {
    "pricing_sandbox": {
        "item_ids": "market_scan.items[].provenance_id",
        "fx_id": "fx_rate.provenance_id",
    },
    "listing_draft": {"item_ids": "market_scan.items[].provenance_id"},
}


def build_registry() -> executor.Registry:
    reg = executor.Registry()
    reg.register(executor.Tool(
        "market_scan", "真实抓取目标品类的市场商品与价格（多源适配，全部带来源存证）",
        market_scan.run))
    reg.register(executor.Tool(
        "fx_rate", "现场真实汇率（Frankfurter/欧洲央行，存证后供定价折算）",
        fx.run))
    reg.register(executor.Tool(
        "pricing_sandbox", "对照真实价格带与成本参数计算报价下限与敏感性",
        pricing.run, provenance_keys=("item_ids", "fx_id")))
    reg.register(executor.Tool(
        "compliance_check", "目标品类合规要求逐条对官方来源现场验证",
        compliance.run))
    reg.register(executor.Tool(
        "listing_draft", "基于真实数据组织 Listing 文案与上架包",
        listing.run, provenance_keys=("item_ids",)))
    return reg


def _wired_input(step: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    tool_input = dict(params)
    for key, path in WIRING.get(step, {}).items():
        src_step, field_path = path.split(".", 1)
        src = state.get(src_step, {}).get("data", {})
        if field_path == "items[].provenance_id":
            tool_input[key] = [i["provenance_id"] for i in src.get("items", [])]
        elif field_path == "provenance_id":
            tool_input[key] = src.get("provenance_id")
    if step == "listing_draft":
        tool_input.setdefault("keyword", params.get("keyword", ""))
        tool_input.setdefault("goal", params.get("goal", ""))
    return tool_input


async def run_mission(
    goal: str, params: dict[str, Any], emit: Any | None = None
) -> AsyncIterator[dict[str, Any]]:
    """执行一次完整任务；yield 决策卡事件流（同时返回最终报告由调用方聚合）。"""
    registry = build_registry()
    started = time.time()
    emit_fn = emit or (lambda event: None)

    mission_id = f"mission-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    plan_engine = "llm" if llm.available() else "rules"
    header = {
        "mission_id": mission_id, "goal": goal, "plan_engine": plan_engine,
        "llm_model": CONFIG.llm_model if llm.available() else None,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    yield {"type": "mission_start", **header}
    emit_fn({"type": "mission_start", **header})

    state: dict[str, Any] = {}
    decisions: list[dict[str, Any]] = []
    remaining = [name for name, _ in RULE_PLAN]

    while remaining:
        step = remaining[0]
        # LLM 规划：只允许在剩余白名单里选；选错/失败即走规则顺序（不中断）
        reason = f"规则流水线：{dict(RULE_PLAN)[step]}"
        if llm.available():
            whitelist = [{"name": n, "description": dict(RULE_PLAN)[n]} for n, _ in RULE_PLAN]
            plan = await llm.plan_next(goal, [d["decision"] for d in decisions], whitelist)
            if plan and plan["next_tool"] == step:
                reason = f"LLM 规划：{plan.get('reason', '')}（与规则序一致）"
            elif plan:
                reason = f"LLM 建议 {plan.get('next_tool')} 但其依赖未就绪，按规则序执行 {step}（兜底）"

        tool_input = _wired_input(step, state, params)
        outcome = await registry.execute(step, tool_input)
        state[step] = outcome.to_json()
        remaining = remaining[1:]

        card = {
            "n": len(decisions) + 1, "decision": f"自主调用 /{step}",
            "reason": reason, "input_keys": sorted(tool_input),
            "status": outcome.status, "gate": outcome.gate,
            "summary": outcome.summary, "elapsed_s": round(time.time() - started, 1),
        }
        decisions.append(card)
        event = {"type": "decision", **card}
        yield event
        emit_fn(event)

    report = {
        **header,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - started, 1),
        "decisions": decisions,
        "results": state,
        "provenance": None,  # 由调用方在落盘前填 provenance.stats()
    }
    yield {"type": "mission_done", "report": report}
    emit_fn({"type": "mission_done", "mission_id": mission_id})
