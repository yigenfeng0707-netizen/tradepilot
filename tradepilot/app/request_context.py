# Copyright 2026 TradePilot project. Apache-2.0.
"""请求级上下文（M4）：把当前租户带给深层调用链。

为什么用 ContextVar 而不是给 4 个 agent + pipeline 的 run() 全部加
tenant_id 参数：agents 的签名是产品契约，为记账改一整条链，每一层
都会多一个"必须记得传"的参数——漏传一层记账就静默失真。ContextVar
在 middleware 设置一次，协程内任意深度可读，请求间天然隔离。

边界：WebSocket 不走 HTTP middleware（无认证，已注明），ws 上下文
读到的 tenant 是 None = 匿名口径，usage 记入 anonymous 键。
"""
from __future__ import annotations

from contextvars import ContextVar

current_tenant_id: ContextVar[str | None] = ContextVar(
    "current_tenant_id", default=None)
