# Copyright 2026 TradePilot project. Apache-2.0.
"""发布地址的**单一来源**（single source of truth）。

## 为什么要有这个文件

这个地址原先同时硬编码在四个脚本里：
`build_bp.py`（印进 BP）、`build_video.py`（演示视频里的字幕与 WebSocket）、
`verify_cloud.py`（云端端到端验证）、`check_release_drift.py`（漂移门禁）。
换一次部署就必然漏改一处——2026-10-31 就真的发生了：重新部署后域名变了，
BP 里仍印着旧链接，而旧链接已经返回 403（网关报"不支持通过 SDK Token 直接访问"）。
评委点开是死链，比代码有 bug 更致命。

所以地址只在这里写一次，其余脚本一律 `import release_info`。
地址变了只改一行，四个消费方同时生效。

## 分享码

发布出来的应用要带分享码，接口不带就 403。分享码等同访问凭证，
但 BP 必须印出**评委能点开的链接**，所以默认值取当前正式发布的分享链接；
需要换环境时用 `TRADEPILOT_SHARE_URL` 覆盖，不要改代码。
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

# 正式发布链接（含分享码）。覆盖方式：TRADEPILOT_SHARE_URL=https://host/?sharecode=xxx
SHARE_URL: str = os.environ.get(
    "TRADEPILOT_SHARE_URL",
    "https://tradepilot.apps.workbuddy.host/?sharecode=4FAF5YBGRD",
).strip()

_parts = urlsplit(SHARE_URL)
ORIGIN: str = f"{_parts.scheme}://{_parts.netloc}"          # 不带查询串
QUERY: str = _parts.query                                  # sharecode=xxx

SHARECODE: str = os.environ.get("TRADEPILOT_SHARECODE", "").strip() or (
    QUERY.split("sharecode=", 1)[1].split("&", 1)[0] if "sharecode=" in QUERY else ""
)

# WebSocket 任务通道（分享码同样要带上，否则握手被拒）
WS_URL: str = (f"wss://{_parts.netloc}/ws/mission"
               + (f"?{QUERY}" if QUERY else ""))

# HTTP 接口基址（带分享码，可直接拼路径）
API_BASE: str = SHARE_URL.rstrip("/")


def url(path: str) -> str:
    """拼一个接口地址，分享码自动带上：`url('/api/tools')`。"""
    return f"{ORIGIN}{path}" + (f"?{QUERY}" if QUERY else "")
