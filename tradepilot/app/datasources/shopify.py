# Copyright 2026 TradePilot project. Apache-2.0.
"""Shopify Admin API 适配器（低门槛平台，真实服务端联调用）。

为什么是 Shopify：Shopee/Lazada 入驻需要企业资质与真实店铺，而 Shopify
Partner development store 免费、即开即用——用它把"连接 → 归属 → 店铺级
调用"整条链路在**真实服务端**跑通。授权语义与 Shopee/Lazada 同构
（授权 → 令牌 → 归属 → 调用），练完这个，接任何开放平台都顺。

模式选择（诚实说明）：Shopify 有两条授权路径——
- OAuth 授权码：App 走重定向回调（public app 分发场景）；
- **自定义 App Token**：卖家在自己店铺后台 Settings → Apps and sales
  channels → Develop apps 创建 App、配 scope、Install，直接得到 Admin API
  access token（`shpat_` 前缀，长期有效，无需回调）。
本适配器实现**自定义 App Token 模式**：测试店场景下用户自己就是店主，
贴 token 即连——比模拟一条用不上的 OAuth 重定向诚实得多。开发店与正式店
此路径完全一致（正式店走 paid app 分发时再补 OAuth 路径）。

Shopify REST 信封：HTTP 2xx = 成功；4xx/5xx body 带 `{"errors": ...}`。
这与 Shopee（200 + error 字段）/ Lazada（200 + code 字段）不同——三家
"成功"判定恰好构成三种形态，各自独立测试守护，不许互相抄。

令牌口径：自定义 App 的 Admin API token 不会自动过期（卸载 App 或手动
rotate 才失效）。进程内缓存给 180 天窗口并如实标注"缓存口径"——重启即
需重贴，与 Shopee/Lazada 的重启语义一致，不假装持久化。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import marketplace_auth as mauth
from .openapi_http import ApiResult, request_json

API_VERSION = "2026-10"      # Shopify 版本化 API（季度版本），在文档确认存在
API_ROOT = "https://{shop}/admin/api/{version}"

# 店铺级业务路径白名单——我们自己的网关闸门（只读业务域），
# 不是 Shopify 官方路径声明；具体接口以 Admin API 文档为准。
SHOP_SCOPED_PREFIXES = ("/shop.json", "/products", "/orders",
                        "/customers", "/inventory", "/locations")

# 缓存窗口：Shopify 自定义 App token 长期有效，这里只是进程内缓存的
# 保守保鲜期（过期后重新贴 token 即可——重贴是幂等的）。
_TOKEN_TTL_S = 180 * 24 * 3600


def normalize_shop(shop: str) -> str:
    """店铺域名归一：接受 `xxx.myshopify.com` / `https://xxx.myshopify.com`
    / 裸名 `xxx`（自动补 .myshopify.com）。API host 必须是店铺域名。"""
    shop = shop.strip().lower()
    shop = shop.removeprefix("https://").removeprefix("http://")
    shop = shop.split("/")[0]
    if not shop:
        return ""
    if "." not in shop:
        shop = f"{shop}.myshopify.com"
    return shop


def _headers(token: str) -> dict[str, str]:
    return {"X-Shopify-Access-Token": token,
            "Accept": "application/json"}


async def call(shop: str, token: str, path: str, params: dict[str, Any] | None = None,
               *, method: str = "GET",
               timeout_s: float = 30.0,
               transport: httpx.AsyncBaseTransport | None = None) -> ApiResult:
    """调一个 Shopify Admin REST 接口。

    `shop` 是店铺域名（normalize_shop 归一后），`path` 形如 `/shop.json`
    （API 版本前缀由本模块拼接，调用方不拼——拼错版本号是常见坑）。
    """
    shop = normalize_shop(shop)
    params = dict(params or {})
    if not shop or "." not in shop:
        return ApiResult(ok=False, error_code="bad_shop",
                         error_msg=f"店铺域名不合法：{shop!r}")
    if not path.startswith("/"):
        return ApiResult(ok=False, error_code="bad_path",
                         error_msg=f"path 必须以 / 开头：{path!r}")
    if "?" in path:
        return ApiResult(ok=False, error_code="bad_path",
                         error_msg="path 不能带 query string")
    if not token:
        return ApiResult(ok=False, error_code="shop_auth_required",
                         error_msg="需要 Admin API access token（店铺后台 "
                                   "Develop apps → Install 获得，shpat_ 前缀）")

    url = API_ROOT.format(shop=shop, version=API_VERSION) + path
    async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
        result = await request_json(client, method, url, params=params,
                                    headers=_headers(token))

    # Shopify 信封：2xx 成功；4xx/5xx 的 body 是 {"errors": ...}（字符串或
    # 结构体都有可能）。错误信息原样带回，不吞不猜。
    if not result.ok and result.data and isinstance(result.data, dict):
        errors = result.data.get("errors")
        if errors:
            result.error_msg = str(errors)
    return result


async def verify_and_connect(token: str, shop: str, *,
                             transport: httpx.AsyncBaseTransport | None = None,
                             ) -> ApiResult:
    """验证 token 可用（调 /shop.json），成功即入进程内 TOKENS + 返回店铺名。

    验证失败不落任何状态——"连上"必须是真的能调 API，不是 token 格式对。
    """
    shop = normalize_shop(shop)
    if not shop or "." not in shop:
        return ApiResult(ok=False, error_code="bad_shop",
                         error_msg=f"店铺域名不合法：{shop!r}")
    token = token.strip()
    if not token:
        return ApiResult(ok=False, error_code="empty_token",
                         error_msg="token 不能为空")

    result = await call(shop, token, "/shop.json", transport=transport)
    if result.ok:
        # /shop.json 响应形如 {"shop": {...}}——店铺字段在 shop 键下
        shop_data = (result.data or {}).get("shop") or {}
        mauth.TOKENS.put(mauth.Token(
            access_token=token, refresh_token="",
            expire_at=time.time() + _TOKEN_TTL_S,
            shop_id=shop, platform="shopify"))
        result.data = {"shop_id": shop,
                       "shop_name": str(shop_data.get("name") or ""),
                       "country": str(shop_data.get("country_name") or ""),
                       "note": "令牌存进程内缓存，服务重启后需重新连接（连接关系已持久保留）"}
    return result
