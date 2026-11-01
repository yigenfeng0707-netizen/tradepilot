# -*- coding: utf-8 -*-
"""Shopee / Lazada 开放平台 HTTP 客户端的回归测试（M3）。

守的红线：
1. **没凭证不发请求**：configured=False 是显式失败，不是"发个请求试试"；
2. **业务错误不是成功**：Shopee `error` 非空 / Lazada `code != "0"` 时 HTTP
   仍是 200——只看状态码会把业务错误当成功，这是接开放平台最常见的坑；
3. **店铺级接口缺令牌在发请求之前拦下**；
4. **重试只给 GET + 429/5xx/网络异常**：POST 重发可能造成重复下单。

说明：这些测试用 httpx.MockTransport 验证的是"我们发什么、怎么判成败"，
**不能证明平台服务端接受我们的签名**——那必须用真实凭证联调一次。
"""
import asyncio
import json
import time
import unittest
from unittest import mock

import httpx

from app.datasources import lazada, marketplace_auth as mauth, openapi_http, shopee

CREDS = mauth.Credentials(partner_id="1001", partner_key="shopee-key",
                          app_key="laz-app", app_secret="laz-secret")


def _ok_client(handler):
    transport = httpx.MockTransport(handler)
    return transport


def _record_handler(recorder, responder):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        return responder(request)
    return handler


class TestShopeeClient(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_sends_nothing(self):
        """没凭证就是没凭证：不发请求，返回 not_configured。"""
        sent: list[httpx.Request] = []
        with mock.patch.object(mauth, "load_credentials", return_value=None):
            r = await shopee.call("/api/v2/product/get_item_list", transport=_ok_client(
                _record_handler(sent, lambda q: httpx.Response(200, json={}))))
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "not_configured")
        self.assertEqual(len(sent), 0)          # 一个包都没发出去

    async def test_shop_scoped_requires_token_before_sending(self):
        """店铺级接口缺令牌在发请求之前拦下，省卖家一次盲猜。"""
        sent: list[httpx.Request] = []
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await shopee.call("/api/v2/product/get_item_list", transport=_ok_client(
                _record_handler(sent, lambda q: httpx.Response(200, json={}))))
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "shop_auth_required")
        self.assertEqual(len(sent), 0)

    async def test_path_with_query_rejected(self):
        """query 必须走 params——path 带 ? 会签错，宁可不发。"""
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await shopee.call("/api/v2/x?a=1")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "bad_path")

    async def test_success_parses_response_envelope(self):
        sent: list[httpx.Request] = []
        token = mauth.Token(access_token="tok", shop_id="shop-1",
                            expire_at=9999999999, platform="shopee")

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "", "msg": "",
                                             "response": {"items": [1, 2]}})

        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await shopee.call("/api/v2/product/get_item_list",
                                  {"offset": 0}, token=token,
                                  transport=_ok_client(_record_handler(sent, responder)))
        self.assertTrue(r.ok, r.error_msg)
        self.assertEqual(r.data, {"items": [1, 2]})
        req = sent[0]
        url = str(req.url)
        # 签名与公共参数必须在 query 里
        for key in ("partner_id", "timestamp", "sign", "access_token", "shop_id"):
            self.assertIn(key, url)

    async def test_business_error_with_http_200_is_not_success(self):
        """HTTP 200 + error 非空 = 业务失败。只看状态码就上当了。"""
        sent: list[httpx.Request] = []
        token = mauth.Token(access_token="tok", shop_id="s",
                            expire_at=9999999999, platform="shopee")

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "error_param",
                                             "msg": "invalid category_id",
                                             "response": {}})

        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await shopee.call("/api/v2/shop/get_shop_info", token=token,
                                  transport=_ok_client(_record_handler(sent, responder)))
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "error_param")
        self.assertIn("invalid category_id", r.error_msg)
        self.assertEqual(len(sent), 1)          # 业务错误不重试

    async def test_429_retries_get_then_succeeds(self):
        calls = {"n": 0}
        token = mauth.Token(access_token="tok", shop_id="s",
                            expire_at=9999999999, platform="shopee")

        def responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={})
            return httpx.Response(200, json={"error": "", "msg": "", "response": {"ok": 1}})

        with mock.patch.object(mauth, "load_credentials", return_value=CREDS), \
             mock.patch.object(openapi_http, "_RETRY_BACKOFF_S", 0):
            r = await shopee.call("/api/v2/shop/get_shop_info", token=token,
                                  transport=_ok_client(
                                      _record_handler([], responder)))
        self.assertTrue(r.ok)
        self.assertEqual(calls["n"], 2)

    async def test_post_failure_is_not_retried(self):
        """POST 重发可能造成重复下单——一次失败就带回错误。"""
        calls = {"n": 0}

        def responder(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await shopee.call("/api/v2/auth/token/get", method="POST",
                                  transport=_ok_client(_record_handler([], responder)))
        self.assertFalse(r.ok)
        self.assertEqual(calls["n"], 1)


class TestLazadaClient(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_sends_nothing(self):
        sent: list[httpx.Request] = []
        with mock.patch.object(mauth, "load_credentials", return_value=None):
            r = await lazada.call("/product/get", transport=_ok_client(
                _record_handler(sent, lambda q: httpx.Response(200, json={}))))
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "not_configured")
        self.assertEqual(len(sent), 0)

    async def test_success_code_zero_string(self):
        """code 为字符串 "0" 才算成功；签名必须是大写 hex（Lazada 官方要求）。"""
        sent: list[httpx.Request] = []

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "0", "data": {"products": []}})
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await lazada.call("/product/get", {"limit": 10}, transport=_ok_client(
                _record_handler(sent, responder)))
        self.assertTrue(r.ok, r.error_msg)
        self.assertEqual(r.data, {"products": []})
        sign = sent[0].url.params["sign"]
        self.assertTrue(sign, "sign 必须出现在 query 里")
        self.assertEqual(sign, sign.upper())

    async def test_business_error_with_http_200_is_not_success(self):
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "10",
                                             "message": "session expired"})
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await lazada.call("/product/get", transport=_ok_client(
                _record_handler([], responder)))
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "10")
        self.assertIn("session expired", r.error_msg)

    async def test_numeric_code_zero_is_tolerated(self):
        """文档说 code 是字符串，但 JSON 里出现数字 0 也宽容地按成功处理——
        这是对实现的宽容假设，未经真实服务端验证（见模块 docstring）。"""
        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await lazada.call("/product/get", transport=_ok_client(
                _record_handler([], responder)))
        self.assertTrue(r.ok, r.error_msg)
        self.assertEqual(r.data, {"ok": True})

    async def test_refresh_token_returns_new_token(self):
        """刷新成功返回新令牌信息。注意 `as_dict()` 刻意不回传
        access_token/refresh_token 明文（防令牌泄漏进日志/响应体），
        所以这里断言非敏感字段 + 过期时间。"""
        sent: list[httpx.Request] = []

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "0", "data": {
                "access_token": "new-at", "refresh_token": "new-rt",
                "expires_in": 604800}})
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            r = await lazada.refresh_token("old-rt", transport=_ok_client(
                _record_handler(sent, responder)))
        self.assertTrue(r.ok, r.error_msg)
        tok = r.data["token"]
        self.assertEqual(tok["platform"], "lazada")
        self.assertFalse(tok["expired"])
        # expire_at ≈ now + 604800（允许几秒误差）
        self.assertAlmostEqual(tok["expire_at"], time.time() + 604800, delta=60)
        # 令牌明文绝不能出现在响应 data 里
        self.assertNotIn("new-at", json.dumps(r.data))


