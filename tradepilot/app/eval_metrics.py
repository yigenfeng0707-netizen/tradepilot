# Copyright 2026 TradePilot project. Apache-2.0.
"""评测口径的纯函数层（参赛方案 §四 五维度里可由脚本实测的部分）。

为什么单独一层：这些数字要进 BP 和 `eval_results.json`，必须能被离线单测复算，
而不是埋在跑批脚本里靠"跑一次看看"。跑批脚本只负责取数与落盘。

三条硬规矩（与作品三条红线一致）：
- **算不了就 null**：需要真人到场的项（人工基线、事实抽检回访）一律返回 None，
  绝不用估算占位——一旦写进 JSON 就再也分不清哪个是实测、哪个是编的；
- **分母要能自证**：来源可溯率的分母只统计"界面上真会展示给评委看的事实"，
  派生值（价格带中位数、净利率）与 AI 生成文案单列，不混进分母凑 100%；
- **判定回查存证内容**：SQL 层的 NOT NULL 只能保证写入时不为空，保证不了
  "这条事实今天还能回访"，所以逐条按 id 回查，缺 url / 时间戳 / 内容 hash
  任一项都算不可溯。
"""
from typing import Any, Callable, Iterable

# 合规三态里只有这两档真落了存证（tools/compliance.py：拦截页/空壳入库等于给假证据盖章）
_STORED_VERIFICATIONS = ("clause_matched", "page_only")


def traceable(pid: Any, resolve: Callable[[int], dict | None]) -> bool:
    """一条存证是否真的可溯源：记录在册，且 url / 抓取时间戳 / 内容 hash 三项齐。"""
    if not isinstance(pid, int):
        return False
    rec = resolve(pid)
    if not rec:
        return False
    return all(bool(rec.get(k)) for k in ("url", "fetched_at", "content_hash"))


def fact_accounting(report: dict, resolve: Callable[[int], dict | None]) -> dict:
    """来源可溯率：界面上展示的事实里，多少条能回查到可回访的存证。

    分母口径（可直接写进 BP）：竞品条目逐条 + 现场汇率 + 已落存证的合规条款行。
    派生值与 AI 文案不进分母，另列计数；未取得正文的条款行也不进分母，
    但计入 `compliance_unverified`，避免"把没验证的东西从账上抹掉"。
    """
    results = report.get("results") or {}
    facts: list[dict[str, Any]] = []
    excluded = {"derived_figures": 0, "ai_generated_fields": 0,
                "compliance_unverified": 0}

    scan = (results.get("market_scan") or {}).get("data") or {}
    for item in scan.get("items") or []:
        facts.append({"kind": "item", "label": (item.get("title") or "")[:60],
                      "provenance_id": item.get("provenance_id")})
    if scan.get("items"):
        excluded["derived_figures"] += 1          # 价格带 min/median/max

    fx = (results.get("fx_rate") or {}).get("data") or {}
    if fx.get("provenance_id"):
        facts.append({"kind": "fx", "label": f"{fx.get('base')}/{fx.get('quote')}",
                      "provenance_id": fx["provenance_id"]})

    comp = (results.get("compliance_check") or {}).get("data") or {}
    for chk in comp.get("checks") or []:
        if chk.get("verification") in _STORED_VERIFICATIONS:
            facts.append({"kind": "clause",
                          "label": chk.get("authority") or chk.get("item") or "",
                          "provenance_id": chk.get("provenance_id")})
        else:
            excluded["compliance_unverified"] += 1

    if (results.get("pricing_sandbox") or {}).get("data"):
        excluded["derived_figures"] += 2          # 报价下限 + 敏感性表
    if (results.get("keyword_plan") or {}).get("data"):
        excluded["derived_figures"] += 1          # 词频/骨架由真实标题派生

    listing = (results.get("listing_draft") or {}).get("data") or {}
    if listing.get("copy"):
        excluded["ai_generated_fields"] += 1

    bad = [f for f in facts if not traceable(f["provenance_id"], resolve)]
    total = len(facts)
    return {
        "facts_total": total,
        "facts_traceable": total - len(bad),
        "source_traceable_rate": round((total - len(bad)) / total, 4) if total else None,
        "untraceable": bad,
        "excluded": excluded,
        "listing_labelled": bool(listing.get("generated_by")) if listing else None,
    }


def spot_check_sheet(records: Iterable[dict]) -> list[dict]:
    """事实抽检表：脚本只备好回访地址与原始 payload，`human_verified` 一律留 null。"""
    return [{
        "provenance_id": r.get("id"), "kind": r.get("kind"), "url": r.get("url"),
        "fetched_at": r.get("fetched_at"), "content_hash": r.get("content_hash"),
        "payload": r.get("payload"),
        "human_verified": None, "human_note": "",
    } for r in records]


def spot_check_accuracy(sheet: list[dict]) -> dict:
    """抽检准确率：真人填完才出数；未填完返回 null 并如实报进度（agent 自检不自证）。"""
    total = len(sheet)
    filled = [r for r in sheet if r.get("human_verified") is not None]
    if not total:
        return {"accuracy": None, "checked": 0, "total": 0, "reason": "抽检表为空"}
    if len(filled) < total:
        return {"accuracy": None, "checked": len(filled), "total": total,
                "reason": f"真人回访未完成（{len(filled)}/{total}）——按红线禁估算"}
    yes = sum(1 for r in filled if r.get("human_verified") is True)
    return {"accuracy": round(yes / total, 4), "checked": total, "total": total,
            "reason": "已全量人工回访核对"}


def labor_cost_equiv(human_baseline: dict | None, agent_elapsed_s: float,
                     hourly_cny: float) -> dict:
    """人工成本折算：人工基线没由真人掐表就返回 null，不按"经验一小时"估。"""
    minutes = (human_baseline or {}).get("task_time_min")
    if not minutes or not hourly_cny or float(hourly_cny) <= 0:
        return {"labor_cost_equiv_cny": None, "automation_rate": None,
                "reason": "human_baseline.task_time_min 未由真人掐表填写（禁估算）"}
    human_s = float(minutes) * 60.0
    agent_s = float(agent_elapsed_s or 0.0)
    return {
        "labor_cost_equiv_cny": round(human_s / 3600.0 * float(hourly_cny), 2),
        "agent_time_cost_cny": round(agent_s / 3600.0 * float(hourly_cny), 2),
        "automation_rate": round(1.0 - agent_s / human_s, 4) if human_s > 0 else None,
        "hourly_cny": float(hourly_cny),
        "human_minutes": float(minutes),
        "reason": "时薪为入参（--hourly-cny）而非实测值，BP 引用时须同时标注",
    }



