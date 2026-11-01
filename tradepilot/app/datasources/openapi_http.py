# Copyright 2026 TradePilot project. Apache-2.0.
"""开放平台 HTTP 公共机制：超时、重试、结果信封。

只放**与平台无关**的机械部分。签名绝不下沉到这里——两个平台的签名
串拼接规则不同，放公共层就会有人"顺手统一"从而签错（见 marketplace_auth.py
的模块注释）。各平台只复用这里的：重试判定、退避、ApiResult 打包。

重试策略（刻意保守）：
- 只重试 `429 / 5xx / 网络异常`，且只重试 GET（POST 重发可能造成重复下单，
  在电商语境里是不可接受的副作用）；
- 最多 2 次尝试、固定 2 秒退避——平台限流时盲目加次只会更糟；
- 4xx（参数错/签名错/权限错）**不重试**：重发一个错误请求拿到的还是同一个错误，
  只是更晚知道。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 30.0
_RETRY_BACKOFF_S = 2.0


@dataclass
class ApiResult:
    """开放平台调用结果。**错误码原文保留**，不翻译不归并——
    平台的 error code 是卖家排查问题的第一线索，归并成"请求失败"等于毁证据。"""
    ok: bool
    http_status: int = 0
    data: dict[str, Any] | None = None
    error_code: str = ""
    error_msg: str = ""
    attempts: int = 1
    latency_s: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "http_status": self.http_status,
                "error_code": self.error_code, "error_msg": self.error_msg,
                "attempts": self.attempts,
                "latency_s": round(self.latency_s, 3),
                "data": self.data}


def retryable(status: int, method: str, exc: bool = False) -> bool:
    """是否值得重试。POST 一律不重试（副作用不可逆）。"""
    if method.upper() != "GET":
        return False
    if exc:                       # 网络层异常（超时/连接失败）
        return True
    return status == 429 or 500 <= status < 600


async def request_json(client: httpx.AsyncClient, method: str, url: str, *,
                       params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None,
                       max_attempts: int = 2,
                       backoff_s: float | None = None,
                       ) -> ApiResult:
    """发请求并解析 JSON，按策略重试。**不吞错误码**，全部带回来。

    `backoff_s=None` 表示运行时读模块常量（测试可 patch；写在默认参数里
    会在 def 时绑定，patch 就失效了）。
    `headers` 是请求头（如 Shopify 的 X-Shopify-Access-Token）——与签名
    无关的通用机械部分，放这里；签名参数仍由各平台自行拼。
    """
    delay = _RETRY_BACKOFF_S if backoff_s is None else backoff_s
    started = time.monotonic()
    attempts = 0
    last: ApiResult | None = None
    while attempts < max(1, max_attempts):
        attempts += 1
        try:
            resp = await client.request(method, url, params=params,
                                        headers=headers)
        except httpx.HTTPError as exc:
            last = ApiResult(ok=False, error_code="network_error",
                             error_msg=f"{type(exc).__name__}: {exc}",
                             attempts=attempts)
            if attempts < max_attempts and retryable(0, method, exc=True):
                await _sleep(delay)
                continue
            break
        latency = time.monotonic() - started
        try:
            payload = resp.json()
            if not isinstance(payload, dict):
                payload = {"_raw": payload}
        except ValueError:
            payload = {}
        last = ApiResult(ok=False, http_status=resp.status_code,
                         error_code=f"http_{resp.status_code}",
                         error_msg=resp.text[:300], attempts=attempts,
                         latency_s=latency, raw=payload, data=payload)
        # data 与 raw 都放：data 供上层按平台结构取业务字段，
        # raw 保留完整响应（排查签名/参数问题时给原始证据）。
        if resp.is_success:
            last.ok = True
            last.error_code = ""
            last.error_msg = ""
            return last
        if attempts < max_attempts and retryable(resp.status_code, method):
            await _sleep(delay)
            continue
        break
    assert last is not None
    last.latency_s = round(time.monotonic() - started, 3)
    return last


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
