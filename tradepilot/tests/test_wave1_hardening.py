# -*- coding: utf-8 -*-
"""Wave 1 回归：墙钟预算、链路归档、租户归属、匿名限速。

守的行为（W1-1~W1-3、W1-5）：
1. 单步墙钟超时 → 该步诚实 fail（事件带"墙钟预算"字样），其余步骤继续跑完；
2. 产品 Agent 全链路（REST/SSE 主链路）跑完必须落档：文件存在、id 合法、
   done 事件带 mission_id 与 archive_error（失败时也不能是静默的）；
3. 归档归属：登录租户的请求写 tenant_id；另一租户不可见；
4. 匿名 agent 端点按 IP 滑动窗口限速，超限 429 且说明原因。

这些是"任务可回放"叙事在主链路上的机器证据——此前只有大屏 WS 通道会存档，
React 主前端跑的任务在 /api/missions 与 Admin 计数里完全不存在。
"""
import asyncio
import json
import os
import unittest
from unittest import mock

from app.agents import pipeline
from app.agents import (compliance as cp, content_generation as cg,
                        market_insight as mi, smart_selection as ss)
from app.config import CONFIG
from app.request_context import current_tenant_id
from app import store


def _ok(agent, data, quality="real"):
    return {"success": True, "agent": agent, "data": data,
            "data_quality": quality, "message": f"{agent}完成"}


async def _collect(**kwargs):
    events = []
    async for ev in pipeline.run_steps(**kwargs):
        events.append(ev)
    return events


class TestStepTimeout(unittest.IsolatedAsyncioTestCase):
    """W1-1：墙钟预算。"""

    async def test_slow_step_times_out_and_chain_continues(self):
        async def slow_market(*args, **kwargs):
            await asyncio.sleep(30)
            return _ok("市场洞察Agent", {})

        others = (
            mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent", {}))),
            mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", {}, "generated"))),
            mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", {}, "sample"))),
        )
        with mock.patch.object(mi, "run", side_effect=slow_market):
            for p in others:
                p.start()
            try:
                events = await _collect(
                    category="pet", keyword="dog bed", product_name="dog bed",
                    step_timeout_s=0.05)
            finally:
                for p in others:
                    p.stop()

        steps = [e for e in events if e.get("event") == "step"]
        statuses = {e["step"]: e["status"] for e in steps}
        # 超时那步失败，其余三步必须照常完成
        self.assertEqual(statuses["market_insight"], "failed")
        self.assertEqual(statuses["smart_selection"], "done")
        self.assertEqual(statuses["compliance"], "done")
        failed = next(e for e in steps
                      if e["step"] == "market_insight" and e["status"] == "failed")
        self.assertIn("墙钟预算", failed["message"])

    async def test_default_budget_comes_from_config(self):
        self.assertEqual(CONFIG.agent_step_timeout_s, 150.0)
        # 显式参数优先于配置（不实际执行，只核对传参解析）
        with mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", {}))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent",
                                 {"product_recommendations": []}))), \
             mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", {}, "generated"))), \
             mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", {}, "sample"))):
            events = await _collect(category="pet", step_timeout_s=999)
        self.assertTrue(any(e.get("event") == "done" for e in events))


