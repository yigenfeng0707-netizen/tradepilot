# Copyright 2026 TradePilot project. Apache-2.0.
"""Shopee 开放平台 HTTP 客户端（M3）。

协议细节见 `marketplace_auth.py`（签名）——本模块只负责：
拼参数 → 发请求 → **按 Shopee 的响应结构判成败** → 原样带回错误码。

Shopee 响应结构：`{"error": "", "msg": "", "response": {...}}`。
- `error` 为空 = 成功；
- `error` 非空 = 业务错误（如 `error_param`、`error_auth`）——HTTP 仍是 200，
  **只看 HTTP 状态码会把业务错误当成成功**，这是接开放平台最常见的坑。

三条硬规矩（继承 marketplace_auth.py）：
1. **没凭证就是没凭证**：`load_credentials()` 返回 None 时 `call()` 直接返回
   `configured=False` 的失败结果，**不发请求**——绝不发一个必然 403 的请求
   来"证明试过了"；
2. **店铺令牌缺失是显式失败**：Shopee 店铺接口必须带 shop 级 access_token，
   没授权过就调用会得到签名/权限错误。这里在发请求**之前**就拦下并说明
   "需要先完成店铺授权"，省卖家一次盲猜；
3. **限速**：Shopee 按 shop 限 QPS，这里对平台保持最小请求间隔。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import marketplace_auth as mauth
from .openapi_http import ApiResult, request_json

BASE_URL = "https://partner.shopeemobile.com"
# 对平台的最小请求间隔。Shopee 官方限流按接口分档（公开 10 QPS / 店铺 10 QPS
# 量级），取保守值——被限流的代价（429 + 退避）远大于慢一点。
_MIN_INTERVAL_S = 0.2
_last_request_at = 0.0

# 店铺级接口必须有 shop access_token 才能调。
SHOP_SCOPED_PREFIXES = ("/api/v2/product/", "/api/v2/order/", "/api/v2/logistics/",
                        "/api/v2/shop/")


async def _respect_rate_limit() -> None:
    """对平台的最小请求间隔（进程内成立；多实例各自计，代价可接受）。"""
    global _last_request_at
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _last_request_at)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request_at = time.monotonic()


async def call(path: str, params: dict[str, Any] | None = None, *,
               method: str = "GET",
               token: mauth.Token | None = None,
               timeout_s: float = 30.0,
               transport: httpx.AsyncBaseTransport | None = None,
               ) -> ApiResult:
    """调一个 Shopee 开放平台接口。

    `path` 必须是纯路径（不带 query），如 `/api/v2/product/get_item_list`。
    店铺级接口需传入已授权的 `token`；公开接口（如 auth/token/get）传 None。
    """
    params = dict(params or {})
    if not path.startswith("/"):
        return ApiResult(ok=False, error_code="bad_path",
                         error_msg=f"path 必须以 / 开头：{path!r}")
    if "?" in path:
        return ApiResult(ok=False, error_code="bad_path",
                         error_msg="path 不能带 query string——签名会用错 base string")

    creds = mauth.load_credentials("shopee")
    if creds is None:
        return ApiResult(ok=False, error_code="not_configured",
                         error_msg="Shopee 凭证未配置（SHOPEE_PARTNER_ID / "
                                   "SHOPEE_PARTNER_KEY）——未接入就是未接入，"
                                   "不冒充已连接")
    if any(path.startswith(p) for p in SHOP_SCOPED_PREFIXES) and token is None:
        return ApiResult(ok=False, error_code="shop_auth_required",
                         error_msg="该接口是店铺级接口，需要先完成店铺授权拿到 "
                                   "access_token（/api/integrations/shopee/auth-url "
                                   "生成授权链接）")

    access_token = token.access_token if token else ""
    shop_id = token.shop_id if token else ""
    timestamp = mauth.shopee_now()
    sign = mauth.shopee_sign(creds.partner_key, creds.partner_id, path,
                             timestamp, access_token, shop_id)

    query = {"partner_id": creds.partner_id, "timestamp": timestamp, "sign": sign,
             **params}
    if access_token:
        query["access_token"] = access_token
    if shop_id:
        query["shop_id"] = shop_id

    await _respect_rate_limit()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout_s,
                                 transport=transport) as client:
        result = await request_json(client, method, path, params=query)

    if result.ok:
        body = result.data or {}
        error = str(body.get("error") or "")
        if error:
            result.ok = False
            result.error_code = error
            result.error_msg = str(body.get("msg") or "")
            result.data = None
        else:
            result.data = body.get("response") if "response" in body else body
    return result


async def exchange_auth_code(code: str, main_merchant_id: str = "", *,
                             store: bool = False,
                             transport: httpx.AsyncBaseTransport | None = None,
                             ) -> ApiResult:
    """用授权回调的 code 换店铺令牌（/api/v2/auth/token/get）。

    默认**不自动入库**：授权是一次性动作，静默存起来会让"哪个店铺授权过"
    变得不可考。产品流程（端点）显式传 store=True 才进 TOKENS——
    入库决策在调用方，token 明文仍不进响应体。
    """
    creds = mauth.load_credentials("shopee")
    if creds is None:
        return ApiResult(ok=False, error_code="not_configured",
                         error_msg="Shopee 凭证未配置")
    path = "/api/v2/auth/token/get"
    timestamp = mauth.shopee_now()
    sign = mauth.shopee_sign(creds.partner_key, creds.partner_id, path, timestamp)
    params: dict[str, Any] = {"partner_id": creds.partner_id, "timestamp": timestamp,
                              "sign": sign, "code": code}
    if main_merchant_id:
        params["main_merchant_id"] = main_merchant_id

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0,
                                 transport=transport) as client:
        result = await request_json(client, "POST", path, params=params)
    if result.ok:
        body = result.data or {}
        if body.get("error"):
            result.ok = False
            result.error_code = str(body["error"])
            result.error_msg = str(body.get("msg") or "")
        else:
            resp = body.get("response") or {}
            token = mauth.Token(
                access_token=str(resp.get("access_token") or ""),
                refresh_token=str(resp.get("refresh_token") or ""),
                expire_at=time.time() + int(resp.get("expire_in") or 0),
                shop_id=str(resp.get("shop_id") or ""),
                platform="shopee")
            if store and token.access_token:
                mauth.TOKENS.put(token)
            result.data = {"token": token.as_dict(),
                           "shop_id": token.shop_id,
                           "has_refresh": bool(token.refresh_token)}
    return result


def auth_url(redirect_path: str = "") -> str:
    """生成店铺授权页链接（卖家点进去点"确认授权"，回调带 code 回来）。"""
    creds = mauth.load_credentials("shopee")
    if creds is None:
        return ""
    path = "/api/v2/shop/auth_partner"
    timestamp = mauth.shopee_now()
    sign = mauth.shopee_sign(creds.partner_key, creds.partner_id, path, timestamp)
    redirect = redirect_path or creds.redirect
    from urllib.parse import quote
    return (f"{BASE_URL}{path}?partner_id={creds.partner_id}"
            f"&timestamp={timestamp}&sign={sign}&redirect={quote(redirect)}")
