# -*- coding: utf-8 -*-
"""智能选品 Agent 的回归测试（M2）。

守的红线（每条都对应一个真实的翻车场景）：
1. 市场无可抓源时**直接失败**，不返回样本评分——假的排序比没排序危险；
2. 市场规模/增长率/供应链不得出现编造数字，必须是显式的"未接入"；
3. 缺采购价时利润分析判为**不可用**，不能把"只有物流成本"的到岸价
   当成完整成本去算 ROI；
4. 综合评分与各分量必须与工具层输出逐位一致，不得二次加工；
5. 预算档位不参与打分（改了预算，分数必须不变）。
"""
import unittest
from unittest import mock

from app.agents import smart_selection
from app.tools.executor import ToolOutcome


def _cand(keyword, score, median, rank=1, comps=None, pids=None):
    return {
        "keyword": keyword, "keyword_used": keyword, "sample_size": 8,
        "raw_count": 10, "filtered_noise": 2, "noise_rate": 0.2,
        "price_min": median * 0.7, "price_median": median, "price_max": median * 1.4,
        "spread_pct": 70.0, "distinct_terms": 6, "provenance_ids": pids or [1, 2],
        "source_url": f"https://x/{keyword}", "score": score, "rank": rank,
        "components": comps or {"yield": 100.0, "noise": 80.0, "depth": 60.0,
                                "spread": 50.0, "terms": 70.0},
        "top_items": [],
    }


def _scan_outcome(candidates, currency="USD"):
    return ToolOutcome(True, "done", None, "扫描完成", {
        "source": "newegg", "category": "宠物用品", "currency": currency,
        "keyword_origin": "builtin_hypothesis",
        "keywords_requested": [c["keyword"] for c in candidates],
        "scans_run": len(candidates), "scans_ok": len(candidates),
        "scans_usable": len(candidates), "min_sample": 3, "target_sample": 8,
        "scans_failed": [], "scans_thin": [],
        "candidates": candidates, "weights": {}, "method": "", "basis": "",
    })


def _profit(cost_basis=28.0, price=45.0, landed=38.0):
    return ToolOutcome(True, "done", None, "定价沙盘", {
        "fx_rate": 7.1, "fx_provenance_id": 77,
        "cost_basis_cny": cost_basis, "cost_basis_note": "参数指定采购价（事实）",
        "cost_ratio": 0.35, "unit_cost_usd": cost_basis / 7.1,
        "landed_cost": landed,
        "cost_breakdown": {"unit_cost_usd": cost_basis / 7.1, "freight_usd": 2.5,
                           "duty_usd": 0.3, "last_mile_usd": 5.5,
                           "duty_pct": 7.0, "commission_pct": 15.0},
        "price_band": {"min": 30.0, "median": 42.0, "max": 60.0},
        "band_source": "us_retail（真实美国站竞品价）",
        "band_currency_note": "8 条 USD 数据点",
        "floor_price": price,
        "suggestion": {"price": price, "margin_pct": 20.0, "in_band": True,
                       "positioning": "进价格带"},
        "sensitivity": [], "assumptions": "佣金/关税为可调参数",
    })


def _rescan_outcome(pids=(11, 12, 13)):
    """Top1 补扫结果：商品级存证（pricing 只认 item 级，不认 observation 级）。"""
    return ToolOutcome(True, "done", None, "补扫完成", {
        "keyword_used": "dog bed", "source": "dangdang", "currency": "CNY",
        "scan_provenance_id": 900,
        "items": [{"title": f"狗床{i}", "price": 200.0 + i * 10, "currency": "CNY",
                   "source": "dangdang", "source_url": f"https://x/{i}",
                   "provenance_id": pid} for i, pid in enumerate(pids)],
        "price_band": {"min": 200.0, "median": 210.0, "max": 220.0},
    })


