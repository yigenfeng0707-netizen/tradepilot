# Copyright 2026 TradePilot project. Apache-2.0.
"""listing_audit：上架文案合规审计（纯规则、零外部依赖、可现场演示）。

为什么要有这一层：listing_draft 只负责"把真实事实组织成文案"，它不知道平台的
字符上限、禁用的宣称词、关键词堆砌规则。亚马逊下架/压制 listing 的头号原因不是
数据错，而是文案违规——所以生成之后必须有一道确定性审计，且结论可复算。

审计五类问题（对应亚马逊/各国监管的真实红线）：
1. 字段上限：标题/五点/描述/Search Terms 的字符与条数上限（超限会被截断或拒收）；
2. 高风险宣称：绝对化排名、平台标识滥用、医疗疗效、官方背书暗示、保证承诺、
   促销信息、无认证环保宣称（FTC Green Guides）、过敏安全宣称；
3. 结构与可读：全大写、符号/emoji 滥用、重复词堆砌、五点条数与长度；
4. A9/A10 基础：核心关键词是否落在标题前 80 字符、Search Terms 是否缺失；
5. 本地化：目标站语言残留（英文站出现中文）、度量单位未本地化。

输出 0-100 分 + 逐条 issue（含修复建议）+ 可勾选清单，前端可直接渲染。
"""
from __future__ import annotations

import re
from typing import Any

from .executor import ToolOutcome

# 各站点字段上限（字符；Search Terms 为字节）。数值取平台公开文档的保守值，
# 类目另有更严限制时由 category_limits 覆盖（如部分类目标题 80 字符）。
LIMITS: dict[str, dict[str, int]] = {
    "US": {"title": 200, "bullets": 5, "bullet": 500, "desc": 2000, "search_terms_bytes": 250},
    "UK": {"title": 200, "bullets": 5, "bullet": 500, "desc": 2000, "search_terms_bytes": 250},
    "DE": {"title": 200, "bullets": 5, "bullet": 500, "desc": 2000, "search_terms_bytes": 250},
    "JP": {"title": 100, "bullets": 5, "bullet": 500, "desc": 2000, "search_terms_bytes": 250},
}

# 部分类目标题上限更严（如宠物/家居常见 80），命中即用更小值
CATEGORY_TITLE_LIMITS = {"pet": 200, "home": 200, "electronics": 200}

# 高风险宣称词：命中即 Critical（下架/审核/律师函级）
HIGH_RISK = {
    "绝对化排名宣称": [
        r"\bbest[\s-]?sell(er|ing)?\b", r"#\s?1\b", r"\bno\.?\s?1\b", r"\bnumber\s+one\b",
        r"\btop[\s-]?rated\b", r"\btop[\s-]?seller\b", r"\bbest\s+in\s+class\b",
        r"\bworld'?s\s+best\b", r"\bamazon'?s\s+choice\b", r"\bbest\s+quality\b",
    ],
    "平台标识滥用": [
        r"\bprime\b", r"\bfulfilled\s+by\s+amazon\b", r"\bfba\b", r"\bsponsored\b",
        r"\bamazon\s+(brand|basics)\b",
    ],
    "医疗疗效宣称": [
        r"\bcures?\b", r"\btreats?\b", r"\bheals?\b", r"\bprevents?\b", r"\bdiagnos\w*\b",
        r"\bmedical[\s-]grade\b", r"\bclinical(ly)?\b", r"\btherapeutic\b",
        r"\bantibiotic\b", r"\banti[\s-]?bacterial\b", r"\banti[\s-]?microbial\b",
        r"\bkills?\s+99", r"\bdisinfect\w*\b",
    ],
    "官方背书暗示": [
        r"\bfda\s+(approved|registered|certified)\b", r"\bepa\s+(approved|registered)\b",
        r"\busda\s+(organic|approved)\b", r"\bce\s+(certified|marked)\b",
        r"\bapproved\s+by\s+the\s+(fda|epa|usda)\b",
    ],
    "保证承诺": [
        r"\bguarantee[ds]?\b", r"\b100%\s+satisfaction\b", r"\bmoney[\s-]back\b",
        r"\blifetime\s+warranty\b", r"\brisk[\s-]free\b", r"\bwarranty\b",
    ],
    "促销与价格信息": [
        r"\bfree\s+shipping\b", r"\bon\s+sale\b", r"\bdiscount(ed)?\b", r"\bcheap(est)?\b",
        r"\bbest\s+price\b", r"\b\d+\s?%\s?off\b", r"\bdeal\b", r"\bcoupon\b",
    ],
    "无认证环保宣称": [
        r"\beco[\s-]?friendly\b", r"\benvironmentally\s+friendly\b", r"\bbiodegradable\b",
        r"\bcompostable\b", r"\bnon[\s-]?toxic\b", r"\b100%\s+organic\b",
        r"\ball[\s-]?natural\b", r"\bcarbon[\s-]?neutral\b",
    ],
    "过敏与安全宣称": [
        r"\bhypoallergenic\b", r"\bfood[\s-]?grade\b", r"\bnon[\s-]?irritating\b",
        r"\bsafe\s+for\s+(babies|children|pets)\b",
    ],
}

