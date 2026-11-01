# Copyright 2026 TradePilot project. Apache-2.0.
"""开放平台鉴权层单测：签名串拼接 + 凭证降级 + token 过期判定。

**测什么、不测什么**——写清楚以免误读覆盖率：
- 测：签名串（base_string）逐字符正确、参数排序稳定、签名格式合规、
       无凭证时如实返回 None、token 过期判定含 5 分钟余量。
- 不测：与平台服务端算出的签名是否一致。这需要真实凭证打通一次接口，
       单测做不到。base_string 的断言是**手工可验算**的，签名值只做
       回归锁（防手滑改动），不作为协议一致性证明。
"""
from __future__ import annotations

import unittest

from app.datasources import marketplace_auth as auth


class TestShopeeSigning(unittest.TestCase):
    def test_public_base_string_is_hand_verifiable(self):
        # 手算：123 + /api/v2/product/get_item_list + 1700000000
        base = auth.shopee_base_string(
            123, "/api/v2/product/get_item_list", 1700000000)
        self.assertEqual(base, "123/api/v2/product/get_item_list1700000000")

    def test_shop_base_string_appends_token_then_shop_id(self):
        # 顺序是 token 在前、shop_id 在后。反了平台就签不过，且只报 error_sign。
        base = auth.shopee_base_string(
            123, "/api/v2/product/get_item_list", 1700000000,
            access_token="tok", shop_id=456)
        self.assertEqual(
            base, "123/api/v2/product/get_item_list1700000000tok456")

    def test_path_must_not_contain_query_string(self):
        # 带 query 会算进签名 → 与服务端不一致。这里锁住"传入即错误拼接"的行为，
        # 提醒调用方自己保证 path 干净（不做静默剥离，静默剥离会掩盖调用方 bug）。
        base = auth.shopee_base_string(1, "/api/v2/x?a=1", 1700000000)
        self.assertIn("?a=1", base)

    def test_sign_is_lowercase_hex_64(self):
        sign = auth.shopee_sign("secret", 1, "/api/v2/x", 1700000000)
        self.assertEqual(len(sign), 64)
        self.assertEqual(sign, sign.lower())
        int(sign, 16)  # 不是合法 hex 会抛 ValueError

    def test_sign_changes_with_every_component(self):
        base = auth.shopee_sign("secret", 1, "/api/v2/x", 1700000000)
        variants = {
            auth.shopee_sign("secret2", 1, "/api/v2/x", 1700000000),
            auth.shopee_sign("secret", 2, "/api/v2/x", 1700000000),
            auth.shopee_sign("secret", 1, "/api/v2/y", 1700000000),
            auth.shopee_sign("secret", 1, "/api/v2/x", 1700000001),
            auth.shopee_sign("secret", 1, "/api/v2/x", 1700000000,
                             access_token="tok"),
        }
        self.assertNotIn(base, variants)
        self.assertEqual(len(variants), 5)

    def test_timestamp_is_seconds(self):
        now = auth.shopee_now()
        # 秒级时间戳在 2001~2286 之间是 10 位
        self.assertEqual(len(str(now)), 10)


class TestLazadaSigning(unittest.TestCase):
    def test_base_string_sorts_keys_and_concats_kv(self):
        # 手算：/test + a1 + b2 + c3（按 key 字典序，不按插入顺序）
        base = auth.lazada_base_string(
            "/test", {"c": "3", "a": "1", "b": "2"})
        self.assertEqual(base, "/testa1b2c3")

    def test_sign_key_is_excluded(self):
        with_sign = auth.lazada_base_string(
            "/test", {"a": "1", "sign": "DEADBEEF"})
        self.assertEqual(with_sign, "/testa1")

    def test_empty_and_none_values_skipped(self):
        base = auth.lazada_base_string(
            "/test", {"a": "1", "b": "", "c": None})
        self.assertEqual(base, "/testa1")

    def test_numeric_values_stringified(self):
        base = auth.lazada_base_string(
            "/test", {"ts": 1700000000123, "limit": 20})
        self.assertEqual(base, "/testlimit20ts1700000000123")

    def test_sign_is_uppercase_hex_64(self):
        sign = auth.lazada_sign("secret", "/test", {"a": "1"})
        self.assertEqual(len(sign), 64)
        self.assertEqual(sign, sign.upper())
        int(sign, 16)

    def test_sorting_is_stable_across_insertion_orders(self):
        one = auth.lazada_base_string(
            "/p", {"b": "2", "a": "1", "c": "3"})
        two = auth.lazada_base_string(
            "/p", {"c": "3", "b": "2", "a": "1"})
        self.assertEqual(one, two)

    def test_timestamp_is_milliseconds(self):
        now = auth.lazada_now()
        self.assertEqual(len(str(now)), 13)