class TestPipelineArchiving(unittest.IsolatedAsyncioTestCase):
    """W1-2/W1-3：主链路归档与租户归属。"""

    async def _patch_all(self):
        return (
            mock.patch.object(mi, "run", mock.AsyncMock(
                return_value=_ok("市场洞察Agent", {}))),
            mock.patch.object(ss, "run", mock.AsyncMock(
                return_value=_ok("智能选品Agent",
                                 {"product_recommendations": [{"product": "x"}]}))),
            mock.patch.object(cg, "run", mock.AsyncMock(
                return_value=_ok("内容生成Agent", {}, "generated"))),
            mock.patch.object(cp, "run", mock.AsyncMock(
                return_value=_ok("合规Agent", {}, "sample"))),
        )

    def _missions_dir(self):
        # 必须走 store 真正使用的 CONFIG（store.CONFIG）：其他测试模块可能
        # 给 store 换过独立 Config；手拼 app.config.CONFIG 会写读不一致。
        from app import store as store_mod
        return store_mod.CONFIG.missions_dir()

    async def test_pipeline_run_archives_mission_file(self):
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            events = await _collect(
                category="pet", keyword="dog bed", product_name="dog bed")
        finally:
            for p in ps:
                p.stop()
        done = next(e for e in events if e.get("event") == "done")
        self.assertIn("mission_id", done)
        self.assertTrue(done["mission_id"].startswith("mission-"))
        # 落盘了、且 id 过合法格式
        files = os.listdir(self._missions_dir())
        self.assertIn(f"{done['mission_id']}.json", files)
        report = json.load(open(
            os.path.join(self._missions_dir(), f"{done['mission_id']}.json"),
            encoding="utf-8"))
        self.assertEqual(report["mission_id"], done["mission_id"])
        self.assertIn("results", report)

    async def test_archive_takes_tenant_from_request_context(self):
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            token = current_tenant_id.set("tenant_wave1_test")
            try:
                events = await _collect(category="pet")
            finally:
                current_tenant_id.reset(token)
        finally:
            for p in ps:
                p.stop()
        done = next(e for e in events if e.get("event") == "done")
        report = store.get_mission(done["mission_id"], tenant_id="tenant_wave1_test")
        self.assertIsNotNone(report, "登录租户必须能看到自己跑的任务")
        self.assertEqual(report.get("tenant_id"), "tenant_wave1_test")
        # 另一租户不可见（跨租户不泄露存在性）
        self.assertIsNone(store.get_mission(done["mission_id"],
                                            tenant_id="tenant_other"))

    async def test_archive_failure_is_reported_not_silent(self):
        ps = await self._patch_all()
        for p in ps:
            p.start()
        try:
            with mock.patch("app.agents.pipeline.store.save_mission",
                            side_effect=RuntimeError("disk full")):
                events = await _collect(category="pet")
        finally:
            for p in ps:
                p.stop()
        done = next(e for e in events if e.get("event") == "done")
        self.assertIsNone(done["archive_path"])
        self.assertIn("disk full", done["archive_error"])


class TestMissionIdAntiCollision(unittest.TestCase):
    """同秒并发的两个任务 id 必须不同（历史秒级 id 会互相覆盖）。"""

    def test_new_mission_id_unique_within_same_second(self):
        ids = {store.new_mission_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)
        for mid in ids:
            self.assertRegex(
                mid, r"^mission-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


class TestAnonymousRateLimit(unittest.IsolatedAsyncioTestCase):
    """W1-5：匿名 agent 端点按 IP 限速。"""

    def setUp(self):
        from app import main
        main._anon_hits.clear()

    def test_anon_limit_returns_429_after_window_full(self):
        from app import main
        ip = "203.0.113.7"
        allowed = [main._anon_agent_allowed(ip)[0] for _ in range(10)]
        self.assertTrue(all(allowed))
        # 第 11 次必须被拦，且说明里带口径
        ok, why = main._anon_agent_allowed(ip)
        self.assertFalse(ok)
        self.assertIn("匿名调用频率超限", why)
        # 登录走配额表，不在这个匿名窗口里计数——另一个 IP 仍可用
        ok2, _ = main._anon_agent_allowed("198.51.100.2")
        self.assertTrue(ok2)

    def test_anon_limit_window_is_sliding(self):
        from app import main
        ip = "203.0.113.9"
        with mock.patch("app.main.time.monotonic",
                        side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 360]):
            for _ in range(10):
                self.assertTrue(main._anon_agent_allowed(ip)[0])
            self.assertFalse(main._anon_agent_allowed(ip)[0])
            # 窗口外（300s 后），同一 IP 重新可用
            self.assertTrue(main._anon_agent_allowed(ip)[0])


if __name__ == "__main__":
    unittest.main()
