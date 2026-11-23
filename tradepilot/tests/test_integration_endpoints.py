# -*- coding: utf-8 -*-
"""店铺授权集成端点的回归测试（M3-③ 接线）。

守的红线：
1. **授权是租户动作**：未登录 401——店铺 API 之后消耗的是该租户的配额；
2. **fail honest**：凭证未配置 = 409（"有功能没条件"），与不支持的平台
   404（"没有这个平台"）分开——404 会让运营者去查路由，409 直接告诉他
   该去平台后台建 App；
3. **成功入库失败带回**：exchange 成功进 TOKENS（进程内、重启即清），
   平台业务错误（HTTP 200 + error 信封）如实透传 error_code；
4. **令牌明文永不回传**（as_dict 无明文的既有红线延续到端点响应）。
"""
import asyncio
import dataclasses
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.config import CONFIG  # noqa: E402
from app import auth, shops  # noqa: E402
from app.datasources import marketplace_auth as mauth, shopee  # noqa: E402
from app.main import (integration_auth_url, integration_exchange,  # noqa: E402
                      integration_shops, integration_shop_call,
                      _require_user)

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-integration-test-")
auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir)
shops.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")


def setUpModule():
    auth.init_db()
    shops.init_db()


def _req(token: str | None = None) -> SimpleNamespace:
    headers = {} if token is None else {"authorization": f"Bearer {token}"}
    return SimpleNamespace(headers=headers)


def _mock_json_response(body: dict):
    """request_json 走 client.request() + resp.json()/is_success/status_code，
    mock 必须对着这些接口来。"""
    resp = mock.Mock()
    resp.json = mock.Mock(return_value=body)
    resp.is_success = True
    resp.status_code = 200
    resp.text = json.dumps(body)
    return resp


def _patch_client(resp):
    client = mock.AsyncMock()
    client.request = mock.AsyncMock(return_value=resp)
    cm = mock.AsyncMock()
    cm.__aenter__ = mock.AsyncMock(return_value=client)
    cm.__aexit__ = mock.AsyncMock(return_value=False)
    # load_credentials 实时读 env——凭证也要一起 mock
    env = mock.patch.dict("os.environ", {"SHOPEE_PARTNER_ID": "1001",
                                         "SHOPEE_PARTNER_KEY": "key"})
    client_patch = mock.patch.object(shopee.httpx, "AsyncClient",
                                     mock.Mock(return_value=cm))
    return _Stack(env, client_patch)


class _Stack:
    """叠加多个上下文管理器（contextlib.ExitStack 的测试内简化版）。"""

    def __init__(self, *cms):
        self._cms = cms

    def __enter__(self):
        from contextlib import ExitStack
        self._stack = ExitStack()
        for c in self._cms:
            self._stack.enter_context(c)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class _User:
    """测试夹具：注册真实租户拿令牌（授权动作归属租户，不是匿名）。"""

    def __init__(self, label: str):
        reg = auth.register(f"integration-{label}@example.com",
                            "password123", "集成测试公司")
        assert reg.get("ok"), reg
        self.tenant_id = reg["tenant_id"]
        self.token = auth.issue_access_token(reg["user_id"], self.tenant_id)


