# Copyright 2026 TradePilot project. Apache-2.0.
"""全链路编排（M2）：把四个产品层 Agent 串成一条可交付的流水线。

设计上只做**编排**，不碰数据：每一步都调用对应 Agent 的 `run()`，
拿它的返回原样放进 `state`。这样单步页与全链路页的口径永远一致——
一旦编排层开始"顺手修一下数据"，两个页面就会开始对不上。

三条硬规矩：

1. **单步失败不拖垮整条链**。四个步骤互相独立（除文案依赖选品结果外），
   所以某一步失败就记 `failed` + 原因，继续跑下一步。整条链报错退出会让
   用户连已经成功的那几步都看不到。

2. **进度必须是真的**。用 async generator 逐步 yield，前端拿到一个事件就
   点亮一个节点。前端自己 `setTimeout` 模拟进度是最容易被看穿的假实现。

3. **总耗时如实上报**，不做"预计 30 秒"这种猜测——单次 LLM 文案就要 60–90 秒，
   整链 2–3 分钟是常态，藏起来只会让用户以为卡死。

步骤依赖：
- market_insight  独立
- smart_selection 独立
- content_generation 需要产品名：显式传入 > 选品 Top1 > 跳过（并说明原因）
- compliance      独立（按目标国家）
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from .. import provenance, store
from ..config import CONFIG
from ..request_context import current_tenant_id
from . import base, compliance, content_generation, market_insight, smart_selection

AGENT = "全链路工作流"

STEP_LABELS = {
    "market_insight": "市场洞察",
    "smart_selection": "智能选品",
    "content_generation": "内容生成",
    "compliance": "合规查询",
}

# 中文步骤标签 → 英文步骤键（归档 autonomy 记账用，保持与 STEP_LABELS 同源）
_LABEL_TO_KEY = {label: key for key, label in STEP_LABELS.items()}


def _step_event(step: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"event": "step", "step": step, "label": STEP_LABELS[step],
            "status": status, **extra}


async def _run_step(coro: Any, label: str, timeout_s: float) -> Any:
    """单步墙钟预算：超时即放弃本步（诚实 fail），绝不挂住整条链。

    为什么要这层：一次合规步内会串行抓多个官方源，每个源各自有 fetch 超时，
    但相加无上限——弱网下单个慢源就能让大屏卡在一个节点十几分钟，
    路演里这就是"演示事故"。预算超时后标注 `step_timeout`，
    链路继续跑其余步骤（与"单步失败不拖垮整条链"的既有规矩一致）。

    已知的取舍：被取消的抓取如果已进入浏览器线程池，该线程仍会跑完
    （同步 API 无法中止），但单线程池保证它不会与新请求并发，
    浏览器上下文在 finally 里回收，无资源泄漏。
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        return base.fail(label, f"步骤超时：墙钟预算 {timeout_s:.0f}s 用尽"
                                "（外部源响应过慢，本步放弃，其余步骤继续）")
    except Exception as exc:  # noqa: BLE001 — 与原口径一致：单步异常不拖垮整条链
        return base.fail(label, f"{type(exc).__name__}: {exc}")


