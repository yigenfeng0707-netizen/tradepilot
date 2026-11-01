# -*- coding: utf-8 -*-
"""产品层 Agent 的回归测试（M1）。

守的红线：
1. 真实数据可用时 data_quality="real" 且 provenance 穿透；
2. 真实数据不可得时，样本必须明示 data_quality="sample" + 失败原因，
   严格模式（allow_sample=False）宁可失败也不给样本；
3. 客服回复不得伪装成官方政策（data_quality="sample_faq" 恒定），
   LLM 不可用时模板兜底可用。
"""
import unittest
from unittest import mock

from app.agents import customer_service, market_insight
from app.tools.executor import ToolOutcome


def _outcome(items, band, currency="CNY", scan_pid=900):
    return ToolOutcome(True, "done", None, "真实采集", {
        "keyword_used": "狗床", "source": "dangdang", "currency": currency,
        "scan_provenance_id": scan_pid, "items": items, "price_band": band,
    })


class TestMarketInsightReal(unittest.IsolatedAsyncioTestCase):
    async def test_real_data_quality_and_tiers(self):
        items = [
            {"title": "狗床A", "price": 100.0, "currency": "CNY", "source": "dangdang",
             "source_url": "https://x/1", "provenance_id": 11},
            {"title": "狗床B", "price": 200.0, "currency": "CNY", "source": "dangdang",
             "source_url": "https://x/2", "provenance_id": 12},
            {"title": "狗床C", "price": 300.0, "currency": "CNY", "source": "dangdang",
             "source_url": "https://x/3", "provenance_id": 13},
        ]
        band = {"min": 100.0, "median": 200.0, "max": 300.0}
        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(return_value=_outcome(items, band))), \
             mock.patch.object(market_insight.llm, "available", return_value=False):
            r = await market_insight.run("宠物用品", marketplace="US")

        self.assertTrue(r["success"])
        self.assertEqual(r["data_quality"], "real")
        d = r["data"]
        self.assertEqual(d["data_quality"], "real")
        self.assertEqual(len(d["competitors"]), 3)
        # 存证穿透：搜索页 + 3 个单品
        self.assertEqual(d["provenance_ids"], [900, 11, 12, 13])
        # 三档按条数三等分
        self.assertEqual([t["tier"] for t in d["price_tiers"]],
                         ["大众价位", "中端价位", "高端价位"])
        self.assertEqual(d["generated_by"], "template")   # LLM 不可用 → 模板
        self.assertIn("100.00", d["summary"]["summary"])

    async def test_source_fallback_second_source(self):
        """第一个源失败，第二个源成功——errors 不吞，结果仍为 real。"""
        first = ToolOutcome(False, "error", None, "dangdang 拦截页", {})
        items = [{"title": "狗床A", "price": 50.0, "currency": "CNY",
                  "source": "newegg", "source_url": "https://x/1", "provenance_id": 21}]
        second = _outcome(items, {"min": 50.0, "median": 50.0, "max": 50.0})
        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(side_effect=[first, second])), \
             mock.patch.object(market_insight.llm, "available", return_value=False):
            r = await market_insight.run("宠物用品", marketplace="US")
        self.assertEqual(r["data_quality"], "real")
        self.assertEqual(r["data"]["competitors"][0]["source"], "newegg")


class TestMarketInsightFallback(unittest.IsolatedAsyncioTestCase):
    async def test_all_fail_returns_marked_sample(self):
        bad = ToolOutcome(False, "error", None, "源不可达", {})
        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(return_value=bad)), \
             mock.patch.object(market_insight.llm, "available", return_value=False):
            r = await market_insight.run("宠物用品", marketplace="US")
        self.assertTrue(r["success"])
        self.assertEqual(r["data_quality"], "sample")
        self.assertIn("样本数据", r["data"]["note"])
        self.assertIn("源不可达", r["data"]["note"])   # 失败原因必须可追溯
        self.assertEqual(r["data"]["provenance_ids"], [])

    async def test_strict_mode_refuses_sample(self):
        bad = ToolOutcome(False, "error", None, "源不可达", {})
        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(return_value=bad)):
            r = await market_insight.run("宠物用品", marketplace="US",
                                         allow_sample=False)
        self.assertFalse(r["success"])
        self.assertIsNone(r["data"])
        self.assertIn("严格模式", r["message"])

    async def test_unknown_category_no_sample(self):
        bad = ToolOutcome(False, "error", None, "源不可达", {})
        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(return_value=bad)):
            r = await market_insight.run("不存在的品类", marketplace="US")
        self.assertFalse(r["success"])

    async def test_empty_category_rejected(self):
        r = await market_insight.run("")
        self.assertFalse(r["success"])


