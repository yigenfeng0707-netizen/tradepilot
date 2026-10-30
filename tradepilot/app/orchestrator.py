# Copyright 2026 TradePilot project. Apache-2.0.
"""任务编排器：单一目标 → 状态驱动的自主规划 → 真实工具执行 → 决策卡实录。

自主性的实现（与旧版"固定序列 + LLM 确认"的关键差异）：
- 下一步由 planner 依据**当前状态**在全部可执行候选里选出，而不是按写死顺序推进；
- LLM 可用时在候选白名单内**真实决策**：模型选的只要合法就采纳，与规则评分不一致
  时以模型为准并在决策卡记录分歧（模型不再只是给规则序盖章）；
- 失败/产出不足 → 换参重试（如切换数据源），重试耗尽 → 依赖步骤降级跳过，
  独立分支继续跑完，任务仍闭环；
- 每一步落一张决策卡：候选集、得分、为什么没选别人、闸门结果、耗时。

所有事实仍来自工具的真实抓取与存证，规划器只决定"做什么、按什么顺序、失败怎么办"。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from . import llm, planner
from .config import CONFIG
from .tools import competitor as market_scan
from .tools import compliance, executor, fx, listing, listing_audit, pricing

# 输入接线表：工具入参 ← 前序步骤产出（同时也是 provenance 的流向）
# 语法：a.b 取字段；a.b[].c 取列表逐项字段；@ctx:step1,step2 打包前序结论为上下文
WIRING: dict[str, dict[str, str]] = {
    "pricing_sandbox": {
        "item_ids": "market_scan.items[].provenance_id",
        "fx_id": "fx_rate.provenance_id",
    },
    "listing_draft": {
        "item_ids": "market_scan.items[].provenance_id",
        "context": "@ctx:pricing_sandbox,compliance_check",
    },
    "listing_audit": {
        "copy": "listing_draft.copy",
        "facts": "listing_draft.facts_used",
        "generated_by": "listing_draft.generated_by",
    },
}


def build_registry() -> executor.Registry:
    reg = executor.Registry()
    for spec in planner.STEPS:
        handler = {
            "market_scan": market_scan.run,
            "fx_rate": fx.run,
            "pricing_sandbox": pricing.run,
            "compliance_check": compliance.run,
            "listing_draft": listing.run,
            "listing_audit": listing_audit.run,
        }[spec.name]
        reg.register(executor.Tool(
            spec.name, spec.purpose, handler,
            provenance_keys=(("item_ids", "fx_id") if spec.name == "pricing_sandbox"
                             else ("item_ids",) if spec.name == "listing_draft" else ()),
        ))
    return reg


def _resolve(state: dict[str, Any], path: str) -> Any:
    """解析接线路径，如 market_scan.items[].provenance_id。"""
    src_step, _, field_path = path.partition(".")
    src = (state.get(src_step) or {}).get("data") or {}
    if not field_path:
        return src
    is_list = "[]." in field_path
    field_name = field_path.split("[].")[-1]
    if is_list:
        return [i[field_name] for i in src.get(field_path.split("[].")[0], []) if field_name in i]
    return src.get(field_path)


def _wired_input(step: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    tool_input = dict(params)
    for key, path in WIRING.get(step, {}).items():
        if path.startswith("@ctx:"):
            steps = path.removeprefix("@ctx:").split(",")
            ctx = {k: (state.get(k) or {}).get("data") for k in steps}
            tool_input[key] = {k: v for k, v in ctx.items() if v}
        else:
            tool_input[key] = _resolve(state, path)
    if step == "listing_draft":
        tool_input.setdefault("keyword", params.get("keyword", ""))
        tool_input.setdefault("goal", params.get("goal", ""))
        tool_input.setdefault("marketplace", params.get("marketplace", "US"))
    if step == "listing_audit":
        tool_input.setdefault("keyword", params.get("keyword", ""))
        tool_input.setdefault("marketplace", params.get("marketplace", "US"))
        tool_input.setdefault("category", params.get("category", ""))
        tool_input.setdefault("search_terms", params.get("search_terms", ""))
        tool_input.setdefault("brand_blocklist", params.get("brand_blocklist") or [])
    return tool_input


async def run_mission(
    goal: str, params: dict[str, Any], emit: Any | None = None
) -> AsyncIterator[dict[str, Any]]:
    """执行一次完整任务；yield 决策卡事件流（最终报告在 mission_done 事件里）。"""
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
    attempts: dict[str, int] = {}
    decisions: list[dict[str, Any]] = []
    llm_overrides = 0

    for _ in range(planner.MAX_STEPS):
        chosen, ranked = planner.pick(state, attempts, goal)
        if chosen is None:
            break

        # ---- 规划：LLM 在合法候选内真实决策，规则评分作为先验与兜底 ----
        reason = f"规则评分：{planner.plan_explain(chosen, ranked)}"
        if llm.available():
            whitelist = planner.build_whitelist(state, attempts, goal)
            plan = await llm.plan_next(goal, [d["tool"] for d in decisions], whitelist)
            if plan and planner.is_legal(plan["next_tool"], state, attempts, goal):
                picked = next(c for c in ranked if c.name == plan["next_tool"])
                if picked.name != chosen.name:
                    llm_overrides += 1
                    reason = (f"LLM 决策：{plan.get('reason', '')}"
                              f"（与规则评分不一致，采纳模型选择；规则首选为 {chosen.name}）")
                else:
                    reason = f"LLM 决策：{plan.get('reason', '')}（与规则评分一致）"
                chosen = picked
            elif plan:
                reason = (f"LLM 建议 {plan.get('next_tool')} 不在当前可执行候选内，"
                          f"按规则评分执行；{planner.plan_explain(chosen, ranked)}")

        spec = planner.SPEC_BY_NAME[chosen.name]
        attempts[chosen.name] = attempts.get(chosen.name, 0) + 1
        step_params = planner.retry_input(chosen.name, spec, params, attempts[chosen.name])
        tool_input = _wired_input(chosen.name, state, step_params)
        outcome = await registry.execute(chosen.name, tool_input)
        state[chosen.name] = outcome.to_json()

        card = {
            "n": len(decisions) + 1,
            "tool": chosen.name,
            "decision": f"自主调用 /{chosen.name}",
            "attempt": attempts[chosen.name],
            "reason": reason,
            "input_keys": sorted(tool_input),
            "candidates": [c.to_json() for c in ranked if c.eligible or c.name == chosen.name],
            "status": outcome.status, "gate": outcome.gate,
            "summary": outcome.summary, "elapsed_s": round(time.time() - started, 1),
        }
        decisions.append(card)
        event = {"type": "decision", **card}
        yield event
        emit_fn(event)

    # 未执行/降级的步骤：显式记账，绝不假装跑过
    not_run = [s.name for s in planner.STEPS if s.name not in state]
    skipped = [s.name for s in planner.STEPS
               if s.name in state and state[s.name].get("status") != "done"]

    report = {
        **header,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - started, 1),
        "decisions": decisions,
        "results": state,
        "autonomy": {
            "steps_executed": len(decisions),
            "steps_not_run": not_run,
            "steps_degraded": skipped,
            "retries": sum(max(0, n - 1) for n in attempts.values()),
            "llm_decisions": sum(1 for d in decisions if d["reason"].startswith("LLM")),
            "llm_overrides": llm_overrides,
        },
        "provenance": None,  # 由调用方在落盘前填 provenance.stats()
    }
    yield {"type": "mission_done", "report": report}
    emit_fn({"type": "mission_done", "mission_id": mission_id})
