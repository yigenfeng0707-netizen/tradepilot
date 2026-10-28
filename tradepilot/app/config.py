# Copyright 2026 TradePilot project. Apache-2.0.
"""运行配置：全部来自环境变量，绝不把密钥写进代码或库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _read_local_key() -> str:
    """本地密钥文件（.llm_key，已 gitignore）：单人开发用，优先级低于环境变量。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".llm_key")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


@dataclass
class Config:
    # LLM 网关（默认书生 InternAI 聚合网关，OpenAI 兼容，多模型聚合。
    # Key 来源：环境变量 LLM_API_KEY > 本地 .llm_key 文件；均不落代码库。
    # 无 key 时编排器走规则兜底，作品依然全流程可跑）
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "LLM_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get(
        "LLM_API_KEY", "") or _read_local_key())
    llm_model: str = field(default_factory=lambda: os.environ.get(
        "LLM_MODEL", "Atria-Dawn"))

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
