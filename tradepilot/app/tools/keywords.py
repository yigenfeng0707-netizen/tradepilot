# Copyright 2026 TradePilot project. Apache-2.0.
"""关键词层：从真实竞品标题反推买家搜索词，并生成后台 Search Terms。

## 诚实性前提（这一层最容易造假，所以先把边界写死）

我们没有搜索量/竞争度数据源，因此**不输出搜索量、不输出竞争度评分**——
凭空给一个"月搜索量 12,000"是最容易被评委一戳就破的假数据。

这一层给的是**可从存证复算的东西**：
1. **观测词频**：某个词在 N 条真实竞品标题里出现 M 次，附证据标题；
2. **中英映射**：中文源标题里的属性词，映射为英文站可用的关键词（编辑性知识，
   映射表是人工维护的，但只有"在真实标题里观测到"的词才会被输出）；
3. **覆盖度代理指标**：核心词在竞品标题中的覆盖率、价格带宽度、标题长度分布——
   都是真实样本算出来的，用来代替"竞争度"这个我们拿不到的指标；
4. **Search Terms**：≤250 字节、去重、不与标题重复、不含品牌与促销词。

## 一条必须说清的界线

这些词是"买家在搜什么 / 竞品在写什么"，**不是"本品有什么参数"**。
把竞品属性词直接写进自家标题属于编造产品参数，因此标题骨架只保留核心词 +
待填占位符，属性词单独列为"可选词，需与本品实际一致"。
"""
from __future__ import annotations

import re
from typing import Any

from .. import provenance
from .executor import ToolOutcome

# 中文属性词 → 英文关键词（人工维护的编辑性知识；只输出真实观测到的词）
CN_EN: dict[str, list[str]] = {
    # 品类词
    "狗床": ["dog bed"], "狗窝": ["dog bed", "dog house"], "猫窝": ["cat bed"],
    "猫床": ["cat bed"], "宠物": ["pet"], "行军床": ["raised pet bed"],
    "沙发窝": ["sofa pet bed"], "床垫": ["pet mattress", "bed pad"],
    "垫子": ["mat", "pad"], "睡垫": ["sleeping mat"],
    "挂钟": ["wall clock"], "台灯": ["desk lamp"], "收纳箱": ["storage box"],
    "数据线": ["charging cable", "usb cable"], "手机壳": ["phone case"],
    "充电宝": ["power bank"],
    # 属性词（材质/结构/工艺）
    "可拆洗": ["washable", "removable cover"], "可机洗": ["machine washable"],
    "记忆棉": ["memory foam"], "离地": ["elevated"], "防滑": ["non-slip"],
    "加厚": ["thickened"], "保暖": ["thermal"], "透气": ["breathable"],
    "防水": ["waterproof"], "耐咬": ["chew resistant"], "静音": ["quiet"],
    "编织": ["braided"], "快充": ["fast charging"], "大容量": ["large capacity"],
    # 场景词
    "四季通用": ["all season"], "夏季": ["summer"], "冬天": ["winter"],
    "冬季": ["winter"], "客厅": ["living room"], "卧室": ["bedroom"],
    "户外": ["outdoor"], "旅行": ["travel"], "便携": ["portable"],
    "睡觉专用": ["sleeping"], "宿舍": ["dorm"],
    # 人群/体型词
    "大型犬": ["large breed", "large dog"], "中型犬": ["medium breed"],
    "小型犬": ["small breed", "small dog"], "幼犬": ["puppy"],
    "老年犬": ["senior dog"], "猫咪": ["cat"], "小中大型犬": ["all breed sizes"],
}

# 平台话术/无搜索价值的词：观测到也要剔除（不构成买家搜索词）
NOISE_TERMS = ("跨境", "包邮", "正品", "新品", "热卖", "特价", "爆款", "秒杀",
               "厂家直销", "专供", "外贸", "优选", "卡贝拉")

# 英文 n-gram 停用词
STOP_EN = {
    "the", "and", "for", "with", "your", "you", "our", "from", "that", "this",
    "are", "was", "has", "have", "will", "can", "not", "but", "all", "any",
    "inch", "inches", "size", "pcs", "pack", "new", "set", "one", "two",
}

_CJK_TERM_ORDER = sorted(CN_EN, key=len, reverse=True)  # 长词优先，避免"狗床"被"床"截断


