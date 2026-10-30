"""来源闸门 + 定价确定性计算。

这两条是作品的"反幻觉"承重墙：闸门挡住编造的数据点，定价只做确定性算术。
它们必须能被机器复算，否则"可复算"只是说法。
"""
from __future__ import annotations

import asyncio
import unittest

from app import provenance
from app.orchestrator import build_registry
from app.tools import pricing

URL = "https://search.dangdang.com/?key=test"


def _seed_items(prices: list[float], currency: str = "CNY") -> list[int]:
    ids = []
    for i, p in enumerate(prices):
        ids.append(provenance.record(
            "item", URL, {"title": f"item {i}", "price": p, "currency": currency},
            raw_text=f"{i}-{p}"))
    return ids


def _seed_fx(rate: float = 7.0) -> int:
    return provenance.record("fx", "https://api.frankfurter.app/latest",
                             {"rate": rate, "pair": "USD/CNY"})


class TestProvenanceGate(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_registry()

    def test_unknown_item_id_is_held_not_executed(self) -> None:
        out = asyncio.run(self.registry.execute(
            "pricing_sandbox", {"item_ids": [999999999], "fx_id": None}))
        self.assertEqual(out.status, "held")
        self.assertEqual(out.gate, "provenance")
        self.assertIn("999999999", out.summary)

    def test_unknown_tool_is_rejected(self) -> None:
        out = asyncio.run(self.registry.execute("rm_rf", {}))
        self.assertEqual(out.status, "error")
        self.assertIn("白名单", out.summary)

    def test_registry_exposes_all_planner_steps(self) -> None:
        from app import planner
        self.assertEqual(set(self.registry.names()),
                         {s.name for s in planner.STEPS})


class TestPricingMath(unittest.TestCase):
    def test_cny_items_require_fx_id(self) -> None:
        ids = _seed_items([90.0, 100.0, 110.0])
        out = asyncio.run(pricing.run({"item_ids": ids}))
        self.assertEqual(out.status, "error")
        self.assertIn("fx_id", out.summary)

    def test_landed_cost_and_floor_are_recomputable(self) -> None:
        ids = _seed_items([90.0, 100.0, 110.0])
        fx_id = _seed_fx(7.0)
        out = asyncio.run(pricing.run({"item_ids": ids, "fx_id": fx_id}))
        self.assertEqual(out.status, "done", out.summary)
        data = out.data
        # 采购价 = 零售中位数 100 × 成本率 0.35 = ¥35，按现场汇率 7.0 折美元
        self.assertAlmostEqual(data["unit_cost_usd"], 35 / 7, places=2)
        # 到岸 = 采购 + 头程 2.5 + 尾程 5.5 + 关税 7%
        expected_landed = 35 / 7 * 1.07 + 8.0
        self.assertAlmostEqual(data["landed_cost"], expected_landed, places=2)
        # 报价下限 = 到岸 / (1 - 佣金 15% - 目标净利 20%)
        self.assertAlmostEqual(data["floor_price"], expected_landed / 0.65, places=1)
        self.assertEqual(len(data["sensitivity"]), 5)
        self.assertEqual(data["fx_provenance_id"], fx_id)
        self.assertIn("成本率为参数假设", data["cost_basis_note"])

    def test_cost_ratio_is_applied_and_adjustable(self) -> None:
        ids = _seed_items([100.0, 100.0, 100.0])
        fx_id = _seed_fx(7.0)
        low = asyncio.run(pricing.run({"item_ids": ids, "fx_id": fx_id,
                                       "cost_ratio": 0.2})).data
        high = asyncio.run(pricing.run({"item_ids": ids, "fx_id": fx_id,
                                        "cost_ratio": 0.5})).data
        self.assertAlmostEqual(low["cost_basis_cny"], 20.0, places=2)
        self.assertAlmostEqual(high["cost_basis_cny"], 50.0, places=2)
        self.assertLess(low["floor_price"], high["floor_price"])

    def test_explicit_unit_cost_wins_over_ratio(self) -> None:
        ids = _seed_items([100.0, 100.0, 100.0])
        fx_id = _seed_fx(7.0)
        data = asyncio.run(pricing.run({"item_ids": ids, "fx_id": fx_id,
                                        "unit_cost_cny": 42.0, "cost_ratio": 0.9})).data
        self.assertAlmostEqual(data["cost_basis_cny"], 42.0, places=2)
        self.assertIn("参数指定采购价", data["cost_basis_note"])

    def test_usd_items_with_cny_cost_require_fx(self) -> None:
        ids = _seed_items([20.0, 30.0, 40.0], currency="USD")
        out = asyncio.run(pricing.run({"item_ids": ids, "unit_cost_cny": 70.0}))
        self.assertEqual(out.status, "error", "给了 CNY 采购价就必须有现场汇率")
        self.assertIn("fx_id", out.summary)

    def test_usd_items_skip_fx_when_no_cny_cost(self) -> None:
        ids = _seed_items([20.0, 30.0, 40.0], currency="USD")
        out = asyncio.run(pricing.run({"item_ids": ids}))
        self.assertEqual(out.status, "done", out.summary)
        self.assertIn("us_retail", out.data["band_source"])


class TestPositioningBranches(unittest.TestCase):
    """定价建议必须三分支：进带 / 高于上限 / 低于下限。

    真实事故：旧版把"下限高于价格带上限"也输出成"报价下限低于价格带下限"，
    建议方向完全反了——这种错误比没有建议更危险。
    """

    def _run(self, unit_cost_cny: float) -> dict:
        ids = _seed_items([20.0, 30.0, 40.0], currency="USD")
        fx_id = _seed_fx(7.0)
        out = asyncio.run(pricing.run(
            {"item_ids": ids, "fx_id": fx_id, "unit_cost_cny": unit_cost_cny}))
        self.assertEqual(out.status, "done", out.summary)
        return out.data

    def test_in_band(self) -> None:
        data = self._run(70.0)  # 到岸 ~$18.7 → 下限 ~$28.8，落在 $20–$40
        self.assertTrue(data["suggestion"]["in_band"])
        self.assertIn("进价格带", data["suggestion"]["positioning"])

    def test_above_band_max(self) -> None:
        data = self._run(700.0)  # 成本过高，下限远超价格带上限
        self.assertFalse(data["suggestion"]["in_band"])
        self.assertGreater(data["floor_price"], data["price_band"]["max"])
        self.assertIn("高于价格带上限", data["suggestion"]["positioning"])

    def test_below_band_min(self) -> None:
        data = self._run(7.0)  # 成本极低，下限低于价格带下限
        self.assertFalse(data["suggestion"]["in_band"])
        self.assertLess(data["floor_price"], data["price_band"]["min"])
        self.assertIn("低于价格带下限", data["suggestion"]["positioning"])


if __name__ == "__main__":
    unittest.main()