class TestSmartSelectionGuards(unittest.IsolatedAsyncioTestCase):
    async def test_market_without_source_fails_not_samples(self):
        r = await smart_selection.run("宠物用品", marketplace="SEA")
        self.assertFalse(r["success"])
        self.assertIn("没有可抓取的数据源", r["message"])
        self.assertIsNone(r["data"])

    async def test_missing_category_fails(self):
        r = await smart_selection.run("  ", marketplace="US")
        self.assertFalse(r["success"])

    async def test_scan_failure_propagates(self):
        bad = ToolOutcome(False, "error", None, "全部候选词样本不足")
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(return_value=bad)):
            r = await smart_selection.run("宠物用品", marketplace="US")
        self.assertFalse(r["success"])
        self.assertIn("选品扫描失败", r["message"])

    async def test_empty_candidates_fails(self):
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(return_value=_scan_outcome([]))):
            r = await smart_selection.run("宠物用品", marketplace="US")
        self.assertFalse(r["success"])

    async def test_scan_falls_back_to_next_source(self):
        """首选源被拦（Cloud Run 出口访问 dangdang 偶发空壳页的实测场景）：
        换到市场源表下一个源继续，不空手报错；结果里数据源标的是实际成功的源。"""
        bad = ToolOutcome(False, "error", None, "dangdang 返回拦截页或空壳", None)
        cands = [_cand("dog bed", 82.0, 45.0), _cand("cat litter box", 61.0, 30.0, rank=2)]
        fx_out = ToolOutcome(True, "done", None, "汇率", {"rate": 7.1, "provenance_id": 77})
        opp = mock.AsyncMock(side_effect=[bad, _scan_outcome(cands)])
        with mock.patch.object(smart_selection.opportunity, "run", opp), \
             mock.patch.object(smart_selection.market_scan, "run",
                               mock.AsyncMock(return_value=_rescan_outcome())), \
             mock.patch.object(smart_selection.fx, "run",
                               mock.AsyncMock(return_value=fx_out)), \
             mock.patch.object(smart_selection.pricing, "run",
                               mock.AsyncMock(return_value=_profit())):
            r = await smart_selection.run("宠物用品", marketplace="US", limit=6)
        self.assertTrue(r["success"])
        self.assertEqual(r["data"]["market_opportunity"]["data_source"], "newegg")
        calls = [c.args[0].get("source") for c in opp.call_args_list]
        self.assertEqual(calls, ["dangdang", "newegg"])

    async def test_all_sources_fail_reports_each_attempt(self):
        bad = ToolOutcome(False, "error", None, "dangdang 返回拦截页或空壳", None)
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(side_effect=[bad, bad])):
            r = await smart_selection.run("宠物用品", marketplace="US")
        self.assertFalse(r["success"])
        self.assertIn("选品扫描失败", r["message"])
        tried = {t["source"] for t in r["detail"]["sources_tried"]}
        self.assertEqual(tried, {"dangdang", "newegg"})


