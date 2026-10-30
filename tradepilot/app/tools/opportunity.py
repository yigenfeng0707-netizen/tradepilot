# Copyright 2026 TradePilot project. Apache-2.0.
"""选品雷达（MVP 模块①）：多关键词真实扇描 + 样本观测口径的机会分。

这个模块存在的意义是回答"下一步该把调研精力放哪个品类"，不是回答"哪个品类好卖"。
两者的区别就是这个模块能不能上答辩的关键：

诚实边界（必须随分数一起出现在界面上）：
- 分数**不是需求量**，也不是销量、搜索量、市场份额——本作品没有接入任何此类数据源，
  接不出来就不写（与关键词层"不给月搜索量"是同一条纪律）；
- 五个分量全部由**本批真实抓到的样本**算出，可点存证回访：
  yield   样本量充足度：可用样本数 / 目标样本数（8 条）
  noise   样本干净度：保留条数 / 原始条数（越高越干净，跨品类噪声越少）
  depth   价格水位：中位价在本批候选中的相对分位（只在本批内可比）
  spread  定位空间：价格带离散度 (max-min)/median 的相对分位
  terms   表达丰富度：去重实词数 / 可用样本数 的相对分位
- 分数用于**排序调研优先级**，不构成采购、备货或上架决策依据。

候选关键词来源有两种，界面与导出都必须标明是哪一种：
- user：任务参数里给定的关键词（用户自己的假设）
- builtin_hypothesis：内置候选词。**它是待验证的假设，不是数据**，
  内置词本身不带任何销量或热度依据，绝不可被表述成"平台热销品类"。
"""
from __future__ import annotations  # noqa: F401


import re
from typing import Any

from .. import provenance
from .executor import ToolOutcome
from .sources import is_book, relevant, scan

TARGET_SAMPLE = 8      # 单候选词的可用样本目标条数（yield 分量的分母）
MIN_SAMPLE = 3         # 低于这个条数不足以谈价格带，该词整体不参与评分（并如实记账）
MAX_KEYWORDS = 5       # 默认扫描词数：每词一次真实抓取，这个数直接决定任务耗时
KEYWORD_CAP = 8
SCAN_LIMIT = 12

# 内置候选词 = **待验证的假设**，没有任何销量/热度依据，不得表述为"平台热销品类"。
# 用户传 keywords 时优先用用户的词（那是用户自己的假设，来源标注会跟着变）。
BUILTIN_HYPOTHESES: dict[str, list[str]] = {
    "pet": ["dog bed", "cat bed", "dog toy", "cat litter", "pet carrier"],
    "home": ["storage box", "desk lamp", "wall clock", "throw pillow", "bath towel"],
    "electronics": ["usb c cable", "phone case", "power bank", "wall charger", "hdmi cable"],
}

# 权重是设计选择（不是数据结论），写进 method 随分数一起交付，可被质疑与调整
WEIGHTS: dict[str, float] = {"yield": 0.20, "noise": 0.15,
                             "depth": 0.25, "spread": 0.25, "terms": 0.15}

