# Copyright 2026 TradePilot project. Apache-2.0.
"""Shopify 联调脚本的编排测试（fake transport，不联网、不碰真实 token）。

11-19 曾临时用假 transport 验证过编排逻辑，但临时文件跑完即删，没有入库——
下次改脚本时无人守护。本文件把那次验证固化：
  1) 缺配置 / token 形态不对 → 退 2，且绝不发网络请求；
  2) connect 成功会往 TOKENS 放令牌（副作用），第 3 步店铺级调用才取得到；
  3) 四步顺序 connect → 归属 → 商品 → 订单，真实 token 不回显日志。
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import verify_shopify  # noqa: E402
from app.datasources import marketplace_auth as mauth  # noqa: E402
from app.datasources.openapi_http import ApiResult  # noqa: E402


class TestVerifyShopifyScript(unittest.TestCase):
    FAKE_TOKEN = "shpat_" + "a" * 24

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        env_patch = mock.patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("SHOPIFY_SHOP", None)
        os.environ.pop("SHOPIFY_TOKEN", None)

        env_file_patch = mock.patch.object(verify_shopify, "ENV_LOCAL", self.tmp / ".env.local")
        env_file_patch.start()
        self.addCleanup(env_file_patch.stop)

        self.result_file = self.tmp / "shopify_verify_result.json"
        result_patch = mock.patch.object(verify_shopify, "RESULT_FILE", self.result_file)
        result_patch.start()
        self.addCleanup(result_patch.stop)

    def test_missing_config_refuses_without_network(self):
        with mock.patch.object(verify_shopify.shopify, "verify_and_connect",
                               side_effect=AssertionError("不应发起网络请求")):
            code = asyncio.run(verify_shopify.main())
        self.assertEqual(code, 2)

    def test_bad_token_shape_refuses(self):
        os.environ["SHOPIFY_SHOP"] = "demo-store"
        os.environ["SHOPIFY_TOKEN"] = "not-a-shopify-token"
        with mock.patch.object(verify_shopify.shopify, "verify_and_connect",
                               side_effect=AssertionError("不应发起网络请求")):
            code = asyncio.run(verify_shopify.main())
        self.assertEqual(code, 2)

    def test_fake_transport_runs_four_steps_in_order(self):
        os.environ["SHOPIFY_SHOP"] = "demo-store"
        os.environ["SHOPIFY_TOKEN"] = self.FAKE_TOKEN
        shop = "demo-store.myshopify.com"
        calls: list[str] = []

        async def fake_verify(token: str, target: str) -> ApiResult:
            calls.append("connect")
            self.assertEqual(token, self.FAKE_TOKEN)
            self.assertEqual(target, shop)
            # 真函数会把 token 放进 TOKENS；mock 必须复现这个副作用，
            # 否则第 3 步店铺级调用会误报"令牌缓存丢失"。
            mauth.TOKENS.put(mauth.Token(
                access_token=token, refresh_token="",
                expire_at=time.time() + 3600,
                shop_id=target, platform="shopify"))
            return ApiResult(ok=True, data={
                "shop_id": target, "shop_name": "Demo", "country": "CN"})

        async def fake_call(shop_id: str, token: str, path: str, params):
            calls.append(f"call{path}")
            self.assertEqual(token, self.FAKE_TOKEN)
            if path == "/products":
                return ApiResult(ok=True, data={"products": [{"title": "Fake product"}]})
            if path == "/orders":
                return ApiResult(ok=True, data={"orders": []})
            return ApiResult(ok=False, error_code="unexpected_path")

        authorized: list[tuple[str, str, str]] = []

        def fake_authorize(tenant: str, platform: str, shop_id: str) -> None:
            calls.append("authorize")
            authorized.append((tenant, platform, shop_id))

        def fake_shops_of(tenant: str, platform: str) -> list[dict]:
            return [{"shop_id": shop}]

        patches = [
            mock.patch.object(verify_shopify.shopify, "verify_and_connect", new=fake_verify),
            mock.patch.object(verify_shopify.shopify, "call", new=fake_call),
            mock.patch.object(verify_shopify.shops, "authorize", new=fake_authorize),
            mock.patch.object(verify_shopify.shops, "shops_of", new=fake_shops_of),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(mauth.TOKENS.drop, "shopify", shop)

        out = io.StringIO()
        with redirect_stdout(out):
            code = asyncio.run(verify_shopify.main())
        text = out.getvalue()

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["connect", "authorize", "call/products", "call/orders"])
        self.assertEqual(authorized, [(verify_shopify.CHECK_TENANT, "shopify", shop)])
        self.assertIn("Fake product", text)
        self.assertIn("联调通过", text)
        self.assertNotIn(self.FAKE_TOKEN, text)
        self.assertIn("…", text)  # 掩码回显

        result = json.loads(self.result_file.read_text(encoding="utf-8"))
        self.assertTrue(result["verified"])
        self.assertEqual(result["products"], 1)
        self.assertEqual(result["orders"], 0)
        self.assertNotIn(self.FAKE_TOKEN, self.result_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
