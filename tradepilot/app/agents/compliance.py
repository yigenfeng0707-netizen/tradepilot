# Copyright 2026 TradePilot project. Apache-2.0.
"""合规 Agent（M2）：美国官方源实时验证 + 东南亚静态条款库分级。

这个 Agent 最容易被写成"看起来很专业"的样子——一屏红黄绿的认证清单、
一列关税数字、一个"风险等级：中"。但那些数字如果没有出处，就是在拿
法律风险开玩笑。所以这里只做两件事，且把两件事的**可信度分开标**：

1. **美国**：走 `app/tools/compliance.py` 真实抓官方页面（FDA / CPSC / FCC /
   OEHHA / USITC），逐条尝试在页面正文里定位条款原文，结果落存证。
   定位不到就如实写 `page_only` / `blocked` / `offline`——HTTP 200 只证明
   网站活着，不证明条款写在上面。

2. **东南亚**：没有可公开抓取的官方条款库，也没有免费 API。因此给的是
   **静态分级参考**（强制认证 / 标签与文件 / 平台准入），逐条标
   `verification="static_reference"` + `verified=False`，并在 `gaps` 里写明
   "未经官方页面验证，须以当地持牌代理/官方门户为准"。
   不拿美国那套去套东南亚——那是最典型的假合规。

**关税不接实时税率**：HTS 与东盟各国税率没有公开免费 API，本 Agent 不输出
任何具体税率数字，只给"需按 HTS/当地税则申报"的动作项。编一个 12% 出来
比不给更危险。

风险等级 `risk_level` 的口径是**未验证的强制要求条数**，不是法律结论；
`risk_basis` 里写清这一点。
"""
from __future__ import annotations

from typing import Any

from ..tools import compliance as compliance_tool
from . import base

AGENT = "合规Agent"

# 国家/地区 → 市场键。未知国家一律拒绝，不猜。
MARKET_BY_COUNTRY: dict[str, str] = {
    "美国": "US", "us": "US", "usa": "US", "united states": "US",
    "印尼": "ID", "印度尼西亚": "ID", "indonesia": "ID",
    "泰国": "TH", "thailand": "TH",
    "越南": "VN", "vietnam": "VN",
    "马来西亚": "MY", "malaysia": "MY",
    "菲律宾": "PH", "philippines": "PH",
    "新加坡": "SG", "singapore": "SG",
}

MARKET_LABEL = {"US": "美国", "ID": "印尼", "TH": "泰国", "VN": "越南",
                "MY": "马来西亚", "PH": "菲律宾", "SG": "新加坡"}

# 品类别名 → 工具层英文键（与 sample_market.CATEGORY_TOOL_KEY 同一套口径）
_CATEGORY_ALIASES: dict[str, str] = {
    "宠物用品": "pet", "宠物": "pet", "pet": "pet",
    "家居用品": "home", "家居": "home", "home": "home",
    "3c电子": "electronics", "3C电子": "electronics", "3c数码": "electronics",
    "3C数码": "electronics", "数码": "electronics", "electronics": "electronics",
}

