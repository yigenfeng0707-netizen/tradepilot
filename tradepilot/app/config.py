# Copyright 2026 TradePilot project. Apache-2.0.
"""运行配置：全部来自环境变量，绝不把密钥写进代码或库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # LLM 网关（可选：无 key 时编排器走规则兜底，作品依然全流程可跑）
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "glm-4-flash"))

    # 抓取策略
    fetch_timeout_s: int = 45
    browser_headless: bool = True
    per_host_min_interval_s: float = 3.0  # 限速：对目标站点保持礼貌
    max_items_per_scan: int = 12

    # 来源存证库
    db_path: str = field(default_factory=lambda: os.environ.get("TRADEPILOT_DB", ""))

    def db_file(self) -> str:
        if not self.db_path:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.db_path = os.path.join(base, "assets", "provenance.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        return self.db_path


CONFIG = Config()
