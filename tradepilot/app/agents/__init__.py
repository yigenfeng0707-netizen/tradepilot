# Copyright 2026 TradePilot project. Apache-2.0.
"""产品层 Agent 包（M1 起）：市场洞察 / 智能客服。

红线见 docs/production-integration.md：Agent 只准通过 app.tools 取数；
任何非实时内容必须带 data_quality 标记（real / sample / sample_faq）。
"""
from . import customer_service, market_insight

__all__ = ["market_insight", "customer_service"]
