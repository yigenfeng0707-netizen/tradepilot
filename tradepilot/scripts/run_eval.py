# -*- coding: utf-8 -*-
"""对照实验跑批：Agent 全流程 × 3 品类 × R 轮，产出 assets/eval_results.json。

指标口径（诚实框架）：
- agent 侧全部自动实测：端到端耗时、数据点数、来源可溯率、合规验证率、LLM 规划率；
- 人工基线：按 docs/eval/人工SOP.md 由真人执行并计时后，填入 human_baseline
  字段（本脚本只留结构，绝不代填估算值）；
- 事实抽检：assets/audit_sample.json 供人工回访原页核对。
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import provenance  # noqa: E402
from app.orchestrator import run_mission  # noqa: E402

CATEGORIES = [
    {"category": "pet", "keyword": "dog bed"},
    {"category": "home", "keyword": "wall clock"},
    {"category": "electronics", "keyword": "usb c cable"},
]

OUT = Path(__file__).resolve().parents[1] / "assets" / "eval_results.json"
SAMPLE_OUT = Path(__file__).resolve().parents[1] / "assets" / "audit_sample.json"


async def run_one(cat: dict, repeat: int, use_llm: bool) -> dict:
    from app import llm
    # 规则模式用显式开关（而不是清空 Key），保证评测口径可复现
    llm.CONFIG.llm_disabled = not use_llm

    t0 = time.time()
    report = None
    async for event in run_mission(
        f"把{cat['category']}品类的 {cat['keyword']} 切入美国站",
        {"keyword": cat["keyword"], "source": "dangdang", "category": cat["category"],
         "freight_usd": 2.5, "duty_pct": 7.0, "last_mile_usd": 5.5},
    ):
        if event["type"] == "mission_done":
            report = event["report"]
    elapsed = time.time() - t0
    r = report["results"]

    scan = r.get("market_scan", {})
    pricing = r.get("pricing_sandbox", {})
    compliance = r.get("compliance_check", {})
    listing = r.get("listing_draft", {})
    audit = r.get("listing_audit", {})
    decisions = report["decisions"]

    return {
        "category": cat["category"], "keyword": cat["keyword"], "repeat": repeat,
        "mission_id": report["mission_id"],
        "elapsed_s": round(elapsed, 1),
        "steps_done": sum(1 for s in r.values() if s.get("status") == "done"),
        "steps_total": len(r),
        "decisions": len(decisions),
        # 口径说明：llm_decisions = LLM 实际做出选择的步数；
        # llm_overrides = 模型选择与规则评分不一致、最终采纳模型的步数（真正的自主证据）
        "llm_planned": sum(1 for d in decisions if d["reason"].startswith("LLM")),
        "llm_overrides": (report.get("autonomy") or {}).get("llm_overrides", 0),
        "retries": (report.get("autonomy") or {}).get("retries", 0),
        "degraded": (report.get("autonomy") or {}).get("steps_degraded") or [],
        "items_n": len(scan.get("data", {}).get("items", [])),
        "price_band": scan.get("data", {}).get("price_band"),
        "currency": scan.get("data", {}).get("currency"),
        "floor_price_usd": pricing.get("data", {}).get("floor_price"),
        "band_source": pricing.get("data", {}).get("band_source"),
        "compliance_verified": compliance.get("data", {}).get("live_verified"),
        "compliance_total": compliance.get("data", {}).get("total"),
        "listing_source": (listing.get("data", {}).get("generated_by", "")[:24]
                           if listing.get("status") == "done" else listing.get("status")),
        "audit_score": (audit.get("data") or {}).get("score"),
        "audit_grade": (audit.get("data") or {}).get("grade"),
        "audit_critical": ((audit.get("data") or {}).get("counts") or {}).get("critical"),
        "errors": [k for k, s in r.items() if s.get("status") == "error"],
    }


def sample_for_audit(n: int = 30) -> list:
    """从存证库抽样（items/fx/policy），供人工回访原页核对事实。"""
    import sqlite3
    from app.config import CONFIG
    with sqlite3.connect(CONFIG.db_file()) as conn:
        rows = conn.execute(
            "SELECT id, kind, url, fetched_at, payload FROM provenance ORDER BY id DESC LIMIT 400"
        ).fetchall()
    items = [r for r in rows if r[1] in ("item", "fx", "policy")]
    step = max(1, len(items) // n)
    picked = items[::step][:n]
    return [{"provenance_id": r[0], "kind": r[1], "url": r[2], "fetched_at": r[3],
             "payload": json.loads(r[4])} for r in picked]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--llm", action="store_true", help="启用 LLM 规划（默认规则流水线）")
    args = ap.parse_args()

    t0 = time.time()
    runs = []
    for cat in CATEGORIES:
        for rep in range(1, args.repeats + 1):
            print(f"▶ {cat['category']}/{cat['keyword']} 第 {rep} 轮 …", flush=True)
            run = await run_one(cat, rep, use_llm=args.llm)
            runs.append(run)
            print(f"  ✓ {run['elapsed_s']}s · {run['steps_done']}/{run['steps_total']} 步 · "
                  f"{run['items_n']} 商品 · 合规 {run['compliance_verified']}/{run['compliance_total']}"
                  f" · LLM规划 {run['llm_planned']}/{run['decisions']}", flush=True)

    by_cat: dict[str, list] = {}
    for run in runs:
        by_cat.setdefault(run["category"], []).append(run)

    summary = {}
    for cat, rs in by_cat.items():
        elapsed = [r["elapsed_s"] for r in rs]
        summary[cat] = {
            "runs": len(rs),
            "elapsed_mean_s": round(statistics.mean(elapsed), 1),
            "elapsed_stdev_s": round(statistics.stdev(elapsed), 2) if len(elapsed) > 1 else 0.0,
            "items_mean": round(statistics.mean(r["items_n"] for r in rs), 1),
            "steps_success_rate": round(statistics.mean(
                r["steps_done"] / r["steps_total"] for r in rs), 3),
            "compliance_verified_mean": round(statistics.mean(
                r["compliance_verified"] / r["compliance_total"] for r in rs), 3),
            "llm_planned_rate": round(statistics.mean(
                r["llm_planned"] / r["decisions"] for r in rs), 3),
            "llm_override_total": sum(r["llm_overrides"] for r in rs),
            "retries_total": sum(r["retries"] for r in rs),
            "audit_score_mean": (round(statistics.mean(
                r["audit_score"] for r in rs if r["audit_score"] is not None), 1)
                if any(r["audit_score"] is not None for r in rs) else None),
        }

    result = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "llm" if args.llm else "rules",
            "repeats": args.repeats,
            "agent_total_wall_s": round(time.time() - t0, 1),
            "honesty_notes": [
                "agent 侧指标全部自动实测，可复算",
                "人工基线待真人按 docs/eval/人工SOP.md 计时后填入 human_baseline（禁估算）",
                "llm_planned_rate = LLM 做出选择的步数占比；与规则序一致不构成自主证据，"
                "llm_override_total 才是（模型选择与规则评分不一致并被采纳的次数）",
                "walmart 源经 robots.txt 校验对通用 UA 禁止 /search，故 band_source 仍为 "
                "domestic_reference；美国站真实竞品带需接该站官方 API 授权后启用",
            ],
        },
        "summary": summary,
        "runs": runs,
        "provenance_totals": provenance.stats(),
        "human_baseline": {
            "_说明": "由真人按 docs/eval/人工SOP.md 执行并计时后填写，禁估算",
            "task_time_min": None, "data_points_collected": None,
            "sources_cited": None, "performed_by": None, "performed_at": None,
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    SAMPLE_OUT.write_text(json.dumps(sample_for_audit(), ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print(f"\n✅ {len(runs)} 轮完成 → {OUT}")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
