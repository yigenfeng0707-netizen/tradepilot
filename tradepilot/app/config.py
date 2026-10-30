# Copyright 2026 TradePilot project. Apache-2.0.
"""运行配置：全部来自环境变量，绝不把密钥写进代码或库。

数据目录（存证库 + 任务记录）解析顺序：
1. 环境变量 TRADEPILOT_DATA_DIR（云上必设，如 /tmp/tradepilot）；
2. 仓库内 assets/（本地开发默认）；
3. 仓库目录不可写时自动回退到系统临时目录——Cloud Run / 只读根文件系统下
   容器不会因为写盘失败而崩溃（代价是实例重建后本地存证丢失，见 docs/gcp-deploy.md）。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_local_key() -> str:
    """本地密钥文件（.llm_key）：单人开发用，优先级低于环境变量。"""
    try:
        return (REPO_ROOT / ".llm_key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_data_dir() -> str:
    env = os.environ.get("TRADEPILOT_DATA_DIR", "").strip()
    if env:
        Path(env).mkdir(parents=True, exist_ok=True)
        return env
    local = REPO_ROOT / "assets"
    if _writable(local):
        return str(local)
    fallback = Path(tempfile.gettempdir()) / "tradepilot"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)


@dataclass
class Config:
    # LLM 网关（默认书生 InternAI 聚合网关，OpenAI 兼容，多模型聚合。
    # Key 来源：环境变量 LLM_API_KEY > 本地 .llm_key 文件；均不落代码库。
    # 无 key 时编排器走规则规划，作品依然全流程可跑）
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "LLM_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1"))
    llm_api_key: str = field(default_factory=lambda: os.environ.get(
        "LLM_API_KEY", "") or _read_local_key())
    llm_model: str = field(default_factory=lambda: os.environ.get(
        "LLM_MODEL", "Atria-Dawn"))
    llm_timeout_s: int = field(default_factory=lambda: int(os.environ.get(
        "LLM_TIMEOUT_S", "150")))
    # 关闭 LLM 的显式开关：评测复现（规则模式）与离线演示都用它，
    # 比"临时删 Key"可靠——避免评测环境不同导致口径漂移
    llm_disabled: bool = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_DISABLE_LLM", "") not in ("", "0", "false", "False"))

    # 抓取策略
    fetch_timeout_s: int = 45
    browser_headless: bool = True
    # 自定义 Chromium 路径（容器内系统浏览器 / 本地多版本共存时用）
    chromium_path: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_CHROMIUM", ""))
    per_host_min_interval_s: float = 3.0  # 限速：对目标站点保持礼貌
    max_items_per_scan: int = 12
    # robots.txt 合规闸门：默认开启。walmart.com 对通用 UA 禁止 /search，
    # 开启后该源会如实报错而不是"绕过"（详见 docs/gcp-deploy.md）
    respect_robots: bool = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_RESPECT_ROBOTS", "1") not in ("0", "false", "False"))

    # 数据目录（存证库 + 任务记录）
    data_dir: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_DATA_DIR", "") or _resolve_data_dir())
    db_path: str = field(default_factory=lambda: os.environ.get("TRADEPILOT_DB", ""))

    # 默认目标站点（US/UK/DE/JP，影响文案字符上限与合规口径）
    default_marketplace: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_MARKETPLACE", "US"))

    def db_file(self) -> str:
        if not self.db_path:
            self.db_path = os.path.join(self.data_dir, "provenance.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        return self.db_path

    def missions_dir(self) -> str:
        path = os.path.join(self.data_dir, "missions")
        os.makedirs(path, exist_ok=True)
        return path


CONFIG = Config()
