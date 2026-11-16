# Copyright 2026 TradePilot project. Apache-2.0.
"""OPC 作战简报：把一次任务报告翻译成「想法→生意」的一人公司叙事。

为什么单独做这一层（而不是塞进 BP 或前端文案）：
    参赛命题是「和 AI 一起，把想法变成生意」。系统其实一直在做这件事，
    但产物叫「上架包」，讲的是工具与字段，评委要的「想法如何变成生意」
    这条主线散落在决策卡里，没人替它串起来。本模块把**同一次任务的真实报告**
    重排成一条可交付的价值链，全程只搬运报告里真实存在的字段。

红线（与全仓一致）：
    1. 不产出任何报告里没有的数字：市场规模、增长率、搜索量、节省百分比一律不写；
    2. 人工基线 / 事实抽检未回填就是 null，简报如实显示「待真人录入」，不估算；
    3. 降级/未执行的步骤如实标注，不假装闭环成功；
    4. 「环节→商业含义」是编辑性映射（如 market_scan → 竞品真实价格带），
       属于解释性知识，与抓取事实分列，不冒充数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# 环节 → 商业含义：编辑性映射（解释这条流水线每一步在回答什么生意问题），
# 不是从数据里读出来的事实。与报告事实分列展示，避免被误读为抓取结果。
STAGE_BUSINESS_MEANING: dict[str, str] = {
    "opportunity_scan": "想法：哪个品类值得先查（本批真实样本观测，非需求量）",
    "market_scan": "市场：竞品真实价格带与在售商品",
    "fx_rate": "汇率：现场真实汇率，用于跨币种折算",
    "pricing_sandbox": "定价：报价下限与毛利敏感性",
    "compliance_check": "合规：能不能卖、要过哪些认证",
    "keyword_plan": "流量：买家在搜什么词",
    "listing_draft": "内容：可直接用的上架文案",
    "listing_audit": "风控：上架前的合规体检",
}


def _status_of(step: str, report: dict[str, Any],
               decision: dict[str, Any] | None = None) -> tuple[str, str]:
    """从报告推导某环节的诚实状态：(状态, 说明)。不猜、不补。

    优先级（已执行步骤以决策卡为准，决策卡才是真正的执行记录）：
      1. autonomy 里的 degraded / not_run 是编排层的诚实记账，最高优先；
      2. 决策卡里带了 status（outcome.status）→ 直接采信，"done" 才算完成；
      3. 没有决策卡（本步没执行）→ 查 results 有没有产物；
      4. 都没有 → "无数据"，不假装完成。

    旧版只看 results[step].data，导致「决策卡明明执行过、只是 results 未随报告带出」
    的步骤被误标"无数据"——真实归档报告 results 是齐的，但评测/裁剪过的输入不齐，
    还原时应以决策卡为准，别把跑过的步骤说成没数据。
    """
    autonomy = report.get("autonomy") or {}
    if step in (autonomy.get("steps_degraded") or []):
        return "降级", "本步执行但未达预期（如实记账，未伪装成功）"
    if step in (autonomy.get("steps_not_run") or []):
        return "未执行", "本步未运行（依赖或条件未满足）"
    if decision is not None:
        raw = decision.get("status")
        if raw:
            return ("完成", "") if raw == "done" else (
                "降级", f"本步决策卡状态为 {raw}（未伪装成功）")
        # 有决策卡即已执行；无 status 字段时按执行过论，不倒查 results（可能未随报告带出）
        return "完成", ""
    data = (report.get("results") or {}).get(step, {}).get("data")
    if data is None:
        return "无数据", "报告里没有本步产物"
    return "完成", ""


def build_opc_brief(report: dict[str, Any]) -> dict[str, Any]:
    """把一次任务报告重排成 OPC「想法→生意」简报（纯函数，无 IO）。

    输入可以是归档任务报告，也可以是 WebSocket 实时报告——两者字段同构。
    """
    autonomy = report.get("autonomy") or {}
    decisions = report.get("decisions") or []
    provenance = report.get("provenance") or {}

    chain: list[dict[str, Any]] = []
    for i, decision in enumerate(decisions, 1):
        step = str(decision.get("tool", ""))
        status, note = _status_of(step, report, decision=decision)
        chain.append({
            "order": i,
            "step": step,
            "business_meaning": STAGE_BUSINESS_MEANING.get(step, "（未登记商业含义的环节）"),
            "status": status,
            "status_note": note,
            "summary": decision.get("summary"),
            "decision_reason": decision.get("reason"),
            "model_involved": bool(decision.get("llm_adopted")),
            "model_overrode_rule": bool(decision.get("llm_rejected")),
            "gate": decision.get("gate"),
        })

    # 未出现在决策里的环节（降级/未执行/条件跳过）也要在价值链里露面，
    # 否则「全链路」就成了只挑跑通的说——把缺口摆出来才是可审计。
    seen = {c["step"] for c in chain}
    for step in STAGE_BUSINESS_MEANING:
        if step in seen:
            continue
        status, note = _status_of(step, report)
        chain.append({
            "order": None,
            "step": step,
            "business_meaning": STAGE_BUSINESS_MEANING[step],
            "status": status,
            "status_note": note,
            "summary": None,
            "decision_reason": None,
            "model_involved": False,
            "model_overrode_rule": False,
            "gate": None,
        })

    # 事实数据点总数：来自报告真实字段，不硬编码。
    facts_total = None
    facts_traceable = None
    trace = provenance.get("traceability") or provenance.get("source_traceability") or {}
    if isinstance(trace, dict):
        facts_total = trace.get("facts_total")
        facts_traceable = trace.get("facts_traceable")

    return {
        "brief_type": "OPC 想法→生意作战简报",
        "mission_id": report.get("mission_id"),
        "goal": report.get("goal"),
        "positioning": "给一人公司（OPC）用的跨境电商 AI 运营部：一句话目标，自主跑完整条出海链路",
        "theme": "和 AI 一起，把想法变成生意",
        "track": "商业生产力",
        "value_chain": chain,
        "run_facts": {
            "steps_executed": autonomy.get("steps_executed"),
            "steps_total_designed": len(STAGE_BUSINESS_MEANING),
            "steps_degraded": autonomy.get("steps_degraded") or [],
            "steps_not_run": autonomy.get("steps_not_run") or [],
            "retries": autonomy.get("retries"),
            "llm_decisions": autonomy.get("llm_decisions"),
            "llm_overrides": autonomy.get("llm_overrides"),
            "elapsed_s": report.get("elapsed_s"),
            "plan_engine": report.get("plan_engine"),
        },
        "provenance_facts": {
            "facts_total": facts_total,
            "facts_traceable": facts_traceable,
            "source_stats": provenance.get("stats") if isinstance(provenance, dict) else None,
        },
        "honesty_notes": [
            "本简报只搬运同一次任务报告里真实存在的字段；不写市场规模、增长率、搜索量等无来源数据。",
            "「环节→商业含义」是解释性映射，不是抓取到的事实。",
            "人工基线耗时与事实抽检准确率需真人按 docs/eval/人工SOP.md 录入，"
            "未录入即为 null（本简报不估算、不自证）。",
            "降级/未执行步骤已如实列出；全链路闭环不等于每步都成功。",
        ],
    }


def export_opc_markdown(brief: dict[str, Any]) -> str:
    """把 OPC 简报渲染成可交付 Markdown（供导出下载）。"""
    chain = brief.get("value_chain") or []
    run = brief.get("run_facts") or {}
    prov = brief.get("provenance_facts") or {}

    def val(v: Any) -> str:
        return "—（待真人录入）" if v is None else str(v)

    lines: list[str] = [
        f"# {brief.get('brief_type', 'OPC 简报')} · {brief.get('mission_id', '')}",
        "",
        f"> 命题：{brief.get('theme', '')}　｜　赛道：{brief.get('track', '')}",
        "",
        f"- 定位：{brief.get('positioning', '')}",
        f"- 本次目标（想法的输入）：{brief.get('goal', '')}",
        f"- 执行：{val(run.get('steps_executed'))}/{run.get('steps_total_designed')} 环节"
        f"　耗时 {val(run.get('elapsed_s'))}s"
        f"　规划引擎 {val(run.get('plan_engine'))}",
        f"- 自主性：LLM 决策 {val(run.get('llm_decisions'))} 次"
        f"　覆盖规则序 {val(run.get('llm_overrides'))} 次"
        f"　重试 {val(run.get('retries'))} 次",
        f"- 事实可溯：{val(prov.get('facts_traceable'))}/{val(prov.get('facts_total'))}"
        f"（口径见 eval_results.json，派生值与 AI 文案不进分母）",
        f"- 降级环节：{', '.join(run.get('steps_degraded') or []) or '无'}"
        f"　未执行：{', '.join(run.get('steps_not_run') or []) or '无'}",
        "",
        "## 想法 → 生意：本次任务的真实价值链",
        "",
        "| # | 环节 | 回答的生意问题 | 状态 | AI 是否参与决策 | 产物摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for c in chain:
        order = c.get("order") if c.get("order") is not None else "—"
        ai = "是" if c.get("model_involved") else "否（规则/未执行）"
        summary = str(c.get("summary") or c.get("status_note") or "—").replace("|", "\\|")[:70]
        lines.append(
            f"| {order} | {c.get('step', '')} | {c.get('business_meaning', '')} | "
            f"{c.get('status', '')} | {ai} | {summary} |")

    lines += ["", "## 口径与红线", ""]
    lines += [f"- {n}" for n in (brief.get("honesty_notes") or [])]
    lines.append("")
    return "\n".join(lines)