async def run_steps(category: str, marketplace: str = "US", budget: str = "中",
                    platform: str = "shopee", target_language: str = "en",
                    target_country: str = "美国", keyword: str = "",
                    keyword_cn: str = "", product_name: str = "",
                    include_content: bool = True,
                    allow_sample: bool = True,
                    step_timeout_s: float | None = None,
                    ) -> AsyncIterator[dict[str, Any]]:
    """逐步执行，yield 事件。事件类型：step_start / step_done / done。

    step_timeout_s：单步墙钟预算（秒）。None = 读 CONFIG.agent_step_timeout_s。
    超时的步骤记 failed 并附原因，不阻塞后续步骤。
    """
    started = time.monotonic()
    budget_s = float(step_timeout_s if step_timeout_s is not None
                     else CONFIG.agent_step_timeout_s)
    mission_id = store.new_mission_id()
    state: dict[str, Any] = {"category": category, "product_name": product_name}
    completed: list[str] = []
    errors: list[str] = []

    # ---- 1. 市场洞察 ----
    yield _step_event("market_insight", "running")
    t0 = time.monotonic()
    mi = await _run_step(
        market_insight.run(category, marketplace=marketplace,
                           keyword=keyword, keyword_cn=keyword_cn,
                           allow_sample=allow_sample),
        "市场洞察Agent", budget_s)
    dur = round(time.monotonic() - t0, 1)
    if mi.get("success"):
        state["market_insight"] = mi["data"]
        state["market_insight_quality"] = mi.get("data_quality")
        completed.append("market_insight")
        yield _step_event("market_insight", "done", duration=dur,
                          quality=mi.get("data_quality"),
                          summary=mi.get("message", ""), data=mi["data"])
    else:
        state["market_insight"] = None
        errors.append(f"市场洞察：{mi.get('message')}")
        yield _step_event("market_insight", "failed", duration=dur,
                          message=mi.get("message", ""))

    # ---- 2. 智能选品 ----
    yield _step_event("smart_selection", "running")
    t0 = time.monotonic()
    ss = await _run_step(
        smart_selection.run(category, marketplace=marketplace,
                            budget=budget, keyword=keyword,
                            keyword_cn=keyword_cn),
        "智能选品Agent", budget_s)
    dur = round(time.monotonic() - t0, 1)
    if ss.get("success"):
        state["smart_selection"] = ss["data"]
        completed.append("smart_selection")
        yield _step_event("smart_selection", "done", duration=dur,
                          quality=ss.get("data_quality"),
                          summary=ss.get("message", ""), data=ss["data"])
    else:
        state["smart_selection"] = None
        errors.append(f"智能选品：{ss.get('message')}")
        yield _step_event("smart_selection", "failed", duration=dur,
                          message=ss.get("message", ""))

    # ---- 3. 内容生成（依赖产品名）----
    name = (product_name or "").strip()
    name_origin = "用户输入"
    if not name and state.get("smart_selection"):
        recs = state["smart_selection"].get("product_recommendations") or []
        if recs:
            name = str(recs[0].get("product") or "").strip()
            name_origin = "选品 Top1"
    if not include_content:
        yield _step_event("content_generation", "skipped",
                          message="调用方显式跳过（include_content=false）")
    elif not name:
        yield _step_event("content_generation", "skipped",
                          message="没有可用的产品名：选品步骤未产出候选，"
                                  "也未传入 product_name")
    else:
        state["product_name"] = name
        state["product_name_origin"] = name_origin
        yield _step_event("content_generation", "running",
                          message=f"产品名取自{name_origin}：{name}")
        t0 = time.monotonic()
        cg = await _run_step(
            content_generation.run(
                name, category=category, platform=platform,
                target_language=target_language, marketplace=marketplace,
                keyword=keyword),
            "内容生成Agent", budget_s)
        dur = round(time.monotonic() - t0, 1)
        if cg.get("success"):
            state["content_generation"] = cg["data"]
            completed.append("content_generation")
            yield _step_event("content_generation", "done", duration=dur,
                              quality=cg.get("data_quality"),
                              summary=cg.get("message", ""), data=cg["data"])
        else:
            state["content_generation"] = None
            errors.append(f"内容生成：{cg.get('message')}")
            yield _step_event("content_generation", "failed", duration=dur,
                              message=cg.get("message", ""))

    # ---- 4. 合规查询 ----
    yield _step_event("compliance", "running")
    t0 = time.monotonic()
    cp = await _run_step(
        compliance.run(category, target_country=target_country),
        "合规Agent", budget_s)
    dur = round(time.monotonic() - t0, 1)
    if cp.get("success"):
        state["compliance"] = cp["data"]
        completed.append("compliance")
        yield _step_event("compliance", "done", duration=dur,
                          quality=cp.get("data_quality"),
                          summary=cp.get("message", ""), data=cp["data"])
    else:
        state["compliance"] = None
        errors.append(f"合规查询：{cp.get('message')}")
        yield _step_event("compliance", "failed", duration=dur,
                          message=cp.get("message", ""))

    total = round(time.monotonic() - started, 1)
    state["steps_completed"] = completed
    state["errors"] = errors

    # ---- 归档：与 orchestrator 同款，跑过的链路必须留下证据 ----
    # 此前只有大屏 WS 通道会存档，React 主前端走的本链路跑完即散，
    # 「任务可回放」在主链路上是空的，Admin 大屏的 missions_total 也不动。
    # 归档归属同 orchestrator：登录租户写 tenant_id，演示模式为 None。
    archive_path: str | None = None
    archive_error: str | None = None
    try:
        archive_path = store.save_mission(
            {"mission_id": mission_id,
             "agent": AGENT,
             "pipeline": "product_agents",
             "goal": f"全链路工作流：{category} → {target_country}（{marketplace}）",
             "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "elapsed_s": total,
             "plan_engine": "fixed_flow",  # 产品 Agent 全链路是固定四步流，
                                            # 决策卡/自主性记账属于 orchestrator 通道
             "results": {k: v for k, v in state.items() if k in STEP_LABELS},
             "summary": {"steps_completed": len(completed),
                         "total_steps": len(STEP_LABELS),
                         "failed_steps": [e.split("：")[0] for e in errors]},
             "autonomy": {"steps_completed": completed,
                          # errors 条目前缀是中文步骤标签（如"合规查询：…"），
                          # 机器消费侧（opc_brief 映射、missions 摘要）要的是与
                          # STEP_LABELS 一致的英文键——此前直接 split 存中文，
                          # 降级映射对不上，失败步被误标"未执行"。
                          "steps_failed": [
                              _LABEL_TO_KEY.get(e.split("：", 1)[0],
                                                e.split("：", 1)[0])
                              for e in errors]},
             "provenance": provenance.stats()},
            tenant_id=current_tenant_id.get())
    except Exception as exc:  # noqa: BLE001
        archive_error = f"{type(exc).__name__}: {exc}"

    yield {
        "event": "done",
        "mission_id": mission_id,
        "archive_path": archive_path,
        "archive_error": archive_error,
        "state": state,
        "summary": {
            "category": category,
            "product": state.get("product_name") or "",
            "steps_completed": len(completed),
            "total_steps": len(STEP_LABELS),
            "errors": len(errors),
            "duration_seconds": total,
            "failed_steps": [e.split("：")[0] for e in errors],
        },
        "note": (f"总耗时 {total} 秒。四个步骤相互独立，单步失败不影响其余步骤；"
                 "各步骤的 data_quality 单独标注，请分别看待。"),
    }


async def run(category: str, **kwargs: Any) -> dict[str, Any]:
    """非流式入口：跑完整条链，返回与流式相同结构的汇总。"""
    last: dict[str, Any] | None = None
    async for ev in run_steps(category, **kwargs):
        last = ev
    if last is None:                                  # 理论上不可达
        return base.fail(AGENT, "流水线未产出任何事件")
    ok = last["summary"]["steps_completed"] > 0
    return {
        "success": ok,
        "agent": AGENT,
        "pipeline": "cross_border_full",
        "state": last["state"],
        "summary": last["summary"],
        "message": last.get("note", ""),
        "data_quality": None,                         # 逐步标注，不合并成一个
    }