_TOKEN = re.compile(r"[A-Za-z\u4e00-\u9fa5]{3,}")


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名同价去重（与 market_scan 同口径），避免一条商品被重复计入价格带。"""
    seen: set[tuple[str, float]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("title", "")).strip()[:40], round(float(item.get("price", 0) or 0), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _percentile(values: list[float]) -> list[float]:
    """批内相对分位（0–100）。单值或全等时统一给 50——不凭空造出差距。"""
    n = len(values)
    if n == 0:
        return []
    if n == 1 or len(set(values)) == 1:
        return [50.0] * n
    return [round(sum(1 for w in values if w <= v) / n * 100.0, 1) for v in values]


def _distinct_terms(titles: list[str]) -> int:
    """去重实词数：只数长度 ≥3 的词形，不做语义判断（语义判断就会引入编造空间）。"""
    tokens: set[str] = set()
    for title in titles:
        tokens.update(t.lower() for t in _TOKEN.findall(str(title)))
    return len(tokens)


def resolve_keywords(tool_input: dict[str, Any], category: str) -> tuple[list[str], str]:
    """待扫描的候选词：用户给的优先，否则用内置假设词。返回 (词表, 来源标记)。"""
    raw = str(tool_input.get("keywords", "") or "").strip()
    try:
        cap = int(tool_input.get("max_keywords") or MAX_KEYWORDS)
    except (TypeError, ValueError):
        cap = MAX_KEYWORDS
    cap = max(1, min(cap, KEYWORD_CAP))
    if raw:
        words = [w.strip() for w in re.split(r"[,，;；\n]", raw) if w.strip()]
        origin = "user"
    else:
        words = list(BUILTIN_HYPOTHESES.get(category, []))
        origin = "builtin_hypothesis"
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out[:cap], origin


def price_stats(prices: list[float]) -> dict[str, float]:
    """min/median/max。空表返回全 0，由调用方按"无数据"处理而不是当作低价。"""
    if not prices:
        return {"min": 0.0, "median": 0.0, "max": 0.0, "spread_pct": 0.0}
    ordered = sorted(prices)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    spread = (ordered[-1] - ordered[0]) / median * 100.0 if median else 0.0
    return {"min": round(ordered[0], 2), "median": round(median, 2),
            "max": round(ordered[-1], 2), "spread_pct": round(spread, 1)}


def score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把观测行换算成五分量与机会分。全部是批内相对量，不引入任何外部口径。"""
    if not rows:
        return []
    depth_p = _percentile([r["price"]["median"] for r in rows])
    spread_p = _percentile([r["price"]["spread_pct"] for r in rows])
    terms_p = _percentile([float(r["distinct_terms"]) for r in rows])
    scored: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        kept, raw_count = row["kept"], row["raw_count"]
        components = {
            "yield": round(min(100.0, kept / TARGET_SAMPLE * 100.0), 1),
            "noise": round(kept / raw_count * 100.0, 1) if raw_count else 0.0,
            "depth": depth_p[i], "spread": spread_p[i], "terms": terms_p[i],
        }
        score = round(sum(components[k] * WEIGHTS[k] for k in WEIGHTS), 1)
        scored.append({**row, "components": components, "score": score})
    scored.sort(key=lambda r: r["score"], reverse=True)
    for rank, row in enumerate(scored, 1):
        row["rank"] = rank
    return scored


async def _observe(source: str, keyword: str, category: str, limit: int) -> dict[str, Any]:
    """真实扫描一个候选词。抓不到 / 解析不出即抛错，由调用方如实计入 scans_failed。"""
    result = await scan(source, keyword, limit)
    raw_items = result["items"]
    kept = [i for i in raw_items if not is_book(i["title"])
            and relevant(i["title"], category, result["keyword_used"])]
    items = _dedupe(kept)
    row: dict[str, Any] = {
        "keyword": keyword,
        "keyword_used": result["keyword_used"],
        "currency": result["currency"],
        "raw_count": len(raw_items),
        "kept": len(items),
        "filtered_noise": len(raw_items) - len(items),
        "noise_rate": round(1 - len(items) / len(raw_items), 3) if raw_items else 1.0,
        "price": price_stats([float(i["price"]) for i in items]),
        "distinct_terms": _distinct_terms([i["title"] for i in items]),
        "source_url": result["source_url"],
        "robots": result.get("robots"),
        "top_items": [{"title": str(i["title"])[:80], "price": float(i["price"]),
                       "url": i.get("source_url")} for i in items[:5]],
    }
    row["provenance_ids"] = [provenance.record(
        "observation", result["source_url"],
        {k: row[k] for k in ("keyword", "keyword_used", "currency", "raw_count", "kept",
                             "filtered_noise", "noise_rate", "price", "distinct_terms",
                             "top_items")},
        raw_text=f"{keyword}|{result['source_url']}")]
    return row


_METHOD = (
    "五分量与机会分全部由本批真实抓取样本算出：yield=可用样本数/" + str(TARGET_SAMPLE) +
    "（上限 100）；noise=保留/原始（越高越干净）；depth=中位价批内相对分位；"
    "spread=(max−min)/median 批内相对分位；terms=去重实词数批内相对分位。"
    "机会分=" + " + ".join(f"{WEIGHTS[k]:.2f}×{k}" for k in WEIGHTS) +
    "。权重是设计选择而非数据结论；分位只在本次候选集内可比，换一批候选会重排。"
    "core_term_coverage 未计算（需要先定义核心词口径，当前没有可靠依据，宁可留空）。")

_BASIS = (
    "机会分**不是需求量**，也不是销量、搜索量或市场份额——本作品未接入任何此类数据源。"
    "它只衡量「本批真实样本的分析价值与定位空间」，用途是把下一步调研排个先后，"
    "不构成采购、备货或上架决策依据。词源为 builtin_hypothesis 时，候选词本身只是待验证假设，"
    "没有任何热度依据，不得对外表述为「平台热销品类」。")



