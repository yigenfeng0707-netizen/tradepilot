# -*- coding: utf-8 -*-
"""全链路编排的回归测试（M2）。

守的红线：
1. **单步失败不拖垮整条链**：其余步骤照常跑完，失败原因逐条记进 errors；
2. **文案的产品名有明确来源**：用户输入 > 选品 Top1 > 跳过（并说明为什么）；
3. **进度事件是真的**：每个 step 必须先 running 再 done/failed/skipped；
4. **不合并 data_quality**：四个步骤的可信度分开标，不糊成一个总标记。
"""
import unittest
from unittest import mock

from app.agents import pipeline
from app.agents import (compliance as cp, content_generation as cg,
                        market_insight as mi, smart_selection as ss)


def _ok(agent, data, quality="real"):
    return {"success": True, "agent": agent, "data": data,
            "data_quality": quality, "message": f"{agent}完成"}


def _fail(agent, msg):
    return {"success": False, "agent": agent, "data": None,
            "data_quality": None, "message": msg, "detail": {}}


MI_DATA = {"category": "宠物用品", "market_size": "未接入", "price_tiers": []}
SS_DATA = {"overall_score": {"value": 54.4, "basis": "五分量"},
           "product_recommendations": [{"product": "dog bed"}]}
CG_DATA = {"content": {"title": "Dog Bed"}, "platform": "shopee",
           "generated_by": "llm"}
CP_DATA = {"target_country": "美国", "risk_level": "中", "checks": []}


async def _collect(**kwargs):
    events = []
    async for ev in pipeline.run_steps(**kwargs):
        events.append(ev)
    return events


class TestHappyPath(unittest.IsolatedAsyncioTestCase):
    async def _patch_all(self):
        return (
            mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))),
            mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))),
            mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", CG_DATA, "generated"))),
            mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))),
        )

    async def test_all_steps_complete(self):
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            evs = await _collect(category="宠物用品")
        finally:
            for p in ps:
                p.stop()
        done = evs[-1]
        self.assertEqual(done["event"], "done")
        self.assertEqual(done["summary"]["steps_completed"], 4)
        self.assertEqual(done["summary"]["errors"], 0)
        self.assertEqual(done["summary"]["product"], "dog bed")

    async def test_quality_is_not_merged(self):
        """四步的可信度必须分开标——糊成一个总标记等于把 sample 洗成 real。"""
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            evs = await _collect(category="宠物用品")
        finally:
            for p in ps:
                p.stop()
        qualities = {e["step"]: e.get("quality") for e in evs
                     if e.get("status") == "done"}
        self.assertEqual(qualities["market_insight"], "real")
        self.assertEqual(qualities["content_generation"], "generated")
        self.assertEqual(qualities["compliance"], "sample")
        self.assertNotIn("data_quality", evs[-1])

    async def test_each_step_starts_with_running(self):
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            evs = await _collect(category="宠物用品")
        finally:
            for p in ps:
                p.stop()
        statuses: dict[str, list[str]] = {}
        for e in evs:
            if e["event"] == "step":
                statuses.setdefault(e["step"], []).append(e["status"])
        for step, seq in statuses.items():
            self.assertEqual(seq[0], "running", f"{step} 没有先报 running")
            self.assertIn(seq[-1], ("done", "failed", "skipped"), step)