class TestAuthUrl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = _User("authurl")

    def test_unauthenticated_401(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(integration_auth_url("shopee", _req()))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_unknown_platform_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(integration_auth_url("tiktok", _req(self.u.token)))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_not_configured_409(self):
        """409 ≠ 404：功能在，条件没有——运营者该去建 App 而不是查路由。"""
        with mock.patch.object(shopee, "auth_url", return_value=""):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(integration_auth_url("shopee", _req(self.u.token)))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_configured_returns_url(self):
        with mock.patch.object(shopee, "auth_url",
                               return_value="https://partner.shopeemobile.com/api/v2/shop/auth_partner?partner_id=1"):
            out = asyncio.run(integration_auth_url("shopee", _req(self.u.token)))
        self.assertTrue(out["auth_url"].startswith("https://"))
        self.assertEqual(out["platform"], "shopee")


class TestExchange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.u = _User("exchange")
        mauth.TOKENS._tokens.clear()        # 进程内缓存，测试间清干净

    def _run(self, platform: str, ok: bool = True):
        from app.datasources import lazada
        from app.datasources.openapi_http import ApiResult
        data = {"token": {"platform": platform, "shop_id": "shop-1",
                          "expire_at": 9999999999, "expired": False},
                "shop_id": "shop-1", "has_refresh": True} if ok else None
        result = ApiResult(ok=ok, data=data)
        if not ok:
            result.error_code, result.error_msg = "error_param", "bad code"
        mod = shopee if platform == "shopee" else lazada
        # main.py `from .datasources import shopee` 拿到的是同一模块对象，
        # patch 模块属性即对端点生效
        with mock.patch.object(mod, "exchange_auth_code",
                               mock.AsyncMock(return_value=result)):
            out = asyncio.run(integration_exchange(
                platform,
                SimpleNamespace(code="one-time-code", main_merchant_id="",
                                redirect=""),
                _req(self.u.token)))
        return out, mod

    def test_success_stores_and_no_secret_in_response(self):
        """走真实的 exchange_auth_code（mock httpx 层）：成功 → TOKENS 入库
        且明文不进响应体；授权关系落库到本租户名下。"""
        from app import shops
        body = {"error": "", "msg": "", "response": {
            "access_token": "real-at", "refresh_token": "real-rt",
            "expire_in": 14400, "shop_id": "shop-1"}}
        with _patch_client(_mock_json_response(body)):
            out = asyncio.run(integration_exchange(
                "shopee",
                SimpleNamespace(code="one-time-code", main_merchant_id="",
                                redirect=""), _req(self.u.token)))
        self.assertTrue(out["success"], out)
        self.assertNotIn("real-at", str(out))               # 明文永不回传
        self.assertEqual(mauth.TOKENS.size(), 1)            # 已入进程内缓存
        # 授权关系归属本租户——"这个店铺是谁授权的"从此可考
        mine = shops.shops_of(self.u.tenant_id, "shopee")
        self.assertEqual([s["shop_id"] for s in mine], ["shop-1"])
        self.assertTrue(shops.is_authorized(self.u.tenant_id, "shopee", "shop-1"))
        # 其他租户对这个店铺没有授权记录
        self.assertFalse(shops.is_authorized("tenant-other", "shopee", "shop-1"))

    def test_platform_error_passthrough(self):
        """HTTP 200 + error 信封 = 业务失败：错误码原样透传成 502+detail，
        不吞成 500 也不丢平台码。"""
        body = {"error": "error_param", "msg": "bad code", "response": {}}
        with _patch_client(_mock_json_response(body)):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(integration_exchange(
                    "shopee",
                    SimpleNamespace(code="one-time-code", main_merchant_id="",
                                    redirect=""), _req(self.u.token)))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["error_code"], "error_param")
        self.assertEqual(ctx.exception.detail["error_msg"], "bad code")

    def test_unknown_platform_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(integration_exchange(
                "tiktok", SimpleNamespace(code="x", main_merchant_id="",
                                          redirect=""), _req(self.u.token)))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_empty_code_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(integration_exchange(
                "shopee", SimpleNamespace(code="  ", main_merchant_id="",
                                          redirect=""), _req(self.u.token)))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_require_user_rejects_anonymous(self):
        with self.assertRaises(HTTPException) as ctx:
            _require_user(_req())
        self.assertEqual(ctx.exception.status_code, 401)


