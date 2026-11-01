# Copyright 2026 TradePilot project. Apache-2.0.
"""Lazada 开放平台 HTTP 客户端（M3）。

协议细节见 `marketplace_auth.py`（签名）。本模块差异点：

- **时间戳是毫秒**（Shopee 是秒）——混用会拿到 `error_sign` 且极难排查；
- **签名是大写 hex**（Shopee 小写）；
- 响应结构：`{"code": "0", "data": {...}, "message": "...", "type": ...}`。
  code 为 `"0"` 才是成功（数字 `0` 也宽容地按成功处理——文档说 code 是
  字符串，此宽容行为未经真实服务端验证，联调时留意）。
  只看 HTTP 状态码会把业务错误当成成功。
- 刷新令牌接口在 `/auth/token/refresh`，与业务接口同签名规则。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import marketplace_auth as mauth
from .openapi_http import ApiResult, request_json

BASE_URL = "https://api.lazada.com/rest"

# 店铺级业务接口前缀白名单——这是**我们自己的调用网关闸门**（防止拿店铺
# 令牌穿过代理去调 auth 类接口），不是对 Lazada 路径体系的官方声明。
# 前缀按 Lazada 文档常见业务域取保守集合；具体接口是否可用以真实联调为准。
SHOP_SCOPED_PREFIXES = ("/product", "/order", "/logistics")


def _sign_params(creds: mauth.Credentials, path: str,
                 params: dict[str, Any], access_token: str) -> dict[str, Any]:
    """拼 Lazada 公共参数 + 签名。`access_token` 参与签名但不放进签名串以外的
    排序体——它本身就是个普通参数，按 key 排序自然参与。"""
    all_params: dict[str, Any] = {
        "app_key": creds.app_key,
        "sign_method": "sha256",
        "timestamp": mauth.lazada_now(),
        **params,
    }
    if access_token:
        all_params["access_token"] = access_token
    all_params["sign"] = mauth.lazada_sign(creds.app_secret, path, all_params)
    return all_params


def _unwrap(result: ApiResult) -> ApiResult:
    """按 Lazada 响应结构判成败。错误码（如 `10` 会话超时、
    `3` 参数缺失）原样带回——它们在 Lazada 文档里各有明确含义。"""
    if not result.ok:
        return result
    body = result.data or {}
    code = str(body.get("code", "0"))
    if code == "0":
        result.data = body.get("data") if "data" in body else body
        return result
    result.ok = False
    result.error_code = code
    result.error_msg = str(body.get("message") or "")
    result.data = None
    return result


async def call(path: str, params: dict[str, Any] | None = None, *,
               method: str = "GET",
               token: mauth.Token | None = None,
               timeout_s: float = 30.0,
               transport: httpx.AsyncBaseTransport | None = None,
               ) -> ApiResult:
    """调一个 Lazada 开放平台接口（`path` 形如 `/product/get`）。"""
    params = dict(params or {})
    if not path.startswith("/"):
        return ApiResult(ok=False, error_code="bad_path",
                         error_msg=f"path 必须以 / 开头：{path!r}")

    creds = mauth.load_credentials("lazada")
    if creds is None:
        return ApiResult(ok=False, error_code="not_configured",
                         error_msg="Lazada 凭证未配置（LAZADA_APP_KEY / "
                                   "LAZADA_APP_SECRET）——未接入就是未接入，"
                                   "不冒充已连接")

    access_token = token.access_token if token else ""
    query = _sign_params(creds, path, params, access_token)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout_s,
                                 transport=transport) as client:
        result = await request_json(client, method, path, params=query)
    return _unwrap(result)


def _token_from(body: dict[str, Any]) -> mauth.Token:
    """从令牌接口响应里取令牌。字段可能在顶层也可能在 data 里——
    两种形状都认，别赌文档：赌错一次排查半天。"""
    source = body.get("data") if isinstance(body.get("data"), dict) else body
    return mauth.Token(
        access_token=str(source.get("access_token") or ""),
        refresh_token=str(source.get("refresh_token") or ""),
        expire_at=time.time() + int(source.get("expires_in") or 0),
        platform="lazada")


AUTH_BASE_URL = "https://auth.lazada.com"


def auth_url(redirect_path: str = "") -> str:
    """生成店铺授权页链接（卖家点"确认授权"后回调带 code）。

    格式按 Lazada 官方 OAuth 授权页（auth.lazada.com/oauth/authorize）。
    **未实测**——签名/信封已有 MockTransport 测试，授权页跳转本身
    属于 runbook 联调验证项（docs/ops-readiness.md 第 3 节）。
    """
    creds = mauth.load_credentials("lazada")
    if creds is None:
        return ""
    redirect = redirect_path or creds.redirect
    from urllib.parse import quote
    return (f"{AUTH_BASE_URL}/oauth/authorize?response_type=code"
            f"&client_key={creds.app_key}&redirect_uri={quote(redirect)}")


async def exchange_auth_code(code: str, *, redirect: str = "",
                             store: bool = False,
                             transport: httpx.AsyncBaseTransport | None = None,
                             ) -> ApiResult:
    """授权回调 code 换店铺令牌（/auth/token/create）。store 语义同 shopee：
    默认不入库，产品流程显式传 store=True 才进 TOKENS。"""
    creds = mauth.load_credentials("lazada")
    if creds is None:
        return ApiResult(ok=False, error_code="not_configured",
                         error_msg="Lazada 凭证未配置")
    params: dict[str, Any] = {"code": code,
                              "uuid": _uuid_or_empty(redirect)}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0,
                                 transport=transport) as client:
        result = await request_json(client, "GET", "/auth/token/create",
                                    params=_sign_params(creds, "/auth/token/create",
                                                        params, ""))
    result = _unwrap(result)
    if result.ok:
        token = _token_from(result.raw or {})
        if not token.access_token:
            result.ok = False
            result.error_code = "no_token_in_response"
            result.error_msg = "授权接口未返回 access_token"
        else:
            if store:
                mauth.TOKENS.put(token)
            result.data = {"token": token.as_dict(),
                           "shop_id": token.shop_id,
                           "has_refresh": bool(token.refresh_token)}
    return result


async def refresh_token(refresh_token: str, *,
                        transport: httpx.AsyncBaseTransport | None = None,
                        ) -> ApiResult:
    """刷新店铺令牌（/auth/token/refresh）。token 过期是常态不是异常。"""
    creds = mauth.load_credentials("lazada")
    if creds is None:
        return ApiResult(ok=False, error_code="not_configured",
                         error_msg="Lazada 凭证未配置")
    path = "/auth/token/refresh"
    params: dict[str, Any] = {"refresh_token": refresh_token}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0,
                                 transport=transport) as client:
        result = await request_json(client, "GET", path,
                                    params=_sign_params(creds, path, params, ""))
    result = _unwrap(result)
    if result.ok:
        token = _token_from(result.raw or {})
        if not token.access_token:
            result.ok = False
            result.error_code = "no_token_in_response"
            result.error_msg = "刷新接口未返回 access_token"
        else:
            result.data = {"token": token.as_dict()}
    return result


def _uuid_or_empty(value: str) -> str:
    """/auth/token/create 的 uuid 参数：防重放的可选字段，没有就传空串
    （签名层会跳过空值，Lazada 侧也接受缺省）。"""
    return value.strip()