class TestCredentialsDegradation(unittest.TestCase):
    def setUp(self):
        self._saved = {k: __import__("os").environ.pop(k, None)
                       for k in ("SHOPEE_PARTNER_ID", "SHOPEE_PARTNER_KEY",
                                 "SHOPEE_REDIRECT", "LAZADA_APP_KEY",
                                 "LAZADA_APP_SECRET", "LAZADA_REDIRECT")}

    def tearDown(self):
        import os
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_missing_returns_none(self):
        self.assertIsNone(auth.load_credentials("shopee"))
        self.assertIsNone(auth.load_credentials("lazada"))

    def test_half_configured_returns_none_not_partial(self):
        # 只填了 ID 没填 Key：返回 None，而不是一个"看起来能用"的半空凭证。
        import os
        os.environ["SHOPEE_PARTNER_ID"] = "123"
        self.assertIsNone(auth.load_credentials("shopee"))

    def test_full_shopee_credentials(self):
        import os
        os.environ["SHOPEE_PARTNER_ID"] = "123"
        os.environ["SHOPEE_PARTNER_KEY"] = "k"
        creds = auth.load_credentials("shopee")
        self.assertIsNotNone(creds)
        self.assertTrue(creds.complete)
        self.assertEqual(creds.partner_id, "123")

    def test_unknown_platform_returns_none(self):
        self.assertIsNone(auth.load_credentials("amazon"))
        self.assertIsNone(auth.load_credentials(""))


class TestTokenStore(unittest.TestCase):
    def test_expiry_includes_margin(self):
        import time
        token = auth.Token(access_token="t", platform="shopee", shop_id="1",
                           expire_at=time.time() + 200)  # 只剩 200s
        # 余量 300s → 已视为过期，避免"刚判断有效、请求到达时已失效"
        self.assertTrue(token.expired())

    def test_valid_token_not_expired(self):
        import time
        token = auth.Token(access_token="t", platform="shopee", shop_id="1",
                           expire_at=time.time() + 3600)
        self.assertFalse(token.expired())

    def test_empty_token_always_expired(self):
        import time
        token = auth.Token(access_token="", platform="lazada", shop_id="1",
                           expire_at=time.time() + 3600)
        self.assertTrue(token.expired())

    def test_store_returns_none_for_expired(self):
        import time
        store = auth.TokenStore()
        store.put(auth.Token(access_token="t", platform="shopee", shop_id="1",
                             expire_at=time.time() + 100))
        self.assertIsNone(store.get("shopee", "1"))

    def test_store_isolates_by_shop(self):
        import time
        store = auth.TokenStore()
        store.put(auth.Token(access_token="a", platform="shopee", shop_id="1",
                             expire_at=time.time() + 3600))
        self.assertIsNotNone(store.get("shopee", "1"))
        self.assertIsNone(store.get("shopee", "2"))
        self.assertIsNone(store.get("lazada", "1"))

    def test_drop(self):
        import time
        store = auth.TokenStore()
        store.put(auth.Token(access_token="a", platform="lazada", shop_id="9",
                             expire_at=time.time() + 3600))
        self.assertEqual(store.size(), 1)
        store.drop("lazada", "9")
        self.assertEqual(store.size(), 0)


if __name__ == "__main__":
    unittest.main()
