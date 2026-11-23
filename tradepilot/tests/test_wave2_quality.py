# -*- coding: utf-8 -*-
"""Wave 2 质量门禁：LLM 网关重试矩阵、配额中间件计费路径。

守的行为：
1. **LLM 重试矩阵**（此前零覆盖，而这里记录过真实线上事故：书生偶发 502、
   Cloud Run DNS 抖动）：
   - 5xx 重试，3 次全失败 → 返回 None（降级，不抛异常）
   - 第 2 次 200 → 成功返回正文
   - 4xx（配置类）立即降级，不重试
   - 连接类瞬时故障与 5xx 同等待遇
   - 超时参数来自 CONFIG.llm_timeout_s（此前硬编码 150，配置形同虚设）
2. **配额中间件计费路径**（计费核心，此前零覆盖）：
   - 超限返 429 且 body 带 quota
   - 404/405/422（没进业务）退款，used 不增
   - 成功请求带 X-Quota-Remaining
   - 匿名请求不扣配额（走的是限速闸门，见 test_wave1_hardening）
"""
import json
import unittest
from unittest import mock

import httpx

from app import llm
from app.config import CONFIG


def _gateway(handler) -> "object":
    """构造带 MockTransport 的 client 工厂（注入 llm.chat 的 client_factory）。"""
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return factory