# 促销/水词：Warning（不致命但降低转化与审核通过率）
FLUFF = [r"\bhigh\s+quality\b", r"\bperfect\s+for\b", r"\bmust[\s-]?have\b",
         r"\bamazing\b", r"\bpremium\s+quality\b", r"\bbrand\s+new\b"]

CJK = re.compile(r"[\u4e00-\u9fff]")
WORD = re.compile(r"[a-z][a-z'\-]{3,}")
CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")
SYMBOLS = re.compile(r"[!?$★☆♥♦]|[\U0001F300-\U0001FAFF\u2600-\u27BF]")

LEVEL_WEIGHT = {"critical": 18, "warning": 6, "info": 1.5}


def _scan_fields(copy: dict[str, Any]) -> dict[str, str]:
    """把文案拆成待审计字段（标题/每条卖点/描述）。"""
    fields = {"title": str(copy.get("title") or "")}
    bullets = copy.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    for i, b in enumerate(bullets, 1):
        fields[f"bullet_{i}"] = str(b)
    fields["description"] = str(copy.get("description") or "")
    return fields


def _issue(level: str, rule: str, field: str, evidence: str, fix: str) -> dict[str, str]:
    return {"level": level, "rule": rule, "field": field,
            "evidence": evidence[:160], "fix": fix}


