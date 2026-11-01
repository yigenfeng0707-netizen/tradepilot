# Copyright 2026 TradePilot project. Apache-2.0.
"""产品层 Agent 基类（M1：从海丝出海项目迁入，重写数据来源）。

分层规则（见 docs/production-integration.md）：
- 产品层 Agent 只准通过 `app.tools.*` 拿数据，禁止直接读静态数据文件；
- 任何非真实抓取的内容必须在返回值里带 `data_quality`（"real" / "sample"），
  前端据此明示——生产环境里"假装有数据"比没有数据更糟。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# 数据质量标记：real = 真实抓取且带存证；sample = 样本数据（必须前端明示）
QUALITY_REAL = "real"
QUALITY_SAMPLE = "sample"
# generated = 模型/模板生成的文案，不是数据。与上面两类并列而非从属：
# 把生成内容标成 real 会让"可溯源"的承诺失效，标成 sample 又暗示它来自样本库。
QUALITY_GENERATED = "generated"


def wrap(agent: str, data: dict[str, Any], quality: str, message: str = "") -> dict[str, Any]:
    """统一响应包装：agent / data / data_quality / message / executed_at。"""
    return {
        "success": True,
        "agent": agent,
        "data": data,
        "data_quality": quality,
        "message": message,
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fail(agent: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """失败响应：宁可报错也不给样本数据充数（allow_sample=False 时使用）。"""
    return {"success": False, "agent": agent, "data": None,
            "data_quality": None, "message": message, "detail": detail or {}}
