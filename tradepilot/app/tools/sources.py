# Copyright 2026 TradePilot project. Apache-2.0.
"""市场数据源适配器：每个源一个解析器，返回统一结构的真实商品卡。

- dangdang（国内零售参考价，CNY）：本网络已验证可达（公开搜索页，真实价格）；
- walmart（美国站竞品价，USD）：实现就绪；该站对本机网络/无头浏览器均反爬，
  预期在云上部署环境（魔搭/阿里云）启用——运行时如实报告可达性，绝不降级为合成数据。

统一商品卡结构：{title, price, currency, source, source_url, provenance_id}
"""
from __future__ import annotations

import re
from typing import Any

from .fetch import _looks_blocked, fetch, fetch_browser

SOURCES = {
    "dangdang": {
        "label": "当当网 · 国内零售参考价（CNY）",
        "search_url": "https://search.dangdang.com/?key={kw}&act=input",
        "currency": "CNY",
        "channel": "browser",   # 价格由前端 JS 渲染，必须走浏览器通道
    },
    "walmart": {
        "label": "Walmart · 美国站竞品价（USD）",
        "search_url": "https://www.walmart.com/search?q={kw}",
        "currency": "USD",
        "channel": "browser",
    },
}


def parse_dangdang(html: str, source_url: str) -> list[dict[str, Any]]:
    """解析当当搜索页（浏览器渲染后 DOM）：按 <li> 块切分，块内配对标题与现价。

    - 价格：class="search_now_price">¥259.00（真实现价）；
    - 标题：a[title] 属性，长度 14–90 过滤掉排序控件（≤10 字）与店铺名（<14 字）；
    - 块内独立配对，杜绝跨商品错配；解析不出返回空列表（调用方如实报错）。
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in re.split(r"<li\b", html)[1:]:
        price_m = re.search(
            r'class="search_now_price"[^>]*>[^¥￥]*[¥￥]\s?([0-9]+(?:\.[0-9]{1,2})?)', chunk)
        if not price_m:
            continue
        title_m = re.search(r'title="([^"]{14,90})"', chunk)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        if title in seen:
            continue
        seen.add(title)
        link = re.search(r'href="(https?://[^"]*dangdang\.com[^"]*\.html[^"]*)"', chunk)
        items.append({"title": title, "price": float(price_m.group(1)),
                      "source_url": (link.group(1) if link else source_url)})
        if len(items) >= 16:
            break
    return items[:12]


def parse_walmart(html: str, source_url: str) -> list[dict[str, Any]]:
    """Walmart 搜索页解析（部署态启用）。解析不出真实字段返回空列表。"""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'\{"(?:id|productId)":"?\d+[^{}]*?"price":\s*([0-9]+(?:\.[0-9]{1,2})?)[^{}]*?\}',
        html, re.S,
    ):
        blob = match.group(0)
        name = None
        for field in ("name", "title", "productName"):
            m = re.search(rf'"{field}":"([^"\\]{{5,120}})"', blob)
            if m:
                name = m.group(1)
                break
        if not name or name in seen:
            continue
        seen.add(name)
        items.append({"title": name[:110], "price": float(match.group(1)),
                      "source_url": source_url})
        if len(items) >= 12:
            break
    return items


async def scan(source: str, keyword: str, limit: int = 8) -> dict[str, Any]:
    """执行一个源的真实抓取+解析。返回 {items, source_url, channel, currency}；
    任何不诚实路径（拦截页/解析失败）抛 ValueError，由工具层转为 held/error。"""
    spec = SOURCES.get(source)
    if spec is None:
        raise ValueError(f"未知数据源 {source}（可用：{', '.join(SOURCES)}）")
    url = spec["search_url"].format(kw=keyword.replace(" ", "+"))
    if spec.get("channel") == "browser":
        result = await fetch_browser(url)
    else:
        result = await fetch(url)
    result["channel"] = spec.get("channel", "http")
    html = result["html"]
    if _looks_blocked(html):
        raise ValueError(
            f"{source} 返回拦截页或空壳（channel={result['channel']}）——"
            "该源在当前网络不可达，如实报告，不输出合成数据。")
    parser = parse_dangdang if source == "dangdang" else parse_walmart
    items = parser(html, result["url"])
    if not items:
        raise ValueError(
            f"{source} 页面可达但未解析出真实商品数据——宁可报错也不编造。")
    return {"items": items[:limit], "source_url": result["url"],
            "channel": result["channel"], "currency": spec["currency"],
            "source_label": spec["label"]}
