"""规划器测试：自主性的行为契约。

这些用例锁住三件过去做不到的事：
1. 下一步由状态决定（依赖未满足的步骤不可执行）；
2. 失败会触发换参重试，重试耗尽后依赖它的步骤降级而不是硬跑；
3. LLM 的建议必须落在当前可执行候选内（白名单校验）。
"""
from __future__ import annotations

import unittest

from app import planner


def done(**data) -> dict:
    return {"status": "done", "data": data}


class TestDependencyGraph(unittest.TestCase):
    def test_first_step_is_market_scan(self) -> None:
        chosen, _ = planner.pick({}, {}, "")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.name, "market_scan")

    def test_pricing_blocked_before_market_scan(self) -> None:
        ranked = planner.rank({}, {}, "")
        pricing = next(c for c in ranked if c.name == "pricing_sandbox")
        self.assertFalse(pricing.eligible)
        self.assertIn("market_scan", pricing.blocked_by)

    def test_listing_needs_market_scan_then_waits_for_facts(self) -> None:
        state = {"market_scan": done(items=[{"provenance_id": 1}])}
        ranked = planner.rank(state, {}, "")
        listing = next(c for c in ranked if c.name == "listing_draft")
        self.assertTrue(listing.eligible, "有商品卡即可成稿")
        self.assertTrue(any("事实面待补" in n for n in listing.notes))

        state["pricing_sandbox"] = done(floor_price=10)
        state["compliance_check"] = done(checks=[])
        after = next(c for c in planner.rank(state, {}, "") if c.name == "listing_draft")
        self.assertLess(after.score, listing.score, "事实面补齐后应更优先")

    def test_audit_requires_draft(self) -> None:
        ranked = planner.rank({}, {}, "")
        audit = next(c for c in ranked if c.name == "listing_audit")
        self.assertFalse(audit.eligible)


class TestRetryAndDegrade(unittest.TestCase):
    def test_failure_triggers_retry_with_alt_source(self) -> None:
        state = {"market_scan": {"status": "error", "data": {}}}
        chosen, _ = planner.pick(state, {"market_scan": 1}, "")
        self.assertEqual(chosen.name, "market_scan")
        self.assertEqual(chosen.attempt, 2)
        params = planner.retry_input("market_scan", planner.SPEC_BY_NAME["market_scan"],
                                     {"source": "dangdang"}, 2)
        self.assertEqual(params["source"], "walmart", "重试应切换数据源")

    def test_exhausted_attempts_are_abandoned(self) -> None:
        state = {"market_scan": {"status": "error", "data": {}}}
        ranked = planner.rank(state, {"market_scan": 3}, "")
        scan = next(c for c in ranked if c.name == "market_scan")
        self.assertFalse(scan.eligible)
        self.assertTrue(any("尝试上限" in b for b in scan.blocked_by))

    def test_dependents_degrade_when_dependency_failed(self) -> None:
        state = {"market_scan": {"status": "error", "data": {}}}
        ranked = planner.rank(state, {"market_scan": 3}, "")
        pricing = next(c for c in ranked if c.name == "pricing_sandbox")
        self.assertFalse(pricing.eligible)
        # 独立分支仍可执行，保证任务降级但不中断
        compliance = next(c for c in ranked if c.name == "compliance_check")
        self.assertTrue(compliance.eligible)


class TestLlmWhitelist(unittest.TestCase):
    def test_only_eligible_candidates_offered(self) -> None:
        wl = planner.build_whitelist({}, {}, "")
        names = {c["name"] for c in wl}
        self.assertIn("market_scan", names)
        self.assertNotIn("pricing_sandbox", names)

    def test_is_legal_rejects_out_of_graph_choice(self) -> None:
        self.assertTrue(planner.is_legal("compliance_check", {}, {}, ""))
        self.assertFalse(planner.is_legal("pricing_sandbox", {}, {}, ""))
        self.assertFalse(planner.is_legal("nonexistent", {}, {}, ""))


class TestGoalAwareness(unittest.TestCase):
    def test_goal_keywords_shift_priority(self) -> None:
        goal = "只做合规体检，看认证要求"
        ranked = planner.rank({}, {}, goal)
        compliance = next(c for c in ranked if c.name == "compliance_check")
        scan = next(c for c in ranked if c.name == "market_scan")
        self.assertLess(compliance.score, scan.score, "目标点名合规时合规应更优先")


if __name__ == "__main__":
    unittest.main()
