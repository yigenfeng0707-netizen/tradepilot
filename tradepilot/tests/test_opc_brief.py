# Copyright 2026 TradePilot project. Apache-2.0.
"""OPC「想法→生意」简报测试：锁住“只搬运真实字段、不造数、不藏缺口”这条红线。

回归对象：参赛材料最容易滑向的两类造假——
  1. 把“全链路成功”写成每步都成功（真实报告里有降级/未执行步骤）；
  2. 编造节省百分比、市场规模等无来源数字。
"""
from __future__ import annotations

import unittest

from app import opc_brief


def sample_report() -> dict:
    return {
        "mission_id": "mission-test-001",
        "goal": "把宠物用品切入美国站",
        "plan_engine": "llm",
        "elapsed_s": 298.6,
        "autonomy": {
            "steps_executed": 2,
            "steps_not_run": ["listing_audit"],
            "steps_degraded": ["opportunity_scan"],
            "retries": 1,
            "llm_decisions": 2,
            "llm_overrides": 1,
        },
        "decisions": [
            {"tool": "market_scan", "summary": "抓到 4 条真实竞品", "reason": "规则评分最高",
             "llm_adopted": True, "llm_rejected": False, "gate": "pass"},
            {"tool": "pricing_sandbox", "summary": "报价下限 $56.99", "reason": "LLM 选择",
             "llm_adopted": True, "llm_rejected": True, "gate": "pass"},
        ],
        "results": {
            "market_scan": {"data": {"items": [1, 2, 3, 4]}},
            "pricing_sandbox": {"data": {"floor_price": 56.99}},
            "opportunity_scan": {"data": None},  # 降级：data 为 None
        },
        "provenance": {"stats": {"total": 117},
                       "traceability": {"facts_total": 9, "facts_traceable": 9}},
    }


class TestOpcBrief(unittest.TestCase):
    def setUp(self):
        self.brief = opc_brief.build_opc_brief(sample_report())

    def test_theme_and_track_are_present(self):
        self.assertEqual(self.brief["theme"], "和 AI 一起，把想法变成生意")
        self.assertEqual(self.brief["track"], "商业生产力")
        self.assertIn("OPC", self.brief["positioning"])

    def test_goal_and_run_facts_come_from_report(self):
        self.assertEqual(self.brief["goal"], "把宠物用品切入美国站")
        run = self.brief["run_facts"]
        self.assertEqual(run["steps_executed"], 2)
        self.assertEqual(run["elapsed_s"], 298.6)
        self.assertEqual(run["llm_overrides"], 1)
        self.assertEqual(run["retries"], 1)

    def test_executed_steps_carry_business_meaning(self):
        executed = [c for c in self.brief["value_chain"] if c["order"] is not None]
        self.assertEqual([c["step"] for c in executed], ["market_scan", "pricing_sandbox"])
        self.assertIn("市场", executed[0]["business_meaning"])
        self.assertIn("定价", executed[1]["business_meaning"])
        self.assertTrue(executed[1]["model_overrode_rule"])

    def test_degraded_and_not_run_steps_are_visible_not_hidden(self):
        by_step = {c["step"]: c for c in self.brief["value_chain"]}
        self.assertEqual(by_step["opportunity_scan"]["status"], "降级")
        self.assertIsNone(by_step["opportunity_scan"]["order"])  # 未进决策，排在末尾
        self.assertEqual(by_step["listing_audit"]["status"], "未执行")
        self.assertIn("listing_audit", self.brief["run_facts"]["steps_not_run"])

    def test_all_designed_stages_appear(self):
        steps = {c["step"] for c in self.brief["value_chain"]}
        self.assertEqual(steps, set(opc_brief.STAGE_BUSINESS_MEANING))

    def test_provenance_numbers_read_from_report(self):
        self.assertEqual(self.brief["provenance_facts"]["facts_total"], 9)
        self.assertEqual(self.brief["provenance_facts"]["facts_traceable"], 9)

    def test_no_fabricated_business_numbers(self):
        text = opc_brief.export_opc_markdown(self.brief)
        # 只看事实承载区（口径/红线章节之前）——免责声明里出现「市场规模」等词
        # 是在声明「不写它们」，属正常，不能据此误判。
        fact_zone = text.split("## 口径与红线")[0]
        for forbidden in ["市场规模", "增长率", "月搜索量", "提升 3", "节省 50"]:
            self.assertNotIn(forbidden, fact_zone)

    def test_markdown_export_has_chain_and_honesty(self):
        text = opc_brief.export_opc_markdown(self.brief)
        self.assertIn("想法 → 生意", text)
        self.assertIn("market_scan", text)
        self.assertIn("降级环节", text)
        self.assertIn("如实列出", text)  # 红线章节声明降级不隐藏

    def test_executed_step_status_comes_from_decision_card_not_results(self):
        # 回归：决策卡明明执行过，但 results 未随报告带出时，旧版误标"无数据"。
        # 已执行步骤的状态必须以决策卡为准。
        report = sample_report()
        report["results"] = {}  # results 缺失
        report["decisions"][0]["status"] = "done"
        report["decisions"][1]["status"] = "done"
        brief = opc_brief.build_opc_brief(report)
        executed = [c for c in brief["value_chain"] if c["order"] is not None]
        self.assertTrue(all(c["status"] == "完成" for c in executed),
                        [c["status"] for c in executed])

    def test_decision_card_non_done_status_is_degraded_not_success(self):
        report = sample_report()
        report["decisions"][0]["status"] = "error"  # 执行了但未成功
        brief = opc_brief.build_opc_brief(report)
        first = [c for c in brief["value_chain"] if c["order"] == 1][0]
        self.assertEqual(first["status"], "降级")
        self.assertIn("error", first["status_note"])

    def test_missing_fields_do_not_crash(self):
        brief = opc_brief.build_opc_brief({})
        self.assertIsNone(brief["goal"])
        self.assertEqual(len(brief["value_chain"]), len(opc_brief.STAGE_BUSINESS_MEANING))
        text = opc_brief.export_opc_markdown(brief)
        self.assertIn("—（待真人录入）", text)


if __name__ == "__main__":
    unittest.main()