# 东南亚静态条款库。
# level 是给卖家的动作分级：certification=不办就清不了关；labeling=标签/文件缺失
# 会被扣货或罚款；platform=平台侧准入，影响能否上架。
# 这些是编辑性知识，逐条带 authority 便于卖家自己回访核实，绝不标 verified。
_SEA_STATIC: dict[str, dict[str, list[dict[str, str]]]] = {
    "ID": {
        "pet": [
            {"item": "宠物食品/零食须取得 BPOM 注册号后方可销售",
             "authority": "BPOM", "level": "certification"},
            {"item": "进口宠物用品须由具备 API-U 进口资质的当地主体申报",
             "authority": "Kemendag", "level": "labeling"},
            {"item": "标签须使用印尼语，注明进口商与净含量",
             "authority": "Kemendag", "level": "labeling"},
        ],
        "home": [
            {"item": "部分家居用品须符合 SNI 强制标准并加贴 SNI 标志",
             "authority": "BSN", "level": "certification"},
            {"item": "标签须使用印尼语，注明进口商与材质",
             "authority": "Kemendag", "level": "labeling"},
        ],
        "electronics": [
            {"item": "无线/通信类设备须取得 SDPPI 型号核准",
             "authority": "Kominfo SDPPI", "level": "certification"},
            {"item": "部分电器须符合 SNI 强制标准并加贴 SNI 标志",
             "authority": "BSN", "level": "certification"},
            {"item": "产品说明书与安全警示须使用印尼语",
             "authority": "Kemendag", "level": "labeling"},
        ],
    },
    "TH": {
        "pet": [
            {"item": "宠物食品须向 DLD 申请进口许可并完成注册",
             "authority": "Department of Livestock Development", "level": "certification"},
            {"item": "标签须使用泰语，注明进口商与成分",
             "authority": "Thai FDA / DLD", "level": "labeling"},
        ],
        "home": [
            {"item": "部分家居用品受 TISI 强制标准约束，须加贴 TIS 标志",
             "authority": "TISI", "level": "certification"},
            {"item": "标签须使用泰语，注明进口商信息",
             "authority": "OCPB", "level": "labeling"},
        ],
        "electronics": [
            {"item": "电器与无线设备须符合 TISI 标准并取得 NBTC 型号核准",
             "authority": "TISI / NBTC", "level": "certification"},
            {"item": "安全警示与说明书须使用泰语",
             "authority": "TISI", "level": "labeling"},
        ],
    },
    "VN": {
        "pet": [
            {"item": "宠物食品须完成 MARD 产品登记并取得进口许可",
             "authority": "MARD", "level": "certification"},
            {"item": "标签须使用越南语，注明进口商与生产日期",
             "authority": "MARD", "level": "labeling"},
        ],
        "home": [
            {"item": "部分家居用品须符合越南 QCVN 技术法规并加贴 CR 标志",
             "authority": "STAMEQ", "level": "certification"},
            {"item": "标签须使用越南语（Decree 43/2017）",
             "authority": "MOIT", "level": "labeling"},
        ],
        "electronics": [
            {"item": "电器与无线设备须符合 QCVN 法规并取得 MIC 型号核准",
             "authority": "STAMEQ / MIC", "level": "certification"},
            {"item": "能效与安全标签须使用越南语",
             "authority": "MOIT", "level": "labeling"},
        ],
    },
    "MY": {
        "pet": [
            {"item": "宠物食品须向 DVS 申请进口许可",
             "authority": "Department of Veterinary Services", "level": "certification"},
            {"item": "标签须使用马来语或英语，注明进口商",
             "authority": "KPDN", "level": "labeling"},
        ],
        "home": [
            {"item": "部分家居用品受 SIRIM 认证与标准约束",
             "authority": "SIRIM / Standards Malaysia", "level": "certification"},
            {"item": "标签须使用马来语或英语",
             "authority": "KPDN", "level": "labeling"},
        ],
        "electronics": [
            {"item": "无线/通信设备须取得 SIRIM MCMC 认证与标签",
             "authority": "SIRIM / MCMC", "level": "certification"},
            {"item": "电器须符合 MS 标准，部分品类强制认证",
             "authority": "SIRIM", "level": "certification"},
        ],
    },
    "PH": {
        "pet": [
            {"item": "宠物食品须在 BAI 完成登记并取得进口许可",
             "authority": "Bureau of Animal Industry", "level": "certification"},
            {"item": "标签须注明进口商与成分",
             "authority": "BAI", "level": "labeling"},
        ],
        "home": [
            {"item": "部分家居用品受 DTI-BPS 强制标准约束（PS/ICC 标志）",
             "authority": "DTI-BPS", "level": "certification"},
            {"item": "标签须注明进口商与净含量",
             "authority": "DTI", "level": "labeling"},
        ],
        "electronics": [
            {"item": "电器与无线设备须取得 NTC 型号核准与 PS/ICC 标志",
             "authority": "NTC / DTI-BPS", "level": "certification"},
        ],
    },
    "SG": {
        "pet": [
            {"item": "宠物食品须向 SFA 注册并取得进口许可",
             "authority": "Singapore Food Agency", "level": "certification"},
            {"item": "标签须使用英语，注明进口商",
             "authority": "SFA", "level": "labeling"},
        ],
        "home": [
            {"item": "受控消费品须加贴 SAFETY Mark",
             "authority": "Enterprise Singapore", "level": "certification"},
        ],
        "electronics": [
            {"item": "通信设备须符合 IMDA 技术规范并完成设备注册",
             "authority": "IMDA", "level": "certification"},
            {"item": "受控电器须加贴 SAFETY Mark",
             "authority": "Enterprise Singapore", "level": "certification"},
        ],
    },
}

_LEVEL_LABEL = {"certification": "强制认证/要求", "labeling": "标签与文件",
                "platform": "平台准入"}


def _level_of(kind: str) -> str:
    """美国官方源按 kind 归类：平台规则 → 平台准入；监管机构条款 → 强制要求。

    工具层不区分"认证"与"申报"，这里不硬造子类，只按发布主体归类，
    避免把 HTS 申报说成"认证"。
    """
    return "platform" if kind == "platform" else "certification"


def _normalize_us_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in checks:
        lv = _level_of(str(c.get("kind") or "authority"))
        out.append({**c, "level_key": lv, "level": _LEVEL_LABEL[lv]})
    return out

_GAP_COMMON = [
    "关税税率：HTS 与东盟各国税则无公开免费 API，本报告不输出具体税率，"
    "须由报关行按实际 HTS/HS 编码核定",
    "条款为编辑性知识整理，不构成法律意见；上架前须以官方门户或当地持牌代理确认为准",
]


def market_of(target_country: str) -> str | None:
    """国家名 → 市场键；认不出来返回 None（不猜）。"""
    key = (target_country or "").strip()
    return MARKET_BY_COUNTRY.get(key) or MARKET_BY_COUNTRY.get(key.lower())