class TestSmartSelectionHonesty(unittest.IsolatedAsyncioTestCase):
    async def _run(self, candidates=None, profit=None, fx_ok=True, **kw):
        cands = candidates or [_cand("dog bed", 82.0, 45.0),
                               _cand("cat litter box", 61.0, 30.0, rank=2)]
        fx_out = ToolOutcome(True, "done", None, "汇率",
                            {"rate": 7.1, "provenance_id": 77})
        price_out = profit if profit is not None else _profit()
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(return_value=_scan_outcome(cands))), \
             mock.patch.object(smart_selection.market_scan, "run",
                               mock.AsyncMock(return_value=_rescan_outcome())), \
             mock.patch.object(smart_selection.fx, "run",
                               mock.AsyncMock(return_value=fx_out)), \
             mock.patch.object(smart_selection.pricing, "run",
                               mock.AsyncMock(return_value=price_out)):
            return await smart_selection.run("宠物用品", marketplace="US", **kw)

    async def test_unsourced_fields_are_explicit(self):
        r = await self._run()
        self.assertTrue(r["success"])
        d = r["data"]
        self.assertEqual(d["data_quality"], "real")
        self.assertIn("未接入", d["market_opportunity"]["market_size"])
        self.assertIn("未接入", d["market_opportunity"]["growth_rate"])
        # 供应链必须是空列表 + 说明，不能有编造供应商
        self.assertEqual(d["supply_recommendations"], [])
        self.assertIn("未接入", d["supply_note"])

    async def test_no_fabricated_supplier_names(self):
        """红线：供应与产品条目里不得出现供应商名（海丝样本数据最易残留的东西）。

        注意断言的**范围**：只查结构化条目，不查 supply_note——那段说明本身
        会列举"哪些平台没接入"，是解释不是数据。
        """
        r = await self._run()
        rows = (r["data"]["supply_recommendations"]
                + r["data"]["product_recommendations"])
        blob = str(rows)
        for fake in ("华强北", "义乌", "1688直供", "阿里巴巴供应商"):
            self.assertNotIn(fake, blob)

    async def test_prices_rounded_to_two_decimals(self):
        """浮点毛刺不得漏进界面：31.499999999999996 这种值必须被舍入。"""
        cand = _cand("dog bed", 82.0, 45.0)
        cand["price_min"] = 45.0 * 0.7     # 31.499999999999996
        cand["price_max"] = 45.0 * 1.4     # 62.99999999999999
        r = await self._run(candidates=[cand])
        p = r["data"]["product_recommendations"][0]
        self.assertEqual(p["price_band"]["min"], 31.5)
        self.assertEqual(p["price_band"]["max"], 63.0)
        self.assertNotIn("99999", p["reason"])
        self.assertIn("31.50", p["reason"])

    async def test_predicted_sales_never_numeric(self):
        r = await self._run()
        for p in r["data"]["product_recommendations"]:
            self.assertNotRegex(p["predicted_sales"], r"^\d")

    async def test_scores_match_tool_output_exactly(self):
        comps = {"yield": 88.0, "noise": 75.0, "depth": 62.0,
                 "spread": 44.0, "terms": 91.0}
        cand = _cand("dog bed", 77.5, 45.0, comps=comps)
        r = await self._run(candidates=[cand])
        d = r["data"]
        self.assertEqual(d["overall_score"]["total"], 77.5)
        self.assertEqual(d["overall_score"]["details"]["样本充足度"], 88.0)
        self.assertEqual(d["overall_score"]["details"]["表达丰富度"], 91.0)
        self.assertEqual(d["product_recommendations"][0]["scores"]["综合评分"], 77.5)

    async def test_budget_does_not_change_score(self):
        low = await self._run(budget="低")
        high = await self._run(budget="高")
        self.assertEqual(low["data"]["overall_score"]["total"],
                         high["data"]["overall_score"]["total"])
        self.assertIn("不参与打分", low["data"]["budget_note"])

    async def test_provenance_ids_aggregated(self):
        r = await self._run()
        ids = r["data"]["provenance_ids"]
        self.assertIn(1, ids)
        self.assertIn(77, ids)   # 汇率存证

    async def test_profit_unavailable_when_cost_basis_zero(self):
        """成本基数为 0 = 只有物流成本，必须判不可用，不能算 ROI。"""
        r = await self._run(profit=_profit(cost_basis=0.0))
        pa = r["data"]["profit_analysis"]
        self.assertFalse(pa["available"])
        self.assertIn("缺少采购价", pa["reason"])
        for p in r["data"]["product_recommendations"]:
            self.assertEqual(p["estimated_roi"], "需采购价")

    async def test_profit_available_when_cost_given(self):
        r = await self._run(unit_cost_cny=28.0)
        pa = r["data"]["profit_analysis"]
        self.assertTrue(pa["available"])
        self.assertIn("到岸总成本", pa["cost_breakdown"])
        self.assertIn("净利率", pa["revenue"])

    async def test_entry_difficulty_derived_from_floor_vs_band(self):
        hard = _profit(price=99.0)     # 下限 99 > 带上限 60
        r = await self._run(unit_cost_cny=28.0, profit=hard)
        self.assertIn("高", r["data"]["market_opportunity"]["entry_difficulty"])

        cheap = _profit(price=10.0)    # 下限 10 < 带下限 30
        r2 = await self._run(unit_cost_cny=28.0, profit=cheap)
        self.assertIn("低", r2["data"]["market_opportunity"]["entry_difficulty"])

    async def test_fx_failure_marks_profit_unavailable(self):
        cands = [_cand("dog bed", 82.0, 45.0)]
        fx_bad = ToolOutcome(False, "error", None, "汇率源不可达")
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(return_value=_scan_outcome(cands))), \
             mock.patch.object(smart_selection.market_scan, "run",
                               mock.AsyncMock(return_value=_rescan_outcome())), \
             mock.patch.object(smart_selection.fx, "run",
                               mock.AsyncMock(return_value=fx_bad)):
            r = await smart_selection.run("宠物用品", marketplace="US",
                                          unit_cost_cny=28.0)
        self.assertTrue(r["success"])          # 选品本身仍可用
        self.assertFalse(r["data"]["profit_analysis"]["available"])
        self.assertIn("汇率", r["data"]["profit_analysis"]["reason"])

    async def test_action_plan_has_no_data_claims(self):
        r = await self._run()
        plan = r["data"]["action_plan"]
        self.assertEqual(len(plan), 3)
        for phase in plan.values():
            self.assertTrue(phase["name"])
            self.assertGreaterEqual(len(phase["tasks"]), 2)

    async def test_gaps_listed(self):
        r = await self._run()
        gaps = " ".join(r["data"]["gaps"])
        for word in ("市场规模", "供应链", "销量", "竞争度"):
            self.assertIn(word, gaps)

    async def test_competition_is_labelled_as_proxy(self):
        r = await self._run()
        self.assertIn("样本口径代理", r["data"]["market_opportunity"]["competition_level"])