class TestDegradation(unittest.IsolatedAsyncioTestCase):
    async def test_one_step_failure_does_not_abort_chain(self):
        """选品失败时，市场洞察与合规照样要跑完——整条链退出会让用户
        连已经成功的那几步都看不到。"""
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_fail("智能选品Agent", "没有内置候选词"))), \
             mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", CG_DATA, "generated"))), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品")
        done = evs[-1]
        # 选品失败 → 没有 Top1 产品名 → 文案步骤跳过；洞察与合规照常完成
        self.assertEqual(done["summary"]["steps_completed"], 2)
        self.assertEqual(done["summary"]["errors"], 1)
        self.assertIn("智能选品", done["summary"]["failed_steps"])
        cg_ev = [e for e in evs if e.get("step") == "content_generation"][-1]
        self.assertEqual(cg_ev["status"], "skipped")

    async def test_exception_in_agent_is_caught(self):
        with mock.patch.object(mi, "run", mock.AsyncMock(
                side_effect=RuntimeError("boom"))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))), \
             mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", CG_DATA, "generated"))), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品")
        mi_ev = [e for e in evs if e.get("step") == "market_insight"][-1]
        self.assertEqual(mi_ev["status"], "failed")
        self.assertIn("RuntimeError", mi_ev["message"])
        self.assertEqual(evs[-1]["summary"]["steps_completed"], 3)


class TestProductNameResolution(unittest.IsolatedAsyncioTestCase):
    async def test_user_input_wins(self):
        seen: dict = {}

        async def fake_cg(name, **kw):
            seen["name"] = name
            return _ok("内容生成Agent", CG_DATA, "generated")

        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))), \
             mock.patch.object(cg, "run", mock.AsyncMock(side_effect=fake_cg)), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品", product_name="自动喂食器")
        self.assertEqual(seen["name"], "自动喂食器")
        self.assertEqual(evs[-1]["state"]["product_name_origin"], "用户输入")

    async def test_falls_back_to_top_candidate(self):
        seen: dict = {}

        async def fake_cg(name, **kw):
            seen["name"] = name
            return _ok("内容生成Agent", CG_DATA, "generated")

        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))), \
             mock.patch.object(cg, "run", mock.AsyncMock(side_effect=fake_cg)), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品")
        self.assertEqual(seen["name"], "dog bed")
        self.assertEqual(evs[-1]["state"]["product_name_origin"], "选品 Top1")

    async def test_no_product_name_skips_content_with_reason(self):
        """没有产品名时必须跳过并说清原因，不能拿品类名硬凑一个产品。"""
        cg_mock = mock.AsyncMock()
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_fail("智能选品Agent", "没有内置候选词"))), \
             mock.patch.object(cg, "run", cg_mock), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品")
        cg_mock.assert_not_called()
        ev = [e for e in evs if e.get("step") == "content_generation"][-1]
        self.assertEqual(ev["status"], "skipped")
        self.assertIn("没有可用的产品名", ev["message"])

    async def test_include_content_false_skips(self):
        cg_mock = mock.AsyncMock()
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))), \
             mock.patch.object(cg, "run", cg_mock), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            evs = await _collect(category="宠物用品", include_content=False)
        cg_mock.assert_not_called()
        ev = [e for e in evs if e.get("step") == "content_generation"][-1]
        self.assertEqual(ev["status"], "skipped")
        self.assertIn("include_content", ev["message"])


class TestNonStreaming(unittest.IsolatedAsyncioTestCase):
    async def test_run_returns_summary(self):
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", MI_DATA))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", SS_DATA))), \
             mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", CG_DATA, "generated"))), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", CP_DATA, "sample"))):
            r = await pipeline.run("宠物用品")
        self.assertTrue(r["success"])
        self.assertEqual(r["pipeline"], "cross_border_full")
        self.assertEqual(r["summary"]["total_steps"], 4)
        self.assertIsNone(r["data_quality"])     # 逐步标注，不合并
        self.assertIn("market_insight", r["state"])

    async def test_all_failed_is_not_success(self):
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_fail("市场洞察Agent", "x"))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_fail("智能选品Agent", "y"))), \
             mock.patch.object(cg, "run", mock.AsyncMock()), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_fail("合规Agent", "z"))):
            r = await pipeline.run("美妆个护", target_country="巴西")
        self.assertFalse(r["success"])
        self.assertEqual(r["summary"]["steps_completed"], 0)


if __name__ == "__main__":
    unittest.main()
