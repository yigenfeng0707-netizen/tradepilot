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

from .fetch import _looks_blocked, fetch, fetch_browser, robots_allowed

SOURCES = {
    "dangdang": {
        "label": "当当网 · 国内零售参考价（CNY）",
        "search_url": "https://search.dangdang.com/?key={kw}&act=input",
        "currency": "CNY",
        "channel": "browser",   # 价格由前端 JS 渲染，必须走浏览器通道
        "lang": "zh",
    },
    "walmart": {
        "label": "Walmart · 美国站竞品价（USD）",
        "search_url": "https://www.walmart.com/search?q={kw}",
        "currency": "USD",
        "channel": "browser",
        "lang": "en",
    },
}

# 中文站检索词映射：英文品类词直接搜中文站会命中同名童书（"dog bed" → 《The Dog on the
# Bed》），必须换成语义等价的中文检索词。可在任务参数里用 keyword_cn 覆盖。
CN_KEYWORD_ALIASES = {
    "dog bed": "狗床", "pet bed": "宠物窝", "cat bed": "猫窝",
    "dog toy": "狗玩具", "cat litter": "猫砂", "pet carrier": "宠物航空箱",
    "wall clock": "挂钟", "desk lamp": "台灯", "storage box": "收纳箱",
    "usb c cable": "type-c数据线", "phone case": "手机壳", "power bank": "充电宝",
}

# 书籍类噪声标记：中文站搜索结果里混入的图书/教辅会污染价格带
BOOK_MARKERS = ("出版社", "书店", "书籍", "阅读书系", "彩插", "彩图", "插图版",
                "绘本", "漫画", "课外", "教辅", "ISBN", "图书", "精装", "平装",
                "著 ", "编著", "译", "少年中国", "正版")
_ISBN = re.compile(r"97[89]\d{10}")

# 品类相关性词：标题至少命中一个，否则视为跨品类噪声
CATEGORY_TOKENS = {
    "pet": ("狗", "犬", "猫", "宠物", "窝", "猫砂", "牵引", "航空箱"),
    "home": ("家居", "收纳", "挂钟", "台灯", "枕", "垫", "架", "箱", "柜"),
    "electronics": ("数据线", "充电", "手机", "耳机", "电源", "适配器", "电子"),
}

# 人用寝具标记：宠物品类搜索常混入"印着狗的图案"的人类床垫（实测 ¥1212 的
# "线条小狗床垫 宿舍寝室"），会直接把价格带上限拉高一个数量级。
HUMAN_BEDDING = ("宿舍", "寝室", "租房", "单人床", "双人床", "榻榻米", "学生",
                 "人用", "上下铺", "折叠床")


def resolve_keyword(source: str, keyword: str, keyword_cn: str = "") -> str:
    """把英文品类词转成目标源可用的检索词（中文源用中文词）。"""
    if keyword_cn:
        return keyword_cn
    if SOURCES.get(source, {}).get("lang") == "zh":
        return CN_KEYWORD_ALIASES.get(keyword.strip().lower(), keyword)
    return keyword


def is_book(title: str) -> bool:
    return bool(_ISBN.search(title)) or any(m in title for m in BOOK_MARKERS)


def relevant(title: str, category: str) -> bool:
    if category == "pet" and any(m in title for m in HUMAN_BEDDING):
        return False
    tokens = CATEGORY_TOKENS.get(category)
    if not tokens:
        return True
    return any(t in title for t in tokens)


def parse_dangdang(html: str, source_url: str) -> list[dict[str, Any]]:
    """解析当当搜索页（浏览器渲染后 DOM）：按 <li> 块切分，块内配对标题与现价。

    - 价格：兼容两种渲染模式：search_now_price（大图）与 price_n（列表/小图）；
    - 标题：a[title] 属性，长度 14–90 过滤掉排序控件（≤10 字）与店铺名（<14 字）；
    - 块内独立配对，杜绝跨商品错配；解析不出返回空列表（调用方如实报错）。
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 两个价格标记各试一次，先大图后列表；货币符号兼容实体编码（&yen; / &#165;）
    sym = r"(?:¥|￥|&yen;|&#165;|&#xa5;)"
    price_patterns = [
        rf'class="search_now_price"[^>]*>[^¥￥]*{sym}\s?([0-9]+(?:\.[0-9]{{1,2}})?)',
        rf'class="price_n"[^>]*>[^¥￥]*{sym}\s?([0-9]+(?:\.[0-9]{{1,2}})?)',
    ]
    for chunk in re.split(r"<li\b", html)[1:]:
        price_raw = None
        for pat in price_patterns:
            m = re.search(pat, chunk)
            if m:
                price_raw = m.group(1)
                break
        if price_raw is None:
            continue
        title_m = re.search(r'title="([^"]{14,90})"', chunk)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        if title in seen:
            continue
        seen.add(title)
        link = re.search(r'href="(https?://[^"]*dangdang\.com[^"]*\.html[^"]*)"', chunk)
        items.append({"title": title, "price": float(price_raw),
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


async def scan(source: str, keyword: str, limit: int = 8,
               keyword_cn: str = "") -> dict[str, Any]:
    """执行一个源的真实抓取+解析。返回 {items, source_url, channel, currency, keyword_used}；
    任何不诚实路径（robots 禁止/拦截页/解析失败）抛 ValueError，由工具层转为 error。"""
    spec = SOURCES.get(source)
    if spec is None:
        raise ValueError(f"未知数据源 {source}（可用：{', '.join(SOURCES)}）")
    keyword_used = resolve_keyword(source, keyword, keyword_cn)
    url = spec["search_url"].format(kw=keyword_used.replace(" ", "+"))

    # robots 合规闸门：声明"尊重 robots"就必须有代码拦在这里
    allowed, robots_note = await robots_allowed(url)
    if not allowed:
        raise ValueError(
            f"{source} 被 robots.txt 拒绝：{robots_note}——本作品不做绕过，"
            "请改用该站的官方 API/授权接口，或选择允许抓取的公开源。")

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
            "source_label": spec["label"], "robots": robots_note,
            "keyword_used": keyword_used}