def category_key(category: str) -> str | None:
    cat = (category or "").strip()
    return _CATEGORY_ALIASES.get(cat) or _CATEGORY_ALIASES.get(cat.lower())


def _risk(unverified_mandatory: int, total_mandatory: int) -> tuple[str, str]:
    """风险等级 = 未验证的强制要求条数分级，不是法律结论。"""
    if total_mandatory == 0:
        return "未知", "该品类在本市场没有收录到强制认证要求，未做评估"
    if unverified_mandatory == 0:
        level = "低"
    elif unverified_mandatory <= 2:
        level = "中"
    else:
        level = "高"
    return level, (f"按未在官方页面验证的强制要求条数分级："
                   f"{total_mandatory} 条强制要求中 {unverified_mandatory} 条未验证。"
                   "这是资料完整度指标，不是该商品的法律合规结论")


def _static_checks(market: str, cat: str) -> list[dict[str, Any]]:
    rows = _SEA_STATIC.get(market, {}).get(cat, [])
    return [{
        "item": r["item"], "authority": r["authority"],
        "kind": "authority", "level": _LEVEL_LABEL[r["level"]],
        "level_key": r["level"],
        "verification": "static_reference", "verified": False,
        "source_url": None, "excerpt": None, "fetched_at": None,
        "note": "静态条款库条目，未经官方页面验证",
    } for r in rows]


async def run(category: str, target_country: str = "美国",
              limit: int = 0) -> dict[str, Any]:
    """合规体检。美国走官方源实时验证；东南亚给静态分级参考。"""
    del limit  # 保留参数位：暂不支持分页裁剪，一次返回全部条款
    category = (category or "").strip()
    if not category:
        return base.fail(AGENT, "缺少 category（品类名）")

    market = market_of(target_country)
    if market is None:
        return base.fail(
            AGENT,
            f"未支持的目标国家「{target_country}」——不猜别国法规。"
            f"可用：{'、'.join(MARKET_LABEL.values())}",
            {"supported": list(MARKET_LABEL.values())})

    cat = category_key(category)
    if cat is None:
        return base.fail(
            AGENT,
            f"暂无品类「{category}」的合规知识卡（可用：宠物用品 / 家居用品 / 3C电子）"
            "——不冒充覆盖面，宁可不答。",
            {"supported": ["宠物用品", "家居用品", "3C电子"]})

    label = MARKET_LABEL[market]
    gaps = list(_GAP_COMMON)

    if market == "US":
        outcome = await compliance_tool.run({"category": cat})
        if not outcome.ok:
            return base.fail(AGENT, outcome.summary or "合规体检失败",
                             {"category": category, "target_country": label})
        data = outcome.data or {}
        checks = _normalize_us_checks(data.get("checks") or [])
        verified_n = int(data.get("live_verified") or 0)
        total = int(data.get("total") or len(checks))
        # 一条都没在官方页面定位到原文时，不把"编辑性知识"标成 real
        quality = base.QUALITY_REAL if verified_n > 0 else base.QUALITY_SAMPLE
        if verified_n == 0:
            gaps.append("本轮没有一条要求在官方页面定位到原文（网络或站点拦截），"
                        "条款内容退回为编辑性知识，请以官方门户复核")
        note = (f"{label}：{verified_n}/{total} 条已在官方页面定位到条款原文；"
                f"数据来源为实时抓取")
        basis = data.get("basis") or ""
        provenance_ids = [c["provenance_id"] for c in checks
                          if c.get("provenance_id")]
    else:
        checks = _static_checks(market, cat)
        verified_n, total = 0, len(checks)
        quality = base.QUALITY_SAMPLE
        if not checks:
            gaps.append(f"{label}的「{category}」品类暂未收录条款，本报告不含该国要求")
        gaps.append(f"{label}无公开可抓取的官方条款库，以上为静态分级参考，"
                    "逐条须经官方门户或当地持牌代理确认")
        note = f"{label}：{total} 条静态分级参考（0 条官方页面验证）"
        basis = "东南亚条款为静态知识库整理，未经官方页面验证；不构成法律意见"
        provenance_ids = []

    mandatory = [c for c in checks if c.get("level_key") == "certification"]
    unverified_mandatory = [c for c in mandatory if not c.get("verified")]
    risk_level, risk_basis = _risk(len(unverified_mandatory), len(mandatory))

    recommendations = [
        f"优先办理：{c['item']}（{c['authority']}）"
        for c in mandatory if not c.get("verified")
    ][:4]
    if not recommendations:
        recommendations.append("强制认证项均已定位到官方条款，按官方要求备齐文件即可")

    return base.wrap(AGENT, {
        "category": category, "category_key": cat,
        "target_country": label, "market": market,
        "checks": checks,
        "verified_count": verified_n, "total": total,
        "mandatory_count": len(mandatory),
        "risk_level": risk_level, "risk_basis": risk_basis,
        "recommendations": recommendations,
        "gaps": gaps,
        "basis": basis,
        "provenance_ids": provenance_ids,
        "note": note,
    }, quality, note)
