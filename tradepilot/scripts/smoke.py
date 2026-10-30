# -*- coding: utf-8 -*-
"""端到端冒烟：market_scan(当当真实价) → fx_rate → pricing → compliance
→ keyword_plan → listing_draft → listing_audit，另验证来源闸门与 robots 闸门。

用法：python scripts/smoke.py
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.orchestrator import build_registry  # noqa: E402


async def main() -> None:
    reg = build_registry()

    # 1. 真实市场扫描（当当：CNY；category 用于跨品类清洗，keyword_cn 指定源站检索词）
    out = await reg.execute("market_scan", {"keyword": "dog bed", "keyword_cn": "",
                                            "source": "dangdang", "category": "pet",
                                            "limit": 6})
    print("[market_scan]", out.status, "|", out.summary)
    if out.status != "done":
        print("  data:", out.data)
        return
    for it in out.data["items"][:3]:
        print(f"   · ¥{it['price']:.2f}  {it['title'][:52]}  [存证#{it['provenance_id']}]")
    ids = [i["provenance_id"] for i in out.data["items"]]

    # 2. 来源闸门：编造 id 必须被拦截
    fake = await reg.execute("pricing_sandbox", {"item_ids": [999001, 999002]})
    print("[gate-test]", fake.status, "|", fake.summary)

    # 3. 现场汇率
    fx = await reg.execute("fx_rate", {"base": "USD", "quote": "CNY"})
    print("[fx_rate]", fx.status, "|", fx.summary)
    fx_id = fx.data.get("provenance_id") if fx.status == "done" else None

    # 4. 定价沙盘（真实 id + 现场汇率接线）
    priced = await reg.execute("pricing_sandbox", {
        "item_ids": ids, "fx_id": fx_id,
        "freight_usd": 2.5, "duty_pct": 7.0, "last_mile_usd": 5.5,
        "commission_pct": 15.0, "target_margin_pct": 20.0,
    })
    print("[pricing]", priced.status, "|", priced.summary)
    if priced.status == "done":
        print("   建议:", priced.data["suggestion"]["positioning"])
        print("   口径:", priced.data["band_source"])

    # 5. 合规体检
    comp = await reg.execute("compliance_check", {"category": "pet"})
    print("[compliance]", comp.status, "|", comp.summary)

    # 6. 关键词层（真实竞品标题词频 → Search Terms）
    kw = await reg.execute("keyword_plan", {"item_ids": ids, "keyword": "dog bed"})
    print("[keyword_plan]", kw.status, "|", kw.summary)
    if kw.status == "done":
        print("   ST:", kw.data["search_terms"][:100])

    # 7. Listing（消费关键词层 + 定价/合规事实面）
    lst = await reg.execute("listing_draft", {
        "item_ids": ids, "keyword": "dog bed", "marketplace": "US",
        "goal": "list dog beds in the US",
        "keyword_plan": kw.data if kw.status == "done" else {},
        "context": {k: v.data for k, v in
                    (("pricing_sandbox", priced), ("compliance_check", comp))},
    })
    print("[listing]", lst.status, "|", lst.summary)

    # 8. 文案审计
    if lst.status == "done":
        aud = await reg.execute("listing_audit", {
            "copy": lst.data["copy"], "keyword": "dog bed", "marketplace": "US",
            "category": "pet", "generated_by": lst.data["generated_by"],
        })
        print("[listing_audit]", aud.status, "|", aud.summary)

    from app import provenance
    print("[provenance]", provenance.stats())


asyncio.run(main())
