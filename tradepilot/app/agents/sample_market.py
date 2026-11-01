# Copyright 2026 TradePilot project. Apache-2.0.
"""市场样本数据（从海丝出海项目迁入，**仅作真实抓取失败时的兜底**）。

红线：任何从这里读出的内容，响应里必须带 data_quality="sample"，
并在 note 里写明"样本数据，非实时市场抓取"。禁止在真实数据可用时混入。
数据来源：海丝项目 app/data/market_data.py（2024 年行业公开报告口径），
保留结构与数值，未做任何"美化"。
"""
from __future__ import annotations

SAMPLE_NOTE = "样本数据（行业公开报告口径，非实时抓取）；真实数据需 market_scan 成功"

SAMPLE_MARKET_DATA: dict[str, dict] = {
    "宠物用品": {
        "name": "宠物用品",
        "market_size": "2024年东南亚宠物用品市场规模约50亿美元，年增长率15%",
        "market_growth": "15%",
        "hot_categories": [
            {"name": "宠物食品", "share": "40%", "growth": "+12%"},
            {"name": "宠物玩具", "share": "20%", "growth": "+18%"},
            {"name": "宠物清洁用品", "share": "15%", "growth": "+10%"},
            {"name": "宠物智能设备", "share": "15%", "growth": "+45%"},
            {"name": "宠物服饰", "share": "10%", "growth": "+8%"},
        ],
        "trends": [
            {"description": "智能宠物用品增速最快（+45%），自动喂食器、智能猫砂盆热销", "impact": "high"},
            {"description": "环保材质宠物玩具需求上升，消费者关注可持续性", "impact": "medium"},
        ],
        "price_tiers": [
            {"tier": "大众价位", "price_range": "$5-15", "volume_share": "60%"},
            {"tier": "中端价位", "price_range": "$15-30", "volume_share": "30%"},
            {"tier": "高端价位", "price_range": "$30+", "volume_share": "10%"},
        ],
        "risks": [
            {"description": "食品类需要 FDA 认证和当地食品安全认证", "level": "high",
             "mitigation": "优先选择非食品品类，或提前办理认证"},
        ],
    },
    "家居用品": {
        "name": "家居用品",
        "market_size": "2024年东南亚家居用品市场规模约120亿美元，年增长率12%",
        "market_growth": "12%",
        "hot_categories": [
            {"name": "智能家居", "share": "35%", "growth": "+35%"},
            {"name": "厨房用品", "share": "25%", "growth": "+10%"},
            {"name": "收纳整理", "share": "20%", "growth": "+15%"},
        ],
        "trends": [
            {"description": "智能家居增速最快（+35%），智能照明和安防需求旺盛", "impact": "high"},
            {"description": "小户型收纳产品热销，东南亚城市化加速", "impact": "high"},
        ],
        "price_tiers": [
            {"tier": "大众价位", "price_range": "$10-30", "volume_share": "55%"},
            {"tier": "中端价位", "price_range": "$30-80", "volume_share": "30%"},
            {"tier": "高端价位", "price_range": "$80+", "volume_share": "15%"},
        ],
        "risks": [
            {"description": "部分国家要求电器类产品本地认证（如印尼 SDPPI）", "level": "medium",
             "mitigation": "提前查询目的国认证清单"},
        ],
    },
    "3C数码": {
        "name": "3C数码",
        "market_size": "2024年东南亚3C数码市场规模约300亿美元，年增长率10%",
        "market_growth": "10%",
        "hot_categories": [
            {"name": "充电配件", "share": "30%", "growth": "+20%"},
            {"name": "音频设备", "share": "25%", "growth": "+15%"},
            {"name": "手机壳膜", "share": "25%", "growth": "+8%"},
        ],
        "trends": [
            {"description": "快充与多口充电器需求随手机换代提升", "impact": "high"},
        ],
        "price_tiers": [
            {"tier": "大众价位", "price_range": "$3-10", "volume_share": "50%"},
            {"tier": "中端价位", "price_range": "$10-30", "volume_share": "35%"},
            {"tier": "高端价位", "price_range": "$30+", "volume_share": "15%"},
        ],
        "risks": [
            {"description": "锂电池运输受 UN38.3 与危险品规则约束", "level": "high",
             "mitigation": "使用合规包装并随附检测报告"},
        ],
    },
}

# 品类 → 检索词（真实抓取用；keyword 显式传入时优先）
CATEGORY_KEYWORDS: dict[str, tuple[str, str]] = {
    "宠物用品": ("dog bed", "狗床"),
    "家居用品": ("storage box", "收纳箱"),
    "3C数码": ("usb c cable", "数据线"),
    "3C电子": ("usb c cable", "数据线"),
    "服装": ("t-shirt", "T恤"),
    "服装服饰": ("t-shirt", "T恤"),
}

# 中文品类 → 工具层品类键。
# 工具层的 CATEGORY_RULES 与 BUILTIN_HYPOTHESES 都用英文键（pet/home/electronics），
# 传中文会让**两件事同时失效**：① 内置候选词取不到（get 返回空）；
# ② relevant() 找不到规则 → **整层噪声过滤被跳过**，无关商品全部放行。
# 第二条不报错、只是结果变脏，所以必须靠这一张表兜住，不能靠调用方自觉。
# 别名要跟前端 useStore.categories 的写法对齐（"3C电子"/"服装服饰"）。
CATEGORY_TOOL_KEY: dict[str, str] = {
    "宠物用品": "pet", "宠物": "pet",
    "家居用品": "home", "家居": "home",
    "3C数码": "electronics", "3c数码": "electronics",
    "3C电子": "electronics", "3c电子": "electronics",
    "数码": "electronics", "3c": "electronics",
}

# 工具层 CATEGORY_RULES / BUILTIN_HYPOTHESES 覆盖的品类键；其余品类必须自己给检索词。
CATEGORIES_WITH_RULES = frozenset({"pet", "home", "electronics"})


def tool_category(category: str) -> str:
    """中文品类 → 工具层英文键；未登记的原样返回（工具层会按"未知品类放行"处理）。"""
    cat = (category or "").strip()
    return CATEGORY_TOOL_KEY.get(cat, CATEGORY_TOOL_KEY.get(cat.lower(), cat))


# 市场 → 抓取源优先级（SEA 官方 API 在 M3 接入，当前返回空表）
MARKET_SOURCES: dict[str, tuple[str, ...]] = {
    "US": ("dangdang", "newegg"),
    "SEA": (),
}
