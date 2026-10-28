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
    if not use_llm:
        llm.CONFIG.llm_api_key = ""  # 规则流水线模式（确定性）

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
    decisions = report["decisions"]

    return {
        "category": cat["category"], "keyword": cat["keyword"], "repeat": repeat,
        "mission_id": report["mission_id"],
        "elapsed_s": round(elapsed, 1),
        "steps_done": sum(1 for s in r.values() if s.get("status") == "done"),
        "steps_total": len(r),
        "decisions": len(decisions),
        "llm_planned": sum(1 for d in decisions if d["reason"].startswith("LLM")),
        "items_n": len(scan.get("data", {}).get("items", [])),
        "price_band": scan.get("data", {}).get("price_band"),
        "currency": scan.get("data", {}).get("currency"),
        "floor_price_usd": pricing.get("data", {}).get("floor_price"),
        "band_source": pricing.get("data", {}).get("band_source"),
        "compliance_verified": compliance.get("data", {}).get("live_verified"),
        "compliance_total": compliance.get("data", {}).get("total"),
        "listing_source": (listing.get("data", {}).get("generated_by", "")[:24]
                           if listing.get("status") == "done" else listing.get("status")),
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
                "美国站竞品带（walmart 源）在部署态启用后，band_source 将从 "
                "domestic_reference 升级为 us_retail",
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