async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    category = str(tool_input.get("category", "")).strip().lower()
    source = str(tool_input.get("source", "newegg")).strip().lower()
    try:
        limit = max(3, min(int(tool_input.get("limit") or SCAN_LIMIT), SCAN_LIMIT))
    except (TypeError, ValueError):
        limit = SCAN_LIMIT

    keywords, origin = resolve_keywords(tool_input, category)
    if not keywords:
        return ToolOutcome(
            False, "error", None,
            f"没有可扫描的候选词（category={category or '空'}）：内置假设词只覆盖 "
            f"{'/'.join(BUILTIN_HYPOTHESES)}，其余品类请用 keywords 参数自己给词——"
            "不替你臆造候选品类。")

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for word in keywords:
        try:
            rows.append(await _observe(source, word, category, limit))
        except ValueError as exc:
            failures.append({"keyword": word, "error": str(exc)[:220]})
        except Exception as exc:  # noqa: BLE001 — 网络异常同样如实记账，不静默丢词
            failures.append({"keyword": word,
                             "error": f"{type(exc).__name__}: {exc}"[:220]})

    usable = [r for r in rows if r["kept"] >= 3]
    # 抓成功但样本不足的词必须单独记账：否则摘要写"4/5"而 scans_failed 为空，
    # 那 1 个词就凭空消失了——一个说不清去处的数字，比不写更糟。
    thin = [{"keyword": r["keyword"], "kept": r["kept"], "raw_count": r["raw_count"],
             "filtered_noise": r["filtered_noise"],
             "reason": f"可用样本 {r['kept']} 条 < 最低 {MIN_SAMPLE} 条"}
            for r in rows if r["kept"] < MIN_SAMPLE]
    candidates = score_rows(usable)
    if not candidates:
        return ToolOutcome(
            False, "error", None,
            f"选品雷达：{len(keywords)} 个候选词全部未取得 ≥{MIN_SAMPLE} 条可用样本"
            f"（{len(failures)} 个抓取失败、{len(thin)} 个样本过少）"
            "——样本不足就不出分，不编造机会排名",
            {"source": source, "keywords_requested": keywords, "scans_run": len(keywords),
             "scans_failed": failures, "scans_thin": thin, "min_sample": MIN_SAMPLE})

    top = candidates[0]
    out_candidates = [{
        "keyword": c["keyword"], "keyword_used": c["keyword_used"],
        "sample_size": c["kept"], "raw_count": c["raw_count"],
        "filtered_noise": c["filtered_noise"], "noise_rate": c["noise_rate"],
        "price_min": c["price"]["min"], "price_median": c["price"]["median"],
        "price_max": c["price"]["max"], "spread_pct": c["price"]["spread_pct"],
        "distinct_terms": c["distinct_terms"], "core_term_coverage": None,
        "provenance_ids": c["provenance_ids"], "source_url": c["source_url"],
        "score": c["score"], "rank": c["rank"], "components": c["components"],
        "top_items": c["top_items"],
    } for c in candidates]

    gaps = []
    if failures:
        gaps.append(f"{len(failures)} 个词未取得数据")
    if thin:
        gaps.append(f"{len(thin)} 个词样本过少（{', '.join(t['keyword'] for t in thin)}）")
    summary = (f"选品雷达：真实扫描 {len(candidates)}/{len(keywords)} 个候选词"
               f"（{source}，{top['currency']}，词源 {origin}），"
               f"Top1「{top['keyword']}」机会分 {top['score']}"
               f"（可用样本 {top['kept']} 条 · 价格带 {top['price']['min']}–"
               f"{top['price']['max']} {top['currency']}）"
               + (f"；其余 {'、'.join(gaps)}，原因已逐条列出" if gaps else ""))
    return ToolOutcome(
        True, "done", None, summary,
        {"source": source, "category": category, "currency": top["currency"],
         "keyword_origin": origin, "keywords_requested": keywords,
         "scans_run": len(keywords), "scans_ok": len(rows), "scans_usable": len(candidates),
         "min_sample": MIN_SAMPLE, "target_sample": TARGET_SAMPLE,
         "scans_failed": failures, "scans_thin": thin,
         "candidates": out_candidates, "weights": WEIGHTS,
         "method": _METHOD, "basis": _BASIS})
