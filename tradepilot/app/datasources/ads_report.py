# Copyright 2026 TradePilot project. Apache-2.0.
"""数据源适配层：把外部真实报告接进工具，且**只做映射、不做补数**。

设计前提（见 docs/keyword-data-sources.md）：
- 广告搜索词报告是当前门槛最低的真实数据源（不需要商标/品牌注册）；
- 报告未配置时必须**优雅降级**，而不是报错或猜数——参赛环境没有广告账户，
  这条路径平时不生效，但架构上必须已经通。

三条硬规矩：
1. 只映射报告里**确实存在**的列，缺列就是 None，不按行业均值填；
2. 任何一条数据都带**报告周期**，脱离时间谈曝光没有意义；
3. 表头兼容中英文与亚马逊导出的多种写法，但**不认识就跳过并计数**，
   宁可少算也不猜列语义。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Iterable

# 表头别名：亚马逊后台中英文导出、Ads API 字段名混用，这里统一收敛。
# 只收**语义确定**的列；拿不准的（如"搜索词排名"）不猜。
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "term": ("customer search term", "search term", "query", "客户搜索词", "搜索词"),
    "impressions": ("impressions", "曝光量", "展示量"),
    "clicks": ("clicks", "点击量", "点击次数"),
    "orders": ("7 day total orders (#)", "14 day total orders (#)", "orders",
               "7天总订单数(#)", "14天总订单数(#)", "订单数"),
    "spend": ("spend", "花费", "总花费"),
    "sales": ("7 day total sales", "14 day total sales", "sales", "销售额"),
}

_NUM_CLEAN = re.compile(r"[^\d.\-]")

# 报告末尾的合计行：搜索词列写的是 "Total"/"总计"，曝光是数字，混进来会让曝光翻倍。
# 这是真实亚马逊导出里一定存在的行，必须显式剔除并单独计数（不能静默吞掉）。
_SUMMARY_MARKERS = ("total", "grand total", "sum", "总计", "合计", "汇总", "总和")


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _map_columns(headers: Iterable[str]) -> dict[str, str]:
    """实际表头 → 标准字段名。未识别的列直接忽略（不参与计算）。"""
    mapping: dict[str, str] = {}
    normalized = {_norm_header(h): h for h in headers if h}
    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


def _num(value: Any) -> float | None:
    """解析数字：容忍千分位、货币符号、百分号；解析不了返回 None（不填 0）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return None
    is_pct = text.endswith("%")
    cleaned = _NUM_CLEAN.sub("", text.replace(",", "").rstrip("%"))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned) / (100.0 if is_pct else 1.0)
    except ValueError:
        return None


def normalize_term(term: str) -> str:
    """搜索词归一化：小写、去首尾空白、压缩空格。

    注意**不做词干化/同义合并**——那是分析判断，不是清洗；
    "dog bed" 与 "dog beds" 在报告里就是两个词，合并会篡改原始数据。
    """
    return re.sub(r"\s+", " ", (term or "").strip().lower())


def load_search_term_report(rows: Iterable[dict], period: str | None = None) -> dict:
    """广告搜索词报告 → 归一化词表。返回结构可直接给 keywords.build 消费。

    `rows` 为 DictReader 产生的行；`period` 是报告覆盖的日期区间（如 2026-10-01~2026-10-31），
    必须显式传入——没有时间区间的曝光量是不可解释的。
    """
    rows = list(rows)
    if not rows:
        return {"data_source": "ads_search_term_report", "configured": False,
                "period": period, "terms": {}, "rows": 0, "skipped": 0,
                "summary_rows": 0, "reason": "报告为空"}
    mapping = _map_columns(rows[0].keys())
    if "term" not in mapping:
        return {"data_source": "ads_search_term_report", "configured": False,
                "period": period, "terms": {}, "rows": len(rows), "skipped": len(rows),
                "summary_rows": 0, "reason": "未识别到搜索词列——表头不在已知别名内，拒绝猜测"}

    terms: dict[str, dict[str, Any]] = {}
    skipped = 0
    summary_rows = 0
    for row in rows:
        term = normalize_term(row.get(mapping["term"], ""))
        if term in _SUMMARY_MARKERS or term.startswith(("total ", "总计", "合计")):
            summary_rows += 1     # 合计行单独计数：它是被识别后剔除的，不是解析失败
            continue
        impressions = _num(row.get(mapping.get("impressions", ""), None))
        if not term or impressions is None:
            skipped += 1          # 空行、非数字行一律跳过并计数
            continue
        bucket = terms.setdefault(term, {"impressions": 0.0, "clicks": 0.0,
                                         "orders": 0.0, "spend": 0.0, "sales": 0.0})
        for field in ("impressions", "clicks", "orders", "spend", "sales"):
            value = _num(row.get(mapping.get(field, ""), None))
            if value is not None:
                bucket[field] += value

    for bucket in terms.values():
        clicks, orders = bucket["clicks"], bucket["orders"]
        bucket["ctr"] = round(clicks / bucket["impressions"], 4) if bucket["impressions"] else None
        bucket["cvr"] = round(orders / clicks, 4) if clicks else None
        for field in ("impressions", "clicks", "orders"):
            bucket[field] = int(bucket[field])
        for field in ("spend", "sales"):
            bucket[field] = round(bucket[field], 2)

    return {"data_source": "ads_search_term_report", "configured": True,
            "period": period, "terms": terms, "rows": len(rows), "skipped": skipped,
            "summary_rows": summary_rows, "reason": None}


def load_csv(path: str | Path, period: str | None = None) -> dict:
    """从本地 CSV 载入（卖家后台导出的报告）。文件不存在时降级，不抛异常。"""
    p = Path(path)
    if not p.exists():
        return {"data_source": "ads_search_term_report", "configured": False,
                "period": period, "terms": {}, "rows": 0, "skipped": 0,
                "summary_rows": 0, "reason": f"报告文件不存在：{p}"}
    text = p.read_text(encoding="utf-8-sig")
    return load_search_term_report(csv.DictReader(io.StringIO(text)), period=period)


def attach(keyword_data: dict, report: dict | None) -> dict:
    """把报告里的曝光/点击挂到关键词层输出上；未配置则原样返回并标注。

    刻意**不写 search_volume**：广告报告没有搜索量字段，编一个就是假数据。
    """
    data = dict(keyword_data)
    if not report or not report.get("configured"):
        data["search_term_evidence"] = {
            "data_source": "not_configured",
            "reason": (report or {}).get("reason") or "未接入广告搜索词报告",
            "note": "竞争度仍用竞品标题覆盖率代理；搜索量不提供（无数据源，不编造）",
        }
        return data

    terms = report["terms"]
    matched = 0
    buckets = {}
    for name, entries in (data.get("buckets") or {}).items():
        for entry in entries:
            for candidate in [entry.get("observed")] + list(entry.get("english") or []):
                key = normalize_term(candidate or "")
                if key and key in terms:
                    entry["ads"] = terms[key]
                    matched += 1
                    break
        buckets[name] = entries
    data["buckets"] = buckets
    data["search_term_evidence"] = {
        "data_source": "ads_search_term_report",
        "period": report.get("period"),
        "rows": report.get("rows"), "skipped": report.get("skipped"),
        "matched_terms": matched,
        "note": "曝光/点击/订单来自广告搜索词报告，仅覆盖广告触达过的词；"
                "搜索量仍未提供（报告无该字段）",
    }
    return data