class TestToolContract(unittest.TestCase):
    """与工具层的接口契约。这两条都是实测踩过的真 bug，不是假想。"""

    def test_chinese_category_maps_to_english_tool_key(self):
        """传中文品类会让内置候选词取不到、relevant() 过滤整层失效。

        实测：传「宠物用品」时 5 个候选词退化成 1 个，且 `relevant()` 因找不到
        CATEGORY_RULES 规则而放行全部噪声。映射表与 market_insight 共用。
        """
        from app.agents.sample_market import CATEGORY_TOOL_KEY, tool_category
        self.assertEqual(CATEGORY_TOOL_KEY["宠物用品"], "pet")
        self.assertEqual(CATEGORY_TOOL_KEY["家居用品"], "home")
        self.assertEqual(CATEGORY_TOOL_KEY["3C电子"], "electronics")
        self.assertEqual(tool_category("宠物用品"), "pet")
        self.assertEqual(tool_category("未登记品类"), "未登记品类")

    def test_keyword_is_comma_string_not_list(self):
        """工具层 keywords 参数按逗号切分；传 list 会被 str() 成 "['dog bed']"。"""
        import inspect
        src = inspect.getsource(smart_selection.run)
        self.assertIn('scan_input["keywords"] = words', src)
        self.assertNotIn('"keywords"] = [', src)


class TestScanInputContract(unittest.IsolatedAsyncioTestCase):
    async def _capture(self, **kw):
        seen: dict = {}

        async def fake_scan(inp):
            seen.update(inp)
            return _scan_outcome([_cand("dog bed", 80.0, 45.0)])

        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(side_effect=fake_scan)), \
             mock.patch.object(smart_selection.market_scan, "run",
                               mock.AsyncMock(return_value=_rescan_outcome())), \
             mock.patch.object(smart_selection.pricing, "run",
                               mock.AsyncMock(return_value=_profit())):
            r = await smart_selection.run("宠物用品", marketplace="US", **kw)
        return r, seen

    async def test_category_translated_and_keywords_omitted(self):
        r, seen = await self._capture()
        self.assertTrue(r["success"])
        self.assertEqual(seen["category"], "pet")
        # 未给词时不传 keywords，让工具层取内置假设词并标记 builtin_hypothesis
        self.assertNotIn("keywords", seen)

    async def test_user_keyword_forwarded_verbatim(self):
        r, seen = await self._capture(keyword="dog bed, cat bed")
        self.assertTrue(r["success"])
        self.assertEqual(seen["keywords"], "dog bed, cat bed")
        self.assertIsInstance(seen["keywords"], str)

    async def test_category_without_builtin_needs_keyword(self):
        r = await smart_selection.run("服装", marketplace="US")
        self.assertFalse(r["success"])
        self.assertIn("没有内置候选词", r["message"])

    async def test_pricing_receives_item_level_provenance(self):
        """红线：pricing 只认 item 级存证。

        实测踩过——直接把选品雷达候选的 provenance_ids（observation 级）喂给
        pricing，恒报"可用商品数据点不足 2 条"，利润分析永远不可用。
        必须补一次 market_scan 拿逐条商品的存证号。
        """
        price_seen: dict = {}

        async def fake_pricing(inp):
            price_seen.update(inp)
            return _profit()

        cand = _cand("dog bed", 80.0, 45.0, pids=[999])   # 候选只带 observation 级 id
        with mock.patch.object(smart_selection.opportunity, "run",
                               mock.AsyncMock(return_value=_scan_outcome([cand]))), \
             mock.patch.object(smart_selection.market_scan, "run",
                               mock.AsyncMock(return_value=_rescan_outcome())), \
             mock.patch.object(smart_selection.fx, "run",
                               mock.AsyncMock(return_value=ToolOutcome(
                                   True, "done", None, "汇率",
                                   {"rate": 7.1, "provenance_id": 77}))), \
             mock.patch.object(smart_selection.pricing, "run",
                               mock.AsyncMock(side_effect=fake_pricing)):
            r = await smart_selection.run("宠物用品", marketplace="US",
                                          unit_cost_cny=28.0)

        self.assertTrue(r["success"])
        self.assertEqual(price_seen["item_ids"], [11, 12, 13])
        self.assertNotIn(999, price_seen["item_ids"])


if __name__ == "__main__":
    unittest.main()
