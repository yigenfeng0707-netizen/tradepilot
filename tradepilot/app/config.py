# Copyright 2026 TradePilot project. Apache-2.0.
"""运行配置：全部来自环境变量，绝不把密钥写进代码或库。

数据目录（存证库 + 任务记录）解析顺序：
1. 环境变量 TRADEPILOT_DATA_DIR（云上必设，如 /tmp/tradepilot）；
2. 系统临时目录下的 tradepilot/。

**为什么默认不再用仓库内 assets/**：`assets/` 是会被**打包上传**的目录——
发布部署会把整个项目目录传到沙箱，只排除 node_modules / .git / 构建产物。
运行态数据放这里，等于每次部署都把本机存证库和历史任务带到线上：线上任务列表
挂着本地测试任务、存证条数不从 0 开始、`assets/` 还混着两个已提交的评测输入文件。
2026-10-31 真的踩过一次。本地开发想让数据留在仓库里方便翻看，显式设
`TRADEPILOT_DATA_DIR=assets` 即可。
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


def _read_listinggood_key() -> str:
    """外部复核服务的密钥（.listinggood_key），同 .llm_key 惯例：不入库。"""
    try:
        return (REPO_ROOT / ".listinggood_key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _resolve_data_dir() -> str:
    """运行态数据目录：环境变量优先，否则系统临时目录（**绝不放进上传目录**）。"""
    env = os.environ.get("TRADEPILOT_DATA_DIR", "").strip()
    if env:
        Path(env).mkdir(parents=True, exist_ok=True)
        return env
    fallback = Path(tempfile.gettempdir()) / "tradepilot"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)


@dataclass
class Config:
    # LLM 网关（默认书生 InternAI 聚合网关，OpenAI 兼容，多模型聚合。
    # Key 来源：环境变量 LLM_API_KEY > 本地 .llm_key 文件；均不落代码库。
    # 无 key 时编排器走规则规划，作品依然全流程可跑）
    # strip 必须有：Secret Manager / .env 类注入常带尾随换行（实测带 \r\n 的 key
    # 让 httpx 抛 LocalProtocolError: Illegal header value，整个网关静默降级）
    llm_base_url: str = field(default_factory=lambda: os.environ.get(
        "LLM_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1").strip())
    llm_api_key: str = field(default_factory=lambda: (os.environ.get(
        "LLM_API_KEY", "") or _read_local_key()).strip())
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

    # 广告搜索词报告（可选）：配置后关键词层挂真实曝光/点击，未配置则降级为代理指标。
    # 报告周期必须显式给出——没有时间区间的曝光量不可解释。
    ads_report_path: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_ADS_REPORT", ""))
    ads_report_period: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_ADS_PERIOD", ""))

    # 外部合规复核（第三方 ListingGood）：默认 off，不调用任何外部接口。
    # 为什么默认关：deep 是计费接口，评测批量跑会烧额度；且外部服务不可用时
    # 作品必须照常出结果。off 下审计结论与未接入时逐字节一致。
    listinggood_api_key: str = field(default_factory=lambda: os.environ.get(
        "LISTINGGOOD_API_KEY", "") or _read_listinggood_key())
    listinggood_mode: str = field(default_factory=lambda: os.environ.get(
        "TRADEPILOT_LISTINGGOOD", "off"))

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