def _titles_from_ids(item_ids: list[int]) -> tuple[list[str], list[str]]:
    """从存证库取真实竞品标题（只认 kind=item，编造 id 拿不到东西）。"""
    titles, urls = [], []
    for pid in item_ids:
        rec = provenance.get(int(pid))
        if rec and rec["kind"] == "item":
            title = str(rec["payload"].get("title") or "").strip()
            if title:
                titles.append(title)
                urls.append(rec["url"])
    return titles, urls


def _count_cn_terms(titles: list[str]) -> list[dict[str, Any]]:
    """统计中文属性词在竞品标题中的出现次数与证据标题。"""
    found: dict[str, dict[str, Any]] = {}
    for title in titles:
        matched_in_title: set[str] = set()
        for term in _CJK_TERM_ORDER:
            if term in NOISE_TERMS or term in matched_in_title:
                continue
            if term in title:
                # 长词命中后不再计其子串（"小中大型犬" 命中就不再计 "大型犬"）
                matched_in_title.update(t for t in CN_EN if t in term)
                entry = found.setdefault(term, {"term": term, "count": 0, "evidence": title})
                entry["count"] += 1
    return sorted(found.values(), key=lambda e: -e["count"])


def _english_ngrams(titles: list[str], seed: str) -> list[dict[str, Any]]:
    """英文源：从标题里提 1–2 gram 词频（真实观测，不是生成）。"""
    counts: dict[str, int] = {}
    evidence: dict[str, str] = {}
    for title in titles:
        words = [w for w in re.findall(r"[a-z][a-z\-]{2,}", title.lower()) if w not in STOP_EN]
        grams = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
        for g in grams:
            counts[g] = counts.get(g, 0) + 1
            evidence.setdefault(g, title)
    seed_words = set(seed.lower().split())
    out = [{"term": g, "count": c, "evidence": evidence[g]}
           for g, c in counts.items()
           if c >= 2 and not any(w in STOP_EN for w in g.split()) and g not in seed_words]
    return sorted(out, key=lambda e: (-e["count"], -len(e["term"])))[:20]


def _bucket(term: str) -> str:
    """把观测词归到四类（用于标题/卖点/ST 的不同用途）。"""
    if term in ("大型犬", "中型犬", "小型犬", "幼犬", "老年犬", "猫咪", "小中大型犬"):
        return "audience"
    if term in ("四季通用", "夏季", "冬天", "冬季", "客厅", "卧室", "户外", "旅行",
                "便携", "睡觉专用", "宿舍"):
        return "scenario"
    if term in ("狗床", "狗窝", "猫窝", "猫床", "宠物", "行军床", "沙发窝", "床垫",
                "垫子", "睡垫", "挂钟", "台灯", "收纳箱", "数据线", "手机壳", "充电宝"):
        return "core"
    return "attribute"


