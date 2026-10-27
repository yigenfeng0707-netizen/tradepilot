# Copyright 2026 TradePilot project. Apache-2.0.
"""合规体检：官方来源知识库 + 现场验证。

每条要求绑定一个**官方权威页面**（FDA/CPSC/CBP/FCC/平台政策）；执行体检时对
来源页做一次真实 GET，HTTP 200 才计"来源有效"并写入存证——知识卡不是凭空
记忆，是可回访的引用。品类的规则映射是编辑性知识，与"抓取的事实"分列，绝不
冒充抓取结果。
"""
from __future__ import annotations

from typing import Any

from .. import provenance
from .executor import ToolOutcome
from .fetch import fetch_http, _looks_blocked

# 品类 → 合规要求（编辑性知识；每条 source_url 为官方权威页面）
KB: dict[str, list[dict[str, str]]] = {
    "pet": [
        {"item": "宠物食品/零食须符合 FDA 动物饲料与成分标签要求",
         "authority": "U.S. FDA", "source_url": "https://www.fda.gov/animal-veterinary",
         "note": "成分表、保证分析值、净含量、制造商信息"},
        {"item": "带毛/纺织类宠物用品注意加州 Prop 65 警示标签",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65",
         "note": "含邻苯二甲酸酯/染料类物质的警示义务"},
        {"item": "含电池的智能宠物设备（如追踪器）运输受锂电池规则约束",
         "authority": "U.S. DOT PHMSA", "source_url": "https://www.phmsa.dot.gov",
         "note": "UN38.3 与包装标注"},
    ],
    "home": [
        {"item": "家居消费品须满足 CPSC 相关强制标准与召回通报义务",
         "authority": "U.S. CPSC", "source_url": "https://www.cpsc.gov",
         "note": "如家具稳定性、小零件、铅含量（CPSIA）"},
        {"item": "加州 Prop 65 化学物质警示标签",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65",
         "note": "按材质评估"},
        {"item": "进口关税归类须按 HTS 编码申报",
         "authority": "U.S. CBP", "source_url": "https://hts.usitc.gov",
         "note": "申报品类与材质决定税率"},
    ],
    "electronics": [
        {"item": "无线/蓝牙类设备须 FCC 合规（SDoC 或认证）",
         "authority": "U.S. FCC", "source_url": "https://www.fcc.gov",
         "note": "FCC ID 标注与测试报告"},
        {"item": "锂电池产品运输受 UN38.3 与航空/海运规则约束",
         "authority": "U.S. DOT PHMSA", "source_url": "https://www.phmsa.dot.gov",
         "note": "瓦时数标注、包装指令"},
        {"item": "进口关税归类须按 HTS 编码申报",
         "authority": "U.S. CBP", "source_url": "https://hts.usitc.gov",
         "note": "电子产品税率普遍 0–5%，以 HTS 归类为准"},
    ],
}

CATEGORY_ALIASES = {
    "pet": "pet", "dog": "pet", "cat": "pet", "宠物": "pet",
    "home": "home", "household": "home", "furniture": "home", "家居": "home",
    "electronics": "electronics", "electronic": "electronics", "电子": "electronics",
}


def resolve_category(raw: str) -> str | None:
    low = raw.lower()
    for key, cat in CATEGORY_ALIASES.items():
        if key in low:
            return cat
    return None


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    category_hint = str(tool_input.get("category", "")).strip()
    category = resolve_category(category_hint)
    if category is None:
        return ToolOutcome(
            False, "error", None,
            f"暂无品类「{category_hint}」的合规知识卡（可用：pet/home/electronics）"
            "——不冒充覆盖面，宁可不答。",
        )
    requirements = KB[category]

    checks = []
    for req in requirements:
        try:
            resp = await fetch_http(req["source_url"])
            live = resp["status"] == 200 and not _looks_blocked(resp["html"])
        except Exception:  # noqa: BLE001
            live = False
        pid = provenance.record(
            "policy", req["source_url"],
            {"item": req["item"], "authority": req["authority"],
             "live_verified": live},
        ) if live else None
        checks.append({**req, "live_verified": live, "provenance_id": pid})

    ok_n = sum(1 for c in checks if c["live_verified"])
    return ToolOutcome(
        True, "done", None,
        f"合规体检（{category} 品类）：{len(checks)} 条要求，{ok_n} 条官方来源现场验证有效"
        f"（其余站点瞬时不可达，已如实标注）",
        {"category": category, "checks": checks, "live_verified": ok_n, "total": len(checks)},
    )
