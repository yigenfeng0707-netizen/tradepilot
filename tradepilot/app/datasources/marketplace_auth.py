# Copyright 2026 TradePilot project. Apache-2.0.
"""东南亚开放平台鉴权层：Shopee / Lazada 的签名与 token 管理。

**这里只做协议实现，不发任何请求**——签名算法是平台规范决定的纯函数，
可以在没有凭证、没有网络的情况下完整单测。真正的 HTTP 调用在
`shopee.py` / `lazada.py` 里，它们把这一层的输出拼进请求。

三条硬规矩（与 ads_report.py 同一套价值观）：

1. **没凭证就是没凭证**：`credentials()` 返回 `None`，调用方如实标注
   `configured: False`，绝不伪造一个能跑的假客户端；
2. **签名不猜**：两个平台的签名串拼接顺序完全不同（Shopee 是
   `partner_id + path + timestamp [+ token + shop_id]`，Lazada 是
   `path + 按 key 排序的 k+v 串联`），这里各写一份并各自单测，
   不抽公共函数——抽了就会有人"顺手统一"从而签错；
3. **token 过期是常态不是异常**：`TokenStore` 用 `expire_at` 判断，
   提前 5 分钟视为过期（时钟漂移 + 网络往返的余量），过期就刷新，
   刷新失败就返回 None 让上层降级。

⚠️ **测试边界说明**：本模块的测试验证的是"签名串拼接正确、签名是 64 位
十六进制、参数排序稳定"，**不能证明与平台服务端算出的签名一致**——
那必须用真实凭证打通一次接口才能确认。第一次联调失败时，第一嫌疑就是
这里，先打印 base_string 与平台文档逐字符比对。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

# Shopee 官方签名用秒级时间戳，Lazada 用毫秒级。混用会导致签名直接对不上，
# 且报错信息只有 "error_sign"，极难排查——所以这里显式命名，不做隐式换算。
_SHOPEE_TS_UNIT = 1
_LAZADA_TS_UNIT = 1000

# 提前刷新的余量：token 剩余寿命低于此值即视为过期。
# 5 分钟 = 一次最长请求耗时(45s) + 重试 + 时钟漂移的保守估计。
_REFRESH_MARGIN_S = 300


def _hmac_hex(secret: str, base_string: str) -> str:
    """HMAC-SHA256 → 小写十六进制。两个平台底层一致，只有大小写要求不同。"""
    return hmac.new(
        secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# --------------------------------------------------------------------------
# Shopee
# --------------------------------------------------------------------------

def shopee_base_string(partner_id: str | int, path: str,
                       timestamp: int, access_token: str = "",
                       shop_id: str | int = "") -> str:
    """Shopee 签名串。

    公开接口（店铺授权前）：`{partner_id}{path}{timestamp}`
    店铺接口（授权后）：    `{partner_id}{path}{timestamp}{access_token}{shop_id}`

    注意 `path` 必须是**不含 query string 的纯路径**（如 `/api/v2/product/get_item_list`），
    带上 `?a=1` 会导致签名与服务端不一致。
    """
    base = f"{partner_id}{path}{timestamp}"
    if access_token or shop_id:
        base = f"{base}{access_token}{shop_id}"
    return base


def shopee_sign(partner_key: str, partner_id: str | int, path: str,
                timestamp: int, access_token: str = "",
                shop_id: str | int = "") -> str:
    """Shopee 签名（小写 hex）。"""
    return _hmac_hex(partner_key,
                     shopee_base_string(partner_id, path, timestamp,
                                        access_token, shop_id))


def shopee_now() -> int:
    return int(time.time()) * _SHOPEE_TS_UNIT


# --------------------------------------------------------------------------
# Lazada
# --------------------------------------------------------------------------

# Lazada 的公共参数，参与签名。`sign` 本身不参与，`access_token` 参与。
_LAZADA_COMMON = ("app_key", "timestamp", "sign_method")


def lazada_base_string(api_path: str, params: Mapping[str, Any]) -> str:
    """Lazada 签名串：`{api_path}` + 按 key 字典序拼接的 `{k}{v}`。

    规则细节（踩过就知道）：
    - 按 key 的**字符串字典序**排序，不是按插入顺序；
    - 值一律转字符串，None 与空串都跳过（传空串会算进签名导致不一致）；
    - `sign` 字段必须排除，否则自己签自己；
    - 中文/特殊字符按原样参与，不做 URL 编码——编码发生在拼 URL 时，不在签名时。
    """
    parts = [api_path]
    for key in sorted(params):
        if key == "sign":
            continue
        value = params[key]
        if value is None or value == "":
            continue
        parts.append(f"{key}{value}")
    return "".join(parts)


def lazada_sign(app_secret: str, api_path: str, params: Mapping[str, Any]) -> str:
    """Lazada 签名（**大写** hex——官方要求大写，小写会被判签名错误）。"""
    return _hmac_hex(app_secret, lazada_base_string(api_path, params)).upper()


def lazada_now() -> int:
    return int(time.time()) * _LAZADA_TS_UNIT


# --------------------------------------------------------------------------
# 凭证与 token
# --------------------------------------------------------------------------

@dataclass
class Credentials:
    """一组平台凭证。`complete` 为假时上层必须降级，不得发请求。"""
    partner_id: str = ""
    partner_key: str = ""
    app_key: str = ""
    app_secret: str = ""
    redirect: str = ""

    @property
    def complete(self) -> bool:
        return bool((self.partner_id and self.partner_key)
                    or (self.app_key and self.app_secret))


def load_credentials(platform: str) -> Credentials | None:
    """从环境变量读凭证。读不到或不全 → 返回 None（**不返回半空的凭证**）。

    `platform` 取 `"shopee"` / `"lazada"`。为什么不用 config.py 的 dataclass：
    凭证是运行期可热更新的（轮换密钥后重启进程即可），放 dataclass 里会被
    `CONFIG` 单例在 import 时冻结。
    """
    platform = platform.strip().lower()
    if platform == "shopee":
        creds = Credentials(
            partner_id=os.environ.get("SHOPEE_PARTNER_ID", "").strip(),
            partner_key=os.environ.get("SHOPEE_PARTNER_KEY", "").strip(),
            redirect=os.environ.get("SHOPEE_REDIRECT", "").strip(),
        )
    elif platform == "lazada":
        creds = Credentials(
            app_key=os.environ.get("LAZADA_APP_KEY", "").strip(),
            app_secret=os.environ.get("LAZADA_APP_SECRET", "").strip(),
            redirect=os.environ.get("LAZADA_REDIRECT", "").strip(),
        )
    else:
        return None
    return creds if creds.complete else None


@dataclass
class Token:
    access_token: str = ""
    refresh_token: str = ""
    expire_at: float = 0.0          # 绝对时间戳（秒）
    shop_id: str = ""
    platform: str = ""

    def expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (not self.access_token) or (now >= self.expire_at - _REFRESH_MARGIN_S)

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "shop_id": self.shop_id,
                "expire_at": self.expire_at, "expired": self.expired()}


@dataclass
class TokenStore:
    """进程内 token 缓存。key 用 `(platform, shop_id)`。

    为什么不上持久化：token 刷新成本极低（一次 HTTP），而持久化引入
    "存了过期 token 还以为有效"的风险。多实例部署时各实例各自持有一份，
    代价只是多刷几次，换来的是不会出现陈旧状态。
    """
    _tokens: dict[tuple[str, str], Token] = field(default_factory=dict)

    def get(self, platform: str, shop_id: str) -> Token | None:
        token = self._tokens.get((platform, shop_id))
        if token is None or token.expired():
            return None
        return token

    def put(self, token: Token) -> None:
        self._tokens[(token.platform, token.shop_id)] = token

    def drop(self, platform: str, shop_id: str) -> None:
        self._tokens.pop((platform, shop_id), None)

    def size(self) -> int:
        return len(self._tokens)


# 全局单例：进程内共享，避免每个请求都重新拿 token。
TOKENS = TokenStore()
