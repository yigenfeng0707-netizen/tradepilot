# Copyright 2026 TradePilot project. Apache-2.0.
"""评测口径纯函数的离线用例：保证进 BP 的每个数字都能复算、算不了就是 null。"""
import unittest

from app.eval_metrics import (fact_accounting, labor_cost_equiv,
                              spot_check_accuracy, spot_check_sheet, traceable)


def _report(items_pids=(1, 2, 3), fx_pid=10, verifs=("clause_matched", "page_only", "blocked"),
            clause_pids=(20, 21, None), with_listing=True):
    checks = []
    for i, v in enumerate(verifs):
        checks.append({"authority": f"机构{i}", "verification": v,
                       "provenance_id": clause_pids[i] if i < len(clause_pids) else None})
    listing = ({"copy": "Title here", "generated_by": "LLM:Atria-Dawn"}
               if with_listing else {})
    return {
        "results": {
            "market_scan": {"status": "done", "data": {
                "items": [{"title": f"item{p}", "price": 9.9, "provenance_id": p}
                          for p in items_pids],
                "price_band": {"min": 9.9}}},
            "fx_rate": {"status": "done", "data": {"base": "CNY", "quote": "USD",
                                                   "rate": 0.14, "provenance_id": fx_pid}},
            "pricing_sandbox": {"status": "done", "data": {"floor_price": 12.3}},
            "keyword_plan": {"status": "done", "data": {"buckets": {}}},
            "compliance_check": {"status": "done", "data": {"checks": checks, "total": len(checks)}},
            "listing_draft": {"status": "done", "data": listing},
        }
    }


def _resolver(ok_ids):
    """模拟存证库：在册且三项齐的 id 才算可溯。"""
    def resolve(pid):
        if pid not in ok_ids:
            return None
        return {"url": f"https://example.com/p/{pid}", "fetched_at": "2026-10-30T00:00:00+00:00",
                "content_hash": "deadbeef"}
    return resolve


class TestTraceable(unittest.TestCase):
    def test_ok_record(self):
        self.assertTrue(traceable(1, _resolver({1})))

    def test_missing_record_fails(self):
        self.assertFalse(traceable(9, _resolver({1})))

    def test_none_id_fails(self):
        self.assertFalse(traceable(None, _resolver({1})))

    def test_empty_hash_fails(self):
        # 写入约束挡不住"内容 hash 是空串"这种实质不可核验的记录
        self.assertFalse(traceable(1, lambda pid: {"url": "u", "fetched_at": "t",
                                                   "content_hash": ""}))


class TestFactAccounting(unittest.TestCase):
    def test_all_traceable_is_one(self):
        acc = fact_accounting(_report(), _resolver({1, 2, 3, 10, 20, 21}))
        self.assertEqual(acc["source_traceable_rate"], 1.0)
        self.assertEqual(acc["untraceable"], [])
        # 3 竞品 + 1 汇率 + 2 已落存证条款 = 6；blocked 那条不进分母
        self.assertEqual(acc["facts_total"], 6)

    def test_unverified_clause_excluded_but_counted(self):
        acc = fact_accounting(_report(), _resolver({1, 2, 3, 10, 20, 21}))
        self.assertEqual(acc["excluded"]["compliance_unverified"], 1)

    def test_broken_item_lowers_rate_and_is_listed(self):
        acc = fact_accounting(_report(items_pids=(1, 2, 3)), _resolver({1, 3, 10, 20, 21}))
        self.assertLess(acc["source_traceable_rate"], 1.0)
        self.assertEqual([u["kind"] for u in acc["untraceable"]], ["item"])

    def test_derived_figures_not_in_denominator(self):
        acc = fact_accounting(_report(), _resolver({1, 2, 3, 10, 20, 21}))
        # 价格带 + 下限/敏感性 + 关键词派生 = 4 项派生，均未进 facts_total
        self.assertEqual(acc["excluded"]["derived_figures"], 4)
        self.assertEqual(acc["excluded"]["ai_generated_fields"], 1)
        self.assertTrue(acc["listing_labelled"])

    def test_empty_report_is_none_not_hundred(self):
        acc = fact_accounting({"results": {}}, _resolver(set()))
        self.assertIsNone(acc["source_traceable_rate"])
        self.assertEqual(acc["facts_total"], 0)

    def test_no_fx_row_when_absent(self):
        rep = _report()
        rep["results"]["fx_rate"]["data"]["provenance_id"] = None
        acc = fact_accounting(rep, _resolver({1, 2, 3, 20, 21}))
        self.assertEqual(acc["facts_total"], 5)


class TestSpotCheck(unittest.TestCase):
    def test_sheet_leaves_human_fields_null(self):
        sheet = spot_check_sheet([{"id": 5, "kind": "item", "url": "u", "fetched_at": "t",
                                   "content_hash": "h", "payload": {"price": 1}}])
        self.assertEqual(len(sheet), 1)
        self.assertIsNone(sheet[0]["human_verified"])
        self.assertEqual(sheet[0]["provenance_id"], 5)

    def test_accuracy_null_until_filled(self):
        sheet = [{"human_verified": None}, {"human_verified": True}]
        out = spot_check_accuracy(sheet)
        self.assertIsNone(out["accuracy"])
        self.assertEqual((out["checked"], out["total"]), (1, 2))

    def test_accuracy_computes_when_complete(self):
        sheet = [{"human_verified": True}, {"human_verified": False},
                 {"human_verified": True}, {"human_verified": True}]
        out = spot_check_accuracy(sheet)
        self.assertEqual(out["accuracy"], 0.75)

    def test_empty_sheet_is_none(self):
        self.assertIsNone(spot_check_accuracy([])["accuracy"])


class TestLaborCost(unittest.TestCase):
    def test_null_baseline_refuses_estimate(self):
        out = labor_cost_equiv({"task_time_min": None}, 200.0, 80.0)
        self.assertIsNone(out["labor_cost_equiv_cny"])
        self.assertIsNone(out["automation_rate"])
        self.assertIn("禁估算", out["reason"])

    def test_math_is_recomputable(self):
        out = labor_cost_equiv({"task_time_min": 120}, 300.0, 90.0)
        self.assertEqual(out["labor_cost_equiv_cny"], 180.0)   # 2h × 90
        self.assertEqual(out["agent_time_cost_cny"], 7.5)      # 5min × 90
        self.assertEqual(out["automation_rate"], 0.9583)
        self.assertIn("入参", out["reason"])

    def test_zero_hourly_refuses(self):
        self.assertIsNone(labor_cost_equiv({"task_time_min": 60}, 100.0, 0)["labor_cost_equiv_cny"])


if __name__ == "__main__":
    unittest.main()