def audit(copy: dict[str, Any], keyword: str = "", marketplace: str = "US",
          category: str = "", brand: str = "", brand_blocklist: list[str] | None = None,
          search_terms: str = "", generated_by: str = "") -> dict[str, Any]:
    """核心审计函数（纯函数，便于单测与复算）。"""
    mp = (marketplace or "US").upper()
    limits = dict(LIMITS.get(mp, LIMITS["US"]))
    if category and category.lower() in CATEGORY_TITLE_LIMITS:
        limits["title"] = min(limits["title"], CATEGORY_TITLE_LIMITS[category.lower()])

    fields = _scan_fields(copy)
    title = fields["title"]
    bullets = [v for k, v in fields.items() if k.startswith("bullet_")]
    desc = fields["description"]
    blob = " ".join(v for v in fields.values() if v)
    low = blob.lower()
    issues: list[dict[str, str]] = []

    # ---- 1. 字段上限 ----
    if len(title) > limits["title"]:
        issues.append(_issue("critical", "标题超限", "title",
                             f"{len(title)} 字符 > 上限 {limits['title']}",
                             f"压缩到 {limits['title']} 字符内，优先删形容词、保留核心词与关键属性"))
    elif len(title) < limits["title"] * 0.55:
        issues.append(_issue("warning", "标题字符浪费", "title",
                             f"{len(title)}/{limits['title']} 字符",
                             "标题长度影响搜索覆盖，建议补属性词（材质/尺寸/适用场景）"))
    if len(bullets) != limits["bullets"]:
        issues.append(_issue("warning", "五点数量不符", "bullets",
                             f"当前 {len(bullets)} 条，平台标准 {limits['bullets']} 条",
                             f"补齐到 {limits['bullets']} 条，每条聚焦一个购买理由"))
    for i, b in enumerate(bullets, 1):
        if len(b) > limits["bullet"]:
            issues.append(_issue("critical", "卖点超限", f"bullet_{i}",
                                 f"{len(b)} 字符 > 上限 {limits['bullet']}",
                                 "拆分或精简，超限部分会被截断"))
        elif len(b) < 80:
            issues.append(_issue("warning", "卖点过短", f"bullet_{i}",
                                 f"{len(b)} 字符",
                                 "每条卖点建议 150-300 字符，写清利益点+支撑事实"))
    if len(desc) > limits["desc"]:
        issues.append(_issue("critical", "描述超限", "description",
                             f"{len(desc)} 字符 > 上限 {limits['desc']}", "精简描述"))

    # ---- 2. 高风险宣称 ----
    for rule, patterns in HIGH_RISK.items():
        for pat in patterns:
            m = re.search(pat, low)
            if m:
                issues.append(_issue("critical", rule, "全文", m.group(0),
                                     _claim_fix(rule, m.group(0))))
                break
    for pat in FLUFF:
        m = re.search(pat, low)
        if m:
            issues.append(_issue("warning", "空泛水词", "全文", m.group(0),
                                 "换成可验证的具体事实（材质/工艺/尺寸/测试数据）"))
            break

    # 竞品品牌词（侵权风险，由调用方传入黑名单）
    for name in (brand_blocklist or []):
        if name and name.lower() in low:
            issues.append(_issue("critical", "疑似侵权品牌词", "全文", name,
                                 "删除竞品品牌名，改用品类通用词；标题与 Search Terms 都要清"))

    # ---- 3. 结构与可读 ----
    caps = CAPS_WORD.findall(title)
    words_in_title = title.split()
    if words_in_title and len(caps) / max(1, len(words_in_title)) > 0.2:
        issues.append(_issue("warning", "全大写滥用", "title",
                             f"{len(caps)} 个全大写词 / {len(words_in_title)} 词",
                             "标题保持正常大小写，全大写会被判定为促销噪音"))
    if SYMBOLS.search(blob):
        issues.append(_issue("warning", "特殊符号/emoji", "全文",
                             SYMBOLS.search(blob).group(0),
                             "移除 ! ? $ ★ 与 emoji，亚马逊会拒收或降权"))
    counts: dict[str, int] = {}
    for w in WORD.findall(low):
        counts[w] = counts.get(w, 0) + 1
    stuffed = {w: c for w, c in counts.items() if c >= 4}
    if stuffed:
        top = sorted(stuffed.items(), key=lambda kv: -kv[1])[:3]
        issues.append(_issue("warning", "关键词堆砌", "全文",
                             "、".join(f"{w}×{c}" for w, c in top),
                             "同一实词全字段出现 3 次以内；用同义词与长尾词覆盖，别硬堆"))

    # ---- 4. A9/A10 基础 ----
    kw = (keyword or "").strip().lower()
    if kw:
        head = title[:80].lower()
        if kw not in head:
            issues.append(_issue("warning", "核心词未进标题前 80 字符", "title",
                                 f"「{keyword}」不在标题前段",
                                 "标题前 80 字符权重最高，把核心词提到最前面（品牌后）"))
        if counts.get(kw.split()[0], 0) == 0 and kw not in low:
            issues.append(_issue("warning", "核心词缺失", "全文", keyword,
                                 "确保核心词在标题与至少一条卖点中出现"))
    if not search_terms:
        issues.append(_issue("info", "缺后台 Search Terms", "search_terms",
                             "未提供",
                             "补 250 字节内的 Search Terms：同义词/拼写变体/使用场景，"
                             "不得与标题重复，不堆品牌词"))
    else:
        st_bytes = len(search_terms.encode("utf-8"))
        if st_bytes > limits["search_terms_bytes"]:
            issues.append(_issue("critical", "Search Terms 超字节", "search_terms",
                                 f"{st_bytes} > {limits['search_terms_bytes']} bytes",
                                 "删低频词，保留同义词与拼写变体"))

    # ---- 5. 本地化 ----
    if mp in ("US", "UK") and CJK.search(blob):
        issues.append(_issue("critical", "目标站语言残留", "全文",
                             CJK.search(blob).group(0),
                             "英文站不得出现中文，会被判定为低质量 listing"))
    if mp == "US" and re.search(r"\b\d+\s?(cm|mm|kg|ml)\b", low):
        issues.append(_issue("info", "度量单位未本地化", "全文",
                             re.search(r"\b\d+\s?(cm|mm|kg|ml)\b", low).group(0),
                             "美国站主用 inch/lb/fl oz，公制可作括号补充"))
    if generated_by and generated_by.startswith("llm"):
        issues.append(_issue("info", "AI 生成内容披露", "meta", generated_by,
                             "导出物保留 generated_by 字段，说明文案由 AI 组织、事实来自存证"))

    # ---- 6. 未填占位符（模板骨架/人工待补项）----
    placeholders = re.findall(r"\[(?:ADD|FILL|TODO)[^\]]*\]", blob, flags=re.I)
    if placeholders:
        issues.append(_issue("critical", "占位符未填", "全文",
                             f"{len(placeholders)} 处，如 {placeholders[0][:40]}",
                             "占位符是待补的规格信息，直接上架会显得未完成；"
                             "补齐材质/尺寸/承重等可验证参数后删除标记"))

    # ---- 评分 ----
    penalty = sum(LEVEL_WEIGHT.get(i["level"], 1) for i in issues)
    score = max(0, round(100 - penalty))
    criticals = [i for i in issues if i["level"] == "critical"]
    warnings = [i for i in issues if i["level"] == "warning"]
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"
    passed = score >= 80 and not criticals

    return {
        "score": score, "grade": grade, "passed": passed,
        "marketplace": mp, "limits": limits,
        "counts": {"critical": len(criticals), "warning": len(warnings),
                   "info": len(issues) - len(criticals) - len(warnings)},
        "stats": {
            "title_chars": len(title), "title_utilization": round(len(title) / limits["title"], 2),
            "bullet_count": len(bullets),
            "bullet_chars": [len(b) for b in bullets],
            "desc_chars": len(desc),
            "top_words": sorted(counts.items(), key=lambda kv: -kv[1])[:5],
        },
        "issues": issues,
        "verdict": ("可上架" if passed else
                    "不可上架：存在必改项" if criticals else "建议修改后再上架"),
        "basis": "规则为编辑性知识（平台公开政策与监管要求），不构成法律意见；"
                 "命中不等于违规，需结合类目与认证实际判定",
    }


