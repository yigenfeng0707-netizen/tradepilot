"""自主性指标口径（llm_decisions / llm_rejected_to_rule）。

这是评测产物里最容易被追问的一个数：旧判据按 reason 前缀匹配，把"模型建议越界、
已回落规则"也计入 LLM 决策，导致自主性虚高。本文件把口径钉死成机器可复算的契约：
只有模型选择被真正采纳才算 LLM 决策，被拒的单独计数。
工具执行整体打桩，不联网、不抓取。
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from app import orchestrator
from app.tools.executor import ToolOutcome

PARAMS = {"keyword": "dog bed", "category": "pet", "source": "dangdang",
          "marketplace": "US", "goal": "把宠物用品切入美国站"}


class FakeRegistry:
    """把每个工具都变成"立即成功、无数据"的桩，只考察编排与记账。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        return ToolOutcome(True, "done", None, f"{name} stub", {"items": []})


def _run_mission(plan_next) -> dict[str, Any]:
    registry = FakeRegistry()
    with mock.patch.object(orchestrator, "build_registry", lambda: registry), \
            mock.patch.object(orchestrator.llm, "available", lambda: True), \
            mock.patch.object(orchestrator.llm, "plan_next", plan_next):
        report: dict[str, Any] = {}

        async def drive() -> None:
            nonlocal report
            async for event in orchestrator.run_mission(PARAMS["goal"], dict(PARAMS)):
                if event["type"] == "mission_done":
                    report = event["report"]

        asyncio.run(drive())
    return report


async def _always_illegal(goal, done, tools):
    """模型每次都点一个不在工具面里的名字 → 任何时刻都不合法，必须全部回落规则。"""
    return {"next_tool": "web_search", "reason": "想直接搜网页"}


async def _follow_rule(goal, done, tools):
    """模型每次都点规则首选 → 采纳且与规则一致，不算覆盖。"""
    return {"next_tool": tools[0]["name"], "reason": "同意规则"}


async def _break_rules(goal, done, tools):
    """模型点在候选内但与规则首选不同的工具 → 采纳 + 覆盖 +1。"""
    names = [t["name"] for t in tools]
    return {"next_tool": names[-1], "reason": "换一条路"}


class TestAutonomyMetrics(unittest.TestCase):
    def test_rejected_suggestions_do_not_count_as_llm_decisions(self) -> None:
        report = _run_mission(_always_illegal)
        auto = report["autonomy"]
        self.assertGreater(auto["steps_executed"], 0)
        self.assertEqual(auto["llm_decisions"], 0)          # 一次都没被采纳
        self.assertEqual(auto["llm_rejected_to_rule"], auto["steps_executed"])
        self.assertEqual(auto["llm_overrides"], 0)
        self.assertTrue(all("不在当前可执行候选内" in d["reason"] for d in report["decisions"]))

    def test_adopted_choices_match_rule_are_counted_once(self) -> None:
        report = _run_mission(_follow_rule)
        auto = report["autonomy"]
        self.assertEqual(auto["llm_decisions"], auto["steps_executed"])
        self.assertEqual(auto["llm_rejected_to_rule"], 0)
        self.assertEqual(auto["llm_overrides"], 0)

    def test_override_is_counted_separately_from_decisions(self) -> None:
        report = _run_mission(_break_rules)
        auto = report["autonomy"]
        self.assertGreaterEqual(auto["llm_overrides"], 1)
        self.assertLessEqual(auto["llm_overrides"], auto["llm_decisions"])

    def test_every_decision_card_carries_explicit_flags(self) -> None:
        """决策卡必须自带布尔标记，指标不得再从 reason 文案里猜。"""
        report = _run_mission(_follow_rule)
        for card in report["decisions"]:
            self.assertIn("llm_adopted", card)
            self.assertIn("llm_rejected", card)
            self.assertNotEqual(card["llm_adopted"], card["llm_rejected"])