class TestSignatureWiring(unittest.IsolatedAsyncioTestCase):
    async def test_shopee_request_carries_correct_sign(self):
        """发出的签名必须等于用同一套参数离线算出的签名——拼参数环节不能漂移。"""
        seen: dict[str, str] = {}

        def responder(request: httpx.Request) -> httpx.Response:
            for k, v in request.url.params.items():
                seen[k] = str(v)
            return httpx.Response(200, json={"error": "", "msg": "", "response": {}})

        token = mauth.Token(access_token="AT", shop_id="SHOP",
                            expire_at=9999999999, platform="shopee")
        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            await shopee.call("/api/v2/shop/get_shop_info", token=token,
                              transport=_ok_client(_record_handler([], responder)))
        expected = mauth.shopee_sign(CREDS.partner_key, CREDS.partner_id,
                                     "/api/v2/shop/get_shop_info",
                                     int(seen["timestamp"]), "AT", "SHOP")
        self.assertEqual(seen["sign"], expected)

    async def test_lazada_request_carries_correct_sign(self):
        seen: dict[str, str] = {}

        def responder(request: httpx.Request) -> httpx.Response:
            for k, v in request.url.params.items():
                seen[k] = str(v)
            return httpx.Response(200, json={"code": "0", "data": {}})

        with mock.patch.object(mauth, "load_credentials", return_value=CREDS):
            await lazada.call("/product/get", {"limit": 5},
                              transport=_ok_client(_record_handler([], responder)))
        params = {k: v for k, v in seen.items() if k != "sign"}
        expected = mauth.lazada_sign(CREDS.app_secret, "/product/get", params)
        self.assertEqual(seen["sign"], expected)
        self.assertEqual(seen["sign"], seen["sign"].upper())


if __name__ == "__main__":
    unittest.main()