def _claim_fix(rule: str, hit: str) -> str:
    fixes = {
        "绝对化排名宣称": "删除排名/最优类表述，改为可验证的具体属性（如材质、尺寸、测试标准）",
        "平台标识滥用": "移除 Prime/FBA/Amazon 等平台标识，属于商标与政策双重风险",
        "医疗疗效宣称": "删除疗效动词，改为功能描述（如“便于清洁”而非“杀菌治疗”）",
        "官方背书暗示": "除非持有对应证书编号，一律删除；有证书也应写在 A+ 或图片而非标题",
        "保证承诺": "删除 guarantee/warranty 类承诺，售后政策写进店铺政策页",
        "促销与价格信息": "移除价格/促销信息，亚马逊禁止在文案中做促销表达",
        "无认证环保宣称": "需 FTC Green Guides 实证支撑；无认证改为具体材质事实（如“rPET 再生料 60%”）",
        "过敏与安全宣称": "需测试报告支撑；无报告改为客观描述（如“不含香精”）",
    }
    return fixes.get(rule, f"删除或改写「{hit}」")


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    copy = tool_input.get("copy") or {}
    if not isinstance(copy, dict) or not copy.get("title"):
        return ToolOutcome(False, "error", None, "缺少待审计文案（copy.title 为空）")
    result = audit(
        copy,
        keyword=str(tool_input.get("keyword") or ""),
        marketplace=str(tool_input.get("marketplace") or "US"),
        category=str(tool_input.get("category") or ""),
        brand=str(tool_input.get("brand") or ""),
        brand_blocklist=list(tool_input.get("brand_blocklist") or []),
        # 关键词层产出的 Search Terms 会随文案包带下来，优先用它审计字节数
        search_terms=str(tool_input.get("search_terms") or copy.get("search_terms") or ""),
        generated_by=str(tool_input.get("generated_by") or ""),
    )
    c = result["counts"]
    return ToolOutcome(
        True, "done", None,
        f"文案审计 {result['score']} 分（{result['grade']} 级）："
        f"{c['critical']} 项必改 / {c['warning']} 项建议 / {c['info']} 项提示 → {result['verdict']}",
        result,
    )
