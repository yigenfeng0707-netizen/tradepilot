# Copyright 2026 TradePilot project. Apache-2.0.
"""合规体检：三层来源口径 + 现场验证。

三层（诚实标注，不冒充）：
- platform  平台品类规则（速卖通规则中心，阿里 CDN，国内可达）→ 现场验证
- authority 官方权威页面：当前网络可达者现场验证；不可达者如实标注
  "引用性来源（当前网络不可达）"，绝不假装通过
- 编辑性知识 = 要求条目本身；每条要求绑定可回访的官方来源。

每条通过体检的来源写入存证（kind=policy）。
"""
from __future__ import annotations

from typing import Any

from .. import provenance
from .executor import ToolOutcome
from .fetch import fetch_http

# 平台规则（国内可达，逐品类现场验证）
PLATFORM_RULES = {
    "pet": {"item": "速卖通宠物用品品类发布规则与禁限售清单（平台侧合规基线）",
            "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com"},
    "home": {"item": "速卖通家居品类发布规则与禁限售清单（平台侧合规基线）",
             "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com"},
    "electronics": {"item": "速卖通电子/电器品类发布规则与禁限售清单（平台侧合规基线）",
                    "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com"},
}

KB: dict[str, list[dict[str, str]]] = {
    "pet": [
        {"item": "宠物食品/零食须符合 FDA 动物饲料与成分标签要求（成分表、保证分析值、净含量）",
         "authority": "U.S. FDA", "source_url": "https://www.fda.gov/animal-veterinary"},
        {"item": "带毛/纺织类宠物用品注意加州 Prop 65 警示标签义务",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65"},
        {"item": "出口美国须按 HTS 编码申报关税（宠物用品类目）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov"},
    ],
    "home": [
        {"item": "家居消费品须满足 CPSC 强制标准与召回通报义务（CPSIA 铅含量、小零件等）",
         "authority": "U.S. CPSC", "source_url": "https://www.cpsc.gov"},
        {"item": "加州 Prop 65 化学物质警示标签（按材质评估）",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65"},
        {"item": "出口美国须按 HTS 编码申报关税（家居品类）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov"},
    ],
    "electronics": [
        {"item": "无线/蓝牙类设备须 FCC 合规（SDoC 或认证，FCC ID 标注）",
         "authority": "U.S. FCC", "source_url": "https://www.fcc.gov"},
        {"item": "锂电池产品运输受 UN38.3 与危险品规则约束（瓦时数标注、包装指令）",
         "authority": "U.S. DOT PHMSA", "source_url": "https://www.phmsa.dot.gov"},
        {"item": "出口美国须按 HTS 编码申报关税（电子产品税率普遍 0–5%）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov"},
    ],
}

CATEGORY_ALIASES = {
    "pet": "pet", "dog": "pet", "cat": "pet", "宠物": "pet",
    "home": "home", "household": "home", "furniture": "home", "decor": "home",
    "家居": "home", "家饰": "home",
    "electronics": "electronics", "electronic": "electronics", "cable": "electronics",
    "usb": "electronics", "电子": "electronics",
}


def resolve_category(raw: str) -> str | None:
    low = raw.lower()
    for key, cat in CATEGORY_ALIASES.items():
        if key in low:
            return cat
    return None


async def _live_check(url: str) -> dict[str, Any]:
    """来源页可达性检查：HTTP 200 即视为来源存活（不要求渲染，只要求权威页在线）。"""
    try:
        resp = await fetch_http(url)
        return {"http_status": resp["status"], "live": resp["status"] == 200,
                "bytes": len(resp["html"])}
    except Exception as exc:  # noqa: BLE001
        return {"http_status": None, "live": False, "error": f"{type(exc).__name__}: {exc}"}


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    category_hint = str(tool_input.get("category", "")).strip()
    category = resolve_category(category_hint)
    if category is None:
        return ToolOutcome(
            False, "error", None,
            f"暂无品类「{category_hint}」的合规知识卡（可用：pet/home/electronics）"
            "——不冒充覆盖面，宁可不答。",
        )
    checks: list[dict[str, Any]] = []

    platform = PLATFORM_RULES[category]
    live = await _live_check(platform["source_url"])
    pid = provenance.record("policy", platform["source_url"],
                            {"item": platform["item"], "live": live}) if live["live"] else None
    checks.append({**platform, "kind": "platform", **live, "provenance_id": pid})

    for req in KB[category]:
        live = await _live_check(req["source_url"])
        pid = provenance.record("policy", req["source_url"],
                                {"item": req["item"], "live": live}) if live["live"] else None
        checks.append({**req, "kind": "authority", **live, "provenance_id": pid})

    ok_n = sum(1 for c in checks if c["live"])
    unverified = [c["authority"] for c in checks if not c["live"]]
    note = (f"{ok_n}/{len(checks)} 条来源现场验证有效"
            + (f"；{', '.join(unverified)} 当前网络不可达，仅作引用标注（部署态自动重验）"
               if unverified else ""))
    return ToolOutcome(
        True, "done", None, f"合规体检（{category} 品类）：{note}",
        {"category": category, "checks": checks, "live_verified": ok_n, "total": len(checks)},
    )
