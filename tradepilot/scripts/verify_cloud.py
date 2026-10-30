# -*- coding: utf-8 -*-
"""云端端到端验证：通过 WebSocket 触发一次完整任务到魔搭部署。"""
import asyncio
import json
import sys

import httpx
import websockets

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
WS_URL = "wss://fygrobert-tradepilot.ms.show/ws/mission"


async def main() -> None:
    print("=== 云端 TradePilot 端到端验证 ===")
    print(f"WebSocket: {WS_URL}")

    task_payload = {
        "goal": "把宠物用品切入美国站，先看真实市场再定价格与合规",
        "keyword": "dog bed",
        "source": "dangdang",
        "category": "pet",
        "freight_usd": 2.5,
        "duty_pct": 7.0,
        "last_mile_usd": 5.5,
        "unit_cost_cny": None,
        "target_margin_pct": 20.0,
    }

    try:
        async with websockets.connect(WS_URL, extra_headers={"User-Agent": UA}) as ws:
            print("WebSocket 已连接，发送任务…")
            await ws.send(json.dumps(task_payload))

            # 接收事件流
            steps_done = 0
            # 步数不写死：写死成 5 会让"旧镜像只跑 5 步"看起来像验证通过。
            # 上限取本地规划器的硬上限，实际以 mission_done 收尾。
            steps_total = 14
            results = {}
            for i in range(steps_total + 2):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=180)
                    event = json.loads(msg)
                    etype = event.get("type")
                    if etype == "decision":
                        steps_done += 1
                        status = event.get("status", "?")
                        tool = event.get("decision", "?")
                        summary = event.get("summary", "")[:80]
                        print(f"  决策{steps_done}: {tool} [{status}] {summary}")
                    elif etype == "mission_done":
                        results = event.get("report", {}).get("results", {})
                        print("  ✓ 任务完成")
                        break
                    elif etype == "error":
                        print(f"  ✗ 任务错误: {event.get('summary', '')}")
                        break
                    else:
                        print(f"  事件: {etype}")
                except asyncio.TimeoutError:
                    print(f"  ⏱ 等待第 {i} 个事件超时")
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  ✗ 接收错误: {type(e).__name__}: {e}")
                    break

            # 汇总结果
            print("\n=== 云端任务结果汇总 ===")
            market = results.get("market_scan", {})
            if market.get("status") == "done":
                items = market.get("data", {}).get("items", [])
                print(f"  市场扫描: {len(items)} 个商品（来源: {market.get('data', {}).get('source_url', '?')}")
                for it in items[:3]:
                    print(f"    · ¥{it['price']:.2f}  {it['title'][:55]}")
            else:
                print(f"  市场扫描: {market.get('status')} - {market.get('summary', '')[:60]}")

            pricing = results.get("pricing_sandbox", {})
            if pricing.get("status") == "done":
                d = pricing.get("data", {})
                print(f"  定价沙盘: 到岸 ${d.get('landed_cost', 0):.2f} → 下限 ${d.get('floor_price', 0):.2f}")
                print(f"    对照口径: {d.get('band_source', '?')}")
                print(f"    建议: {d.get('suggestion', {}).get('positioning', '?')[:60]}")
            else:
                print(f"  定价沙盘: {pricing.get('status')}")

            compliance = results.get("compliance_check", {})
            if compliance.get("status") == "done":
                d = compliance.get("data", {})
                print(f"  合规体检: {d.get('live_verified', 0)}/{d.get('total', 0)} 条来源验证")
            else:
                print(f"  合规体检: {compliance.get('status')}")

            listing = results.get("listing_draft", {})
            if listing.get("status") == "done":
                d = listing.get("data", {})
                print(f"  Listing: {d.get('generated_by', '?')}")
                print(f"    标题: {d.get('copy', {}).get('title', '')[:60]}")
            else:
                print(f"  Listing: {listing.get('status')}")

            # 健康检查（provenance 应该已填充）
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://fygrobert-tradepilot.ms.show/api/health",
                                     headers={"User-Agent": UA})
                prov = r.json().get("provenance", {})
                print(f"\n  云端来源存证: {prov.get('total', 0)} 条 / {prov.get('distinct_sources', 0)} 个来源")

            print("\n✅ 云端端到端验证完成")
    except Exception as e:  # noqa: BLE001
        print(f"\n✗ 验证失败: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
