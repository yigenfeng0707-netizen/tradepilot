# -*- coding: utf-8 -*-
"""Shopify 适配器测试（M4 低门槛平台联调层）。

守的红线：
1. **连接即验证**：verify_and_connect 必须调真实 API（/shop.json）成功才落
   TOKENS——"连上"是真的能调 API，不是 token 格式对；
2. **信封形态独立**：Shopify 是 HTTP 2xx=成功 / 4xx body={"errors":...}，
   与 shopee（200+error 字段）/lazada（200+code）不同，不许互相抄判定；
3. **token 明文不进**结果数据；
4. 域名归一：裸名自动补 .myshopify.com，带协议/路径也能归一。
"""
import asyncio
import json
import unittest
from unittest import mock

from app.datasources import marketplace_auth as mauth, shopify


def _mock_json_response(body: dict, status: int = 200):
    resp = mock.Mock()
    resp.json = mock.Mock(return_value=body)
    resp.is_success = status < 400
    resp.status_code = status
    resp.text = json.dumps(body)
    return resp


def _patch_client(resp):
    client = mock.AsyncMock()
    client.request = mock.AsyncMock(return_value=resp)
    cm = mock.AsyncMock()
    cm.__aenter__ = mock.AsyncMock(return_value=client)
    cm.__aexit__ = mock.AsyncMock(return_value=False)
    patcher = mock.patch.object(shopify.httpx, "AsyncClient",
                                mock.Mock(return_value=cm))
    return patcher, client


class TestNormalizeShop(unittest.TestCase):
    def test_bare_name_gets_domain(self):
        self.assertEqual(shopify.normalize_shop("my-store"),
                         "my-store.myshopify.com")

    def test_protocol_and_path_stripped(self):
        self.assertEqual(
            shopify.normalize_shop("https://My-Store.myshopify.com/admin/"),
            "my-store.myshopify.com")

    def test_empty_rejected(self):
        self.assertEqual(shopify.normalize_shop("  "), "")


class TestVerifyAndConnect(unittest.TestCase):
    def setUp(self):
        mauth.TOKENS._tokens.clear()

    def test_success_puts_token_and_returns_shop_name(self):
        body = {"shop": {"name": "Test Store", "country_name": "United States"}}
        patcher, client = _patch_client(_mock_json_response(body))
        with patcher:
            out = asyncio.run(shopify.verify_and_connect(
                "shpat_abc123", "test-store"))
        self.assertTrue(out.ok, out)
        self.assertEqual(out.data["shop_name"], "Test Store")
        self.assertEqual(out.data["shop_id"], "test-store.myshopify.com")
        self.assertNotIn("shpat_abc123", str(out))       # 明文不回传
        # 已入缓存且按 (platform, shop) 寻址
        tok = mauth.TOKENS.get("shopify", "test-store.myshopify.com")
        self.assertIsNotNone(tok)
        self.assertEqual(tok.access_token, "shpat_abc123")
        # 请求确实带上了 token 头与归一域名
        req = client.request.call_args
        self.assertIn("X-Shopify-Access-Token", req.kwargs["headers"])
        self.assertIn("/admin/api/2026-10/shop.json", req.args[1])

    def test_invalid_token_fails_and_lands_nothing(self):
        """401 → 失败；TOKENS 里没有任何东西——验证失败不落状态。"""
        patcher, _ = _patch_client(
            _mock_json_response({"errors": "Invalid API key"}, status=401))
        with patcher:
            out = asyncio.run(shopify.verify_and_connect(
                "shpat_bad", "test-store"))
        self.assertFalse(out.ok)
        self.assertIn("Invalid API key", out.error_msg)
        self.assertEqual(mauth.TOKENS.size(), 0)

    def test_empty_token_rejected_without_http(self):
        out = asyncio.run(shopify.verify_and_connect("  ", "test-store"))
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "empty_token")

    def test_bad_shop_rejected(self):
        out = asyncio.run(shopify.verify_and_connect("shpat_x", ""))
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "bad_shop")


class TestCall(unittest.TestCase):
    def test_success_returns_data(self):
        patcher, client = _patch_client(
            _mock_json_response({"products": []}))
        with patcher:
            out = asyncio.run(shopify.call(
                "test-store", "shpat_x", "/products.json", {"limit": "5"}))
        self.assertTrue(out.ok)
        self.assertEqual(out.data, {"products": []})
        req = client.request.call_args
        self.assertEqual(req.args[0], "GET")
        self.assertEqual(req.kwargs["params"].get("limit"), "5")

    def test_error_envelope_extracted(self):
        patcher, _ = _patch_client(
            _mock_json_response({"errors": "Not Found"}, status=404))
        with patcher:
            out = asyncio.run(shopify.call(
                "test-store", "shpat_x", "/products.json"))
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "http_404")
        self.assertIn("Not Found", out.error_msg)

    def test_query_in_path_rejected(self):
        out = asyncio.run(shopify.call(
            "test-store", "shpat_x", "/products.json?limit=5"))
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "bad_path")


if __name__ == "__main__":
    unittest.main()