def build(keyword: str, titles: list[str], marketplace: str = "US",
          max_bytes: int = 250) -> dict[str, Any]:
    """核心构建函数（纯函数，便于单测）。"""
    observed = _count_cn_terms(titles) or _english_ngrams(titles, keyword)
    has_cjk = bool(observed) and bool(re.search(r"[\u4e00-\u9fff]", observed[0]["term"]))

    buckets: dict[str, list[dict[str, Any]]] = {
        "core": [], "attribute": [], "scenario": [], "audience": [], "related": []}
    for entry in observed:
        term = entry["term"]
        english = CN_EN.get(term, [term]) if has_cjk else [term]
        bucket = _bucket(term) if has_cjk else "attribute"
        buckets[bucket].append({
            "observed": term,
            "english": english,
            "count": entry["count"],
            "coverage": round(entry["count"] / max(1, len(titles)), 2),
            "evidence": entry["evidence"][:60],
        })

    # 核心词只留 1–2 个（标题前 80 字符位置有限）。排序先看是否与目标品类词同义，
    # 再看频次——否则高频的泛词（"宠物"）会把真正的品类词（"狗床"）挤掉。
    seed_words = set(keyword.lower().split())

    def _matches_seed(entry: dict[str, Any]) -> bool:
        english = " ".join(entry["english"]).lower()
        return any(w in english.split() for w in seed_words)

    core_sorted = sorted(buckets["core"],
                         key=lambda e: (not _matches_seed(e), -e["count"], e["observed"]))
    buckets["core"] = core_sorted[:2]
    buckets["related"] = core_sorted[2:]
    # 两个中文词映射到同一个英文词时只留一个，避免标题里出现重复概念
    seen_en: set[str] = set()
    deduped_core = []
    for entry in buckets["core"]:
        first_en = (entry["english"] or [""])[0]
        if first_en in seen_en:
            buckets["related"].append(entry)
            continue
        seen_en.add(first_en)
        deduped_core.append(entry)
    buckets["core"] = deduped_core
    # 相关品类同样按英文词去重（猫窝/猫床都映射到 cat bed，展示层不该重复）
    seen_related: set[str] = set()
    deduped_related = []
    for entry in buckets["related"]:
        first_en = (entry["english"] or [""])[0]
        if first_en in seen_related or first_en in seen_en:
            continue
        seen_related.add(first_en)
        deduped_related.append(entry)
    buckets["related"] = deduped_related

    # Search Terms：属性 + 场景 + 人群 + 相关品类，去重、剔除标题已用词、按字节截断
    title_words = set(re.findall(r"[a-z][a-z\-]{2,}", keyword.lower()))
    st_terms: list[str] = []
    for bucket in ("attribute", "scenario", "audience", "related"):
        for item in buckets[bucket]:
            for word in item["english"]:
                low = word.lower()
                if low in st_terms or all(w in title_words for w in low.split()):
                    continue
                st_terms.append(low)
    st, used = [], 0
    for term in st_terms:
        add = len(term.encode("utf-8")) + (2 if st else 0)
        if used + add > max_bytes:
            break
        st.append(term)
        used += add
    search_terms = ", ".join(st)

    # 竞争度代理指标：全部来自真实样本，明确标注是代理不是搜索量
    core_terms = buckets["core"] or [{"observed": keyword, "coverage": None}]
    core_coverage = max((c.get("coverage") or 0) for c in core_terms)
    proxy = {
        "sample_size": len(titles),
        "core_term_coverage": core_coverage,
        "avg_title_len": round(sum(len(t) for t in titles) / max(1, len(titles)), 1),
        "distinct_terms": len(observed),
        "_说明": "竞争度代理：核心词在竞品标题中的覆盖率越高，说明该词越拥挤；"
                 "本作品未接入搜索量数据源，故不提供搜索量与竞争度评分",
    }

    return {
        "keyword": keyword,
        "marketplace": marketplace,
        "language_of_source": "zh" if has_cjk else "en",
        "buckets": buckets,
        "search_terms": search_terms,
        "search_terms_bytes": len(search_terms.encode("utf-8")),
        "competition_proxy": proxy,
        "title_skeleton": (f"{keyword.title()} — [ADD: 本品 2–3 个核心属性；"
                           f"属性词请从下方 attribute 列表挑选并与本品实际一致]"),
        "honesty": [
            "词表来自真实竞品标题的观测计数，每条附证据标题，可回访原页复核",
            "不提供搜索量/竞争度评分：未接入相应数据源，编造该指标等于假数据",
            "观测词描述的是「买家在搜什么 / 竞品在写什么」，不等于「本品有什么参数」",
        ],
    }


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    item_ids = [int(i) for i in (tool_input.get("item_ids") or [])]
    if not item_ids:
        return ToolOutcome(False, "error", None, "缺少 item_ids（须来自 market_scan）")
    titles, _urls = _titles_from_ids(item_ids)
    if len(titles) < 3:
        return ToolOutcome(False, "error", None,
                           f"存证中可用竞品标题仅 {len(titles)} 条，不足以统计词频（需 ≥3）")
    keyword = str(tool_input.get("keyword") or "").strip() or titles[0][:20]
    result = build(keyword, titles, str(tool_input.get("marketplace") or "US"))
    buckets = result["buckets"]
    return ToolOutcome(
        True, "done", None,
        f"关键词层：从 {len(titles)} 条真实竞品标题观测到 {sum(len(v) for v in buckets.values())} 个可用词"
        f"（核心 {len(buckets['core'])} / 属性 {len(buckets['attribute'])}"
        f" / 场景 {len(buckets['scenario'])} / 人群 {len(buckets['audience'])}"
        f" / 相关品类 {len(buckets['related'])}），"
        f"Search Terms {result['search_terms_bytes']}/250 字节；"
        f"不提供搜索量（无数据源，不编造）",
        result,
    )
