# -*- coding: utf-8 -*-
"""端到端冒烟（多源适配版）：market_scan(当当真实价) → fx_rate(现场汇率)
→ pricing_sandbox → compliance_check → listing_draft，另验证来源闸门。
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.orchestrator import build_registry  # noqa: E402


async def main() -> None:
    reg = build_registry()

    # 1. 真实市场扫描（当当：CNY）
    out = await reg.execute("market_scan", {"keyword": "dog bed", "source": "dangdang",
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
        print("   建议:", priced.data["suggestion"])
        print("   口径:", priced.data["band_source"])

    # 5. 合规体检
    comp = await reg.execute("compliance_check", {"category": "pet"})
    print("[compliance]", comp.status, "|", comp.summary)

    # 6. Listing
    lst = await reg.execute("listing_draft", {"item_ids": ids, "keyword": "dog bed",
                                              "goal": "list dog beds in the US"})
    print("[listing]", lst.status, "|", lst.summary)
    if lst.status == "done":
        print("   title:", lst.data["copy"]["title"][:80])

    from app import provenance
    print("[provenance]", provenance.stats())


asyncio.run(main())