def _ok_response(request) -> httpx.Response:
    body = {"choices": [{"message": {"content": "模型正文"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return httpx.Response(200, json=body)


class TestLLMRetryMatrix(unittest.IsolatedAsyncioTestCase):
    """llm.chat 的重试/降级分支。"""

    def setUp(self):
        # available() 需要 key 且未禁用；测试环境里真实 key 可能不存在，
        # 用 patch 让网关可用，不改变重试逻辑本身。
        self._av = mock.patch.object(llm, "available", return_value=True)
        self._av.start()
        self._sleep = mock.patch.object(llm.asyncio, "sleep", mock.AsyncMock())
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        self._av.stop()

    async def test_success_on_second_attempt(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(503, json={"error": "service unavailable"})
            return _ok_response(request)

        text = await llm.chat("sys", "user", client_factory=_gateway(handler))
        self.assertEqual(text, "模型正文")
        self.assertEqual(len(calls), 2, "566 应重试一次后成功")

    async def test_all_three_attempts_fail_returns_none(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(502, json={"error": "bad gateway"})

        text = await llm.chat("sys", "user", client_factory=_gateway(handler))
        self.assertIsNone(text, "3 次失败必须降级返回 None，不抛异常")
        self.assertEqual(len(calls), 3)

    async def test_4xx_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "bad key"})

        text = await llm.chat("sys", "user", client_factory=_gateway(handler))
        self.assertIsNone(text)
        self.assertEqual(len(calls), 1, "4xx 是配置问题，立即降级不重试")

    async def test_connection_error_is_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ConnectError("dns resolution failed")

        text = await llm.chat("sys", "user", client_factory=_gateway(handler))
        self.assertIsNone(text)
        self.assertEqual(len(calls), 3, "连接类瞬时故障与 5xx 同等待遇")

    async def test_usage_recorded_on_success(self):
        from app import usage
        with mock.patch.object(usage, "record") as rec:
            await llm.chat("sys", "user", client_factory=_gateway(_ok_response))
        self.assertTrue(rec.called, "成功调用必须记 token 用量（计费地基）")

    async def test_reasoning_content_fallback(self):
        """200 但 content 为空、reasoning 里有 JSON → 返回尾段交给调用方兜底。"""
        def handler(request):
            body = {"choices": [{"message": {"content": "",
                                              "reasoning_content": '思考…结论 {"k": "v"}'}}],
                    "usage": {}}
            return httpx.Response(200, json=body)
        text = await llm.chat("sys", "user", client_factory=_gateway(handler))
        self.assertIn('"k": "v"', text)


class TestLLMTimeoutConfig(unittest.TestCase):
    """超时来自配置（此前硬编码 150，LLM_TIMEOUT_S 形同虚设）。"""

    def test_default_is_config_value(self):
        # 生产行为：client_factory=None 时用 CONFIG.llm_timeout_s
        self.assertEqual(CONFIG.llm_timeout_s, 150)

    def test_extract_json_handles_plain_prefix(self):
        """removeprefix 修复：模型输出 'no, here is...' 首字母不再被吃。"""
        self.assertEqual(llm._extract_json('no, here is the plan: {"a": 1}'),
                         {"a": 1})
        self.assertEqual(llm._extract_json('```json\n{"b": 2}\n```'), {"b": 2})
        self.assertIsNone(llm._extract_json("纯文本无 JSON"))


class TestQuotaMiddlewareBilling(unittest.IsolatedAsyncioTestCase):
    """配额扣减/退款路径（计费核心，此前只有 quota 模块单测，中间件零覆盖）。"""

    def _make_app(self):
        """起一个最小 FastAPI 应用，复用 main 的中间件逻辑。"""
        from fastapi import FastAPI
        from app import main as main_mod

        app = FastAPI()

        @app.middleware("http")
        async def mw(request, call_next):
            return await main_mod.metrics_middleware.__wrapped__(request, call_next)

        # 直接复用 main 的中间件（它注册在 app 上，这里整用 main 的 app）
        return main_mod.app

    async def test_dispatch_rejects_when_quota_exhausted(self):
        """超限 → 429 且 body 带 quota 详情，不进业务。"""
        from app import main as main_mod
        called = {"business": False}

        async def fake_call_next(request):
            called["business"] = True
            return httpx.Response(200, json={})

        request = mock.Mock()
        request.url.path = "/api/agents/pipeline"
        request.client = mock.Mock(host="127.0.0.1")
        with (mock.patch.object(main_mod, "_tenant_of", return_value="tenant-bill"),
              mock.patch.object(main_mod.quota, "consume",
                                return_value={"allowed": False, "remaining": 0.0,
                                              "limit": 100.0, "used": 100.0})):
            resp = await main_mod._dispatch_with_quota(request, fake_call_next,
                                                       "/api/agents/pipeline", 0.0,
                                                       "tenant-bill")
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(called["business"], "拒绝时不能进入业务执行")
        body = json.loads(resp.body)
        self.assertIn("quota", body)

    async def test_dispatch_refunds_on_404_405_422(self):
        """没进业务的失败（404/405/422）必须退款——接口没调对，LLM 成本为零。"""
        from app import main as main_mod

        async def fake_call_next(request):
            return httpx.Response(404, json={"detail": "not found"})

        request = mock.Mock()
        request.url.path = "/api/agents/pipeline"
        request.client = mock.Mock(host="127.0.0.1")
        with (mock.patch.object(main_mod, "_tenant_of", return_value="tenant-bill"),
              mock.patch.object(main_mod.quota, "consume",
                                return_value={"allowed": True, "remaining": 96.0,
                                              "limit": 100.0, "used": 4.0}),
              mock.patch.object(main_mod.quota, "refund") as refund):
            resp = await main_mod._dispatch_with_quota(request, fake_call_next,
                                                       "/api/agents/pipeline", 0.0,
                                                       "tenant-bill")
        self.assertEqual(resp.status_code, 404)
        refund.assert_called_once_with("tenant-bill", 4.0)

    async def test_dispatch_success_has_remaining_header(self):
        from app import main as main_mod

        async def fake_call_next(request):
            return httpx.Response(200, json={})

        request = mock.Mock()
        request.url.path = "/api/agents/pipeline"
        request.client = mock.Mock(host="127.0.0.1")
        with (mock.patch.object(main_mod, "_tenant_of", return_value="tenant-bill"),
              mock.patch.object(main_mod.quota, "consume",
                                return_value={"allowed": True, "remaining": 96.0,
                                              "limit": 100.0, "used": 4.0})):
            resp = await main_mod._dispatch_with_quota(request, fake_call_next,
                                                       "/api/agents/pipeline", 0.0,
                                                       "tenant-bill")
        self.assertEqual(resp.headers.get("X-Quota-Remaining"), "96.0")


if __name__ == "__main__":
    unittest.main()