class TestShopsListing(unittest.TestCase):
    """店铺清单：只返回本租户授权的——租户 A 的店铺对租户 B 不存在。"""

    @classmethod
    def setUpClass(cls):
        cls.u = _User("shops")

    def setUp(self):
        shops_cfg = shops.CONFIG
        with shops._db() as conn:
            conn.execute("DELETE FROM shop_authorizations")
        self._cfg = shops_cfg

    def test_lists_only_own_authorizations(self):
        from app import shops as S
        S.authorize(self.u.tenant_id, "shopee", "shop-A")
        S.authorize("tenant-other", "shopee", "shop-B")     # 别人的
        out = asyncio.run(integration_shops("shopee", _req(self.u.token)))
        ids = [s["shop_id"] for s in out["shops"]]
        self.assertEqual(ids, ["shop-A"])
        self.assertFalse(any(s["shop_id"] == "shop-B" for s in out["shops"]))

    def test_token_alive_honest(self):
        """token 存活状态是进程内缓存口径——没有 token 就如实 false。"""
        from app import shops as S
        S.authorize(self.u.tenant_id, "shopee", "shop-A")
        out = asyncio.run(integration_shops("shopee", _req(self.u.token)))
        self.assertFalse(out["shops"][0]["token_alive"])     # 没换过 token，如实

    def test_empty_list_for_new_tenant(self):
        u2 = _User("shops2")
        out = asyncio.run(integration_shops("shopee", _req(u2.token)))
        self.assertEqual(out["shops"], [])

    def test_unauthenticated_401(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(integration_shops("shopee", _req()))
        self.assertEqual(ctx.exception.status_code, 401)


class TestShopCall(unittest.TestCase):
    """店铺级业务调用网关：闸门顺序 + 白名单 + 错误透传。

    闸门语义的红线：
    - 无授权关系 404（跨租户与不存在同响应，与店铺清单口径一致）；
    - 关系在但令牌没了 409（重新授权的信号，不冒充 404 也不假装能用）；
    - auth 类接口 403（店铺令牌不得穿过网关调 auth）。
    """

    @classmethod
    def setUpClass(cls):
        cls.u = _User("shopcall")

    def setUp(self):
        with shops._db() as conn:
            conn.execute("DELETE FROM shop_authorizations")
        mauth.TOKENS._tokens.clear()
        import time as _time
        self._time = _time
        mauth.TOKENS.put(mauth.Token(
            access_token="call-at", refresh_token="call-rt",
            expire_at=_time.time() + 9999, shop_id="shop-C",
            platform="shopee"))

    def _authorize(self):
        shops.authorize(self.u.tenant_id, "shopee", "shop-C")

    def _run(self, path="/api/v2/shop/get_shop_info", platform="shopee",
             shop_id="shop-C", method="GET", params=None, token="DEFAULT"):
        from types import SimpleNamespace as NS
        body = NS(path=path, params=params or {}, method=method)
        if token == "DEFAULT":
            token = self.u.token
        return asyncio.run(integration_shop_call(
            platform, shop_id, body, _req(token)))

    def test_unauthenticated_401(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run(token=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_unknown_platform_404(self):
        self._authorize()
        with self.assertRaises(HTTPException) as ctx:
            self._run(platform="tiktok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_not_authorized_404(self):
        """没有授权关系 → 404；别人的店铺同样 404（不泄露存在性）。"""
        self._authorize()
        with self.assertRaises(HTTPException) as ctx:
            self._run(shop_id="shop-other-tenant")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_never_authorized_shop_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run(shop_id="shop-never-seen")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_token_missing_409(self):
        """授权关系在、进程内令牌没了（重启/过期）→ 409 重新授权信号。"""
        self._authorize()
        mauth.TOKENS.drop("shopee", "shop-C")
        with self.assertRaises(HTTPException) as ctx:
            self._run()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("重新", str(ctx.exception.detail))

    def test_expired_token_409(self):
        """过期 token = 没 token（TokenStore.get 口径），同样 409。"""
        self._authorize()
        mauth.TOKENS.put(mauth.Token(
            access_token="old", refresh_token="r",
            expire_at=self._time.time() - 10, shop_id="shop-C",
            platform="shopee"))
        with self.assertRaises(HTTPException) as ctx:
            self._run()
        self.assertEqual(ctx.exception.status_code, 409)

    def test_path_outside_whitelist_403(self):
        """auth 类接口不得穿过店铺调用网关（拿店铺令牌调 auth 是红线）。"""
        self._authorize()
        with self.assertRaises(HTTPException) as ctx:
            self._run(path="/api/v2/auth/token/get")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_bad_method_400(self):
        self._authorize()
        with self.assertRaises(HTTPException) as ctx:
            self._run(method="DELETE")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_success_returns_data(self):
        """走真实 shopee.call（mock httpx 层）：成功 → data 原样带回，
        access_token/sign 等签名参数不进响应体。"""
        self._authorize()
        body = {"error": "", "msg": "", "response": {"shop_name": "Toko A"}}
        with _patch_client(_mock_json_response(body)):
            out = self._run()
        self.assertTrue(out["success"])
        self.assertEqual(out["data"], {"shop_name": "Toko A"})
        self.assertNotIn("call-at", str(out))

    def test_platform_error_502_passthrough(self):
        """HTTP 200 + error 信封 = 业务失败：502 + 平台错误码原样透传。"""
        self._authorize()
        body = {"error": "error_auth", "msg": "invalid token", "response": {}}
        with _patch_client(_mock_json_response(body)):
            with self.assertRaises(HTTPException) as ctx:
                self._run()
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["error_code"], "error_auth")
        self.assertEqual(ctx.exception.detail["error_msg"], "invalid token")


if __name__ == "__main__":
    unittest.main()