class TestCustomerService(unittest.IsolatedAsyncioTestCase):
    async def test_dispute_and_angry(self):
        with mock.patch.object(customer_service.llm, "available", return_value=False):
            r = await customer_service.chat("这个破东西质量太差了，我要退款！", category="宠物用品")
        d = r["data"]
        self.assertEqual(d["emotion"]["type"], "angry")
        self.assertEqual(d["dispute"]["type"], "quality")   # 质量优先于退款（先命中）
        self.assertEqual(d["generated_by"], "template")
        self.assertEqual(d["data_quality"], "sample_faq")
        self.assertIn("处理动作", d["reply"])

    async def test_faq_match(self):
        with mock.patch.object(customer_service.llm, "available", return_value=False):
            r = await customer_service.chat("自动喂食器如何清洗？", category="宠物用品")
        self.assertGreaterEqual(len(r["data"]["faqs"]), 1)
        self.assertIn("清洗", r["data"]["faqs"][0]["question"])

    async def test_llm_unavailable_template_still_replies(self):
        with mock.patch.object(customer_service.llm, "available", return_value=False):
            r = await customer_service.chat("你好", category="3C数码", language="en")
        self.assertTrue(r["data"]["reply"])
        self.assertEqual(r["data"]["emotion"]["type"], "neutral")

    async def test_llm_reply_used_when_available(self):
        """LLM 在线时必须用 LLM 组织语言，且标记 generated_by=llm。"""
        with mock.patch.object(customer_service.llm, "available", return_value=True), \
             mock.patch.object(customer_service.llm, "chat",
                               mock.AsyncMock(return_value="亲，可拆卸清洗哦。")):
            r = await customer_service.chat("自动喂食器怎么清洗？", category="宠物用品")
        self.assertEqual(r["data"]["generated_by"], "llm")
        self.assertEqual(r["data"]["reply"], "亲，可拆卸清洗哦。")

    async def test_empty_message(self):
        r = await customer_service.chat("   ")
        self.assertFalse(r["success"])


class TestMarketInsightCategoryMapping(unittest.IsolatedAsyncioTestCase):
    """品类必须换成工具层英文键，否则 relevant() 的噪声过滤整层失效。

    这是个**静默**的质量问题：不报错，只是无关商品被算进价格带。
    实测口径：传中文时 `relevant()` 走"未知品类放行"分支，一条都不过滤。
    """

    async def test_tool_receives_english_category_key(self):
        seen: dict = {}

        async def fake_run(inp):
            seen.update(inp)
            return _outcome(
                [{"title": "狗床A", "price": 100.0, "currency": "CNY",
                  "source": "dangdang", "source_url": "https://x/1",
                  "provenance_id": 11}],
                {"min": 100.0, "median": 100.0, "max": 100.0})

        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(side_effect=fake_run)), \
             mock.patch.object(market_insight.llm, "available", return_value=False):
            await market_insight.run("宠物用品", marketplace="US")

        self.assertEqual(seen["category"], "pet")

    async def test_3c_alias_maps_to_electronics(self):
        seen: dict = {}

        async def fake_run(inp):
            seen.update(inp)
            return _outcome([], {"min": 0, "median": 0, "max": 0})

        with mock.patch.object(market_insight.competitor, "run",
                               mock.AsyncMock(side_effect=fake_run)), \
             mock.patch.object(market_insight.llm, "available", return_value=False):
            await market_insight.run("3C电子", marketplace="US")

        self.assertEqual(seen["category"], "electronics")


if __name__ == "__main__":
    unittest.main()
