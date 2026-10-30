# Copyright 2026 TradePilot project. Apache-2.0.
"""市场数据源适配器：每个源一个解析器，返回统一结构的真实商品卡。

- dangdang（国内零售参考价，CNY）：本网络已验证可达（公开搜索页，真实价格）；
- newegg（美国站竞品价，USD）：本网络实测可达（公开搜索页，免登录）。单品页链接取自
  结果页 DOM 里真实存在的 <a href="…/p/…">；本机直访单品页会触发人机校验，
  故存证以"搜索页 + 单品 href"为准（见 docs/sources-audit.md 实测记录）；
- walmart（美国站竞品价，USD）：解析器实现保留，但**不作为可达源承诺**：
  2026-10-30 实测 robots.txt 对通用 UA 禁止 /search（`robots_allowed()` 返回
  (False, "robots.txt 禁止抓取 …")），respect_robots=1 下必然如实报错；
  无头浏览器直取也返回 17.6KB 的 "Robot or human?" 拦截页。

统一商品卡结构：{title, price, currency, source, source_url, provenance_id}
"""
from __future__ import annotations

import json
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
    "newegg": {
        # 可达性：本机网络（亚太出口）实测可抓，robots.txt 对 /p/pl 搜索页无 Disallow；
        # 价格在服务端内联的 window.__initialState__ JSON 里，先试 httpx、被 403 时走浏览器。
        "label": "Newegg · 美国站竞品价（USD，公开搜索页/免登录）",
        "search_url": "https://www.newegg.com/p/pl?d={kw}",
        "currency": "USD",
        "channel": "browser",   # httpx 直取返回 403，浏览器通道稳定拿到 1.5MB 真实结果页
        "lang": "en",
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

# 品类相关性：两层判定（主名词命中 + 排除词），不是"命中任一泛化词"。
#
# 旧实现（CATEGORY_TOKENS 单层）实测过宽：electronics 里带"充电"，于是
# "飞科 USB-C 剃须刀 ¥180"混进 `usb c cable` 的竞品样本，直接把价格带上限抬高一个量级。
# 现在必须：
#   1) 命中该品类的**主名词**（狗床/狗窝/数据线/usb c/收纳箱…这类实物名，
#      不用"充电""架""箱""柜"这种到处都有的泛化词）；
#   2) 且不含**排除词**（整机/人用品：剃须刀、充电宝、宿舍床垫…）。
# 排除词是品类级"异物"，但若检索词本身就在找它（如 keyword="power bank"），
# 该条排除词自动失效——否则用户搜充电宝会被自己的过滤器清空。
CATEGORY_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "pet": {
        "primary": (
            "狗床", "狗窝", "犬窝", "猫窝", "宠物窝", "宠物床", "狗垫", "猫垫",
            "狗笼", "猫笼", "猫砂", "狗玩具", "猫玩具", "猫爬架", "宠物航空箱",
            "牵引绳", "dog bed", "cat bed", "pet bed", "dog crate", "cat litter",
            "dog toy", "cat toy", "pet carrier", "dog house",
        ),
        # 排除词：人用寝具/家具（"线条小狗床垫 宿舍寝室"¥1212 曾把宠物价格带拉高一个量级）
        "exclude": (
            "宿舍", "寝室", "租房", "单人床", "双人床", "榻榻米", "学生", "人用",
            "上下铺", "折叠床", "儿童床", "婴儿床", "行军床",
        ),
    },
    "home": {
        "primary": (
            "收纳箱", "收纳盒", "储物箱", "挂钟", "壁钟", "台灯", "落地灯", "抱枕",
            "靠垫", "置物架", "鞋架", "衣架", "衣柜", "床头柜", "储物柜",
            "storage box", "storage bin", "wall clock", "desk lamp", "shelf",
            "shoe rack", "wardrobe", "nightstand",
        ),
        # 排除词：宠物用品混进家居价格带（搜索 "storage box" 常带出猫窝收纳）
        "exclude": ("狗窝", "猫窝", "宠物窝", "dog bed", "cat bed", "pet bed"),
    },
    "electronics": {
        "primary": (
            "数据线", "充电线", "快充线", "转接线", "延长线", "编织线",
            "usb c", "usb-c", "type-c", "typec", "lightning", "cable", "cord",
            "适配器", "adapter", "充电器", "charger",
        ),
        # 排除词：整机/大件（剃须刀、充电宝、音箱、显示器…）不是"线缆/配件"，
        # 混进来会让配件价格带失真。
        # 英文只收"绝不会出现在线缆标题里的整机名"：monitor / laptop / printer 会出现在
        # 线缆的适用设备描述里（实测 "Hannord USB C Cable … for 4K Monitor" ¥10.99 线缆
        # 被 "monitor" 误杀），故不收。
        "exclude": (
            "剃须刀", "剃须", "剃毛", "shaver", "razor", "充电宝", "移动电源",
            "power bank", "电动牙刷", "toothbrush", "音箱", "显示器", "笔记本",
            "打印机",
        ),
    },
}

# 兼容旧名（外部脚本/文档里引用过 CATEGORY_TOKENS）
CATEGORY_TOKENS = {k: v["primary"] for k, v in CATEGORY_RULES.items()}

# 人用寝具标记：保留独立常量，测试与说明文档都直接引用它
HUMAN_BEDDING = CATEGORY_RULES["pet"]["exclude"][:11]


def resolve_keyword(source: str, keyword: str, keyword_cn: str = "") -> str:
    """把英文品类词转成目标源可用的检索词（中文源用中文词）。"""
    if keyword_cn:
        return keyword_cn
    if SOURCES.get(source, {}).get("lang") == "zh":
        return CN_KEYWORD_ALIASES.get(keyword.strip().lower(), keyword)
    return keyword


def is_book(title: str) -> bool:
    return bool(_ISBN.search(title)) or any(m in title for m in BOOK_MARKERS)


def relevant(title: str, category: str, keyword: str = "") -> bool:
    """品类相关性两层判定：主名词命中 **且** 不含排除词。

    - category 未登记 → 不过滤（保持"未知品类放行"的旧行为）；
    - 主名词：具体实物名（狗床/数据线/usb c…），不接受"充电""架""箱"这类泛化词；
    - 排除词：整机/人用品。若检索词本身就在找这一类（keyword 或其中文别名命中排除词），
      排除层对该条整体失效——否则"搜充电宝"会被自己的过滤器清空。
    """
    rules = CATEGORY_RULES.get(category)
    if not rules:
        return True
    low = title.lower()
    if not any(t.lower() in low for t in rules["primary"]):
        return False
    kw = keyword.strip().lower()
    # 英文检索词要连中文别名一起看（"power bank" ↔ "充电宝"），否则跨语言时排除层会误伤
    kw_all = f"{kw} {CN_KEYWORD_ALIASES.get(kw, '')}".strip()
    hit_excluded = [w for w in rules["exclude"] if w.lower() in low]
    if hit_excluded:  # 标题确实是整机/人用品：只有查询本身在找这类东西才放行
        return any(w.lower() in kw_all for w in hit_excluded)
    return True


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


_NE_INIT_STATE = re.compile(r"__initialState__\s*=\s*")


def _json_object_at(text: str, start: int) -> str | None:
    """从 start 处的 '{' 起按括号配平截取一个完整 JSON 对象（识别字符串/转义）。

    页面里 `window.__initialState__ = {...}` 后面还跟着其它脚本，直接贪婪匹配会
    把后面的 JS 一起吞进来 json.loads 就废了——必须配平。
    """
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _newegg_link_map(html: str) -> tuple[dict[str, str], dict[str, str]]:
    """从结果页 DOM 抽"商品号 → 单品页链接"两张表。

    为什么不能只拼 /p/<ParentItem>：旧编码商品（如 12-356-245）的单品页真实链接是
    /p/N82E16812356245，拼 ParentItem 会 404。这里以页面里真实存在的 <a href> 为准，
    拼字符串只作最后兜底（宁可给搜索页也不给假链接）。
    """
    by_item: dict[str, str] = {}
    by_token: dict[str, str] = {}
    for m in re.finditer(r'data-itemnumber="([^"]+)"', html):
        window = html[m.end():m.end() + 1200]
        hm = re.search(r'href="((?:https?://www\.newegg\.com)?[^"]*?/p/[^"#?]+)"', window)
        if hm:
            url = hm.group(1)
            if url.startswith("/"):
                url = "https://www.newegg.com" + url
            by_item.setdefault(m.group(1), url)
    for m in re.finditer(r'href="((?:https?://www\.newegg\.com)?[^"]*?/p/[^"#?]+)"', html):
        url = m.group(1)
        if url.startswith("/"):
            url = "https://www.newegg.com" + url
        token = url.split("/p/")[-1].strip("/")
        by_token.setdefault(token, url)
    return by_item, by_token


def _newegg_item(cell: dict[str, Any], by_item: dict[str, str] | None = None,
                 by_token: dict[str, str] | None = None) -> dict[str, Any] | None:
    """把一个 ItemCell 转成商品卡；缺标题或缺真实价格则丢弃（不猜价格）。"""
    desc = cell.get("Description") or {}
    title = str(desc.get("Title") or desc.get("LineDescription") or "").strip()
    price = cell.get("FinalPrice")
    if not isinstance(price, (int, float)) or float(price) <= 0:
        price = cell.get("UnitCost")
    if not title or not isinstance(price, (int, float)) or float(price) <= 0:
        return None
    item_no = str(cell.get("Item") or "").strip()
    parent = str(cell.get("ParentItem") or "").strip()
    url = (by_item or {}).get(item_no) or (by_token or {}).get(parent) or ""
    if not url and parent:
        url = f"https://www.newegg.com/p/{parent}"
    return {"title": title[:120], "price": float(price), "source_url": url}


def parse_newegg(html: str, source_url: str) -> list[dict[str, Any]]:
    """解析 Newegg 搜索页（`window.__initialState__` 内联 JSON，服务端渲染）。

    - 主路径：配平截取 `window.__initialState__ = {...}` → json.loads → Products[*].ItemCell
      （Item / FinalPrice / Description.Title / ParentItem 全在同一对象里，块内配对无错配）；
    - 兜底：JSON 被截断时按 `"ItemCell":{"Item":"…"` 切片做块内配对；
    - 单品页 URL：优先取结果页里真实存在的 <a href="…/p/…">（旧编码商品是 /p/N82E…），
      取不到才退到 /p/<ParentItem>，再退到搜索页；
    - 解析不出返回空列表，由 scan() 如实报错（绝不补造）。
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_item, by_token = _newegg_link_map(html)

    for match in _NE_INIT_STATE.finditer(html):
        raw = _json_object_at(html, match.end())
        if not raw:
            continue
        try:
            state = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            continue
        for prod in (state.get("Products") or []):
            cell = prod.get("ItemCell") if isinstance(prod, dict) else None
            if not isinstance(cell, dict):
                continue
            card = _newegg_item(cell, by_item, by_token)
            if not card or card["title"] in seen:
                continue
            seen.add(card["title"])
            items.append(card)
        if items:
            break

    if not items:  # 兜底：JSON 不完整时按 ItemCell 切片配对
        for cell_match in re.finditer(r'"ItemCell"\s*:\s*\{', html):
            window = html[cell_match.end():cell_match.end() + 8000]
            title_m = re.search(r'"Title"\s*:\s*"([^"\\]{8,200})"', window)
            price_m = re.search(r'"FinalPrice"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)', window)
            if not (title_m and price_m):
                continue
            title = title_m.group(1).strip()
            if title in seen or float(price_m.group(1)) <= 0:
                continue
            seen.add(title)
            item_m = re.search(r'"Item"\s*:\s*"([^"\\]+)"', window)
            parent_m = re.search(r'"ParentItem"\s*:\s*"([^"\\]+)"', window)
            url = (by_item.get(item_m.group(1)) if item_m else "") or \
                  (by_token.get(parent_m.group(1)) if parent_m else "") or ""
            if not url and parent_m:
                url = f"https://www.newegg.com/p/{parent_m.group(1)}"
            items.append({"title": title[:120], "price": float(price_m.group(1)),
                          "source_url": url})
            if len(items) >= 16:
                break

    for card in items:
        card["source_url"] = card["source_url"] or source_url
    return items[:12]


PARSERS = {
    "dangdang": parse_dangdang,
    "newegg": parse_newegg,
    "walmart": parse_walmart,
}


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
    parser = PARSERS.get(source)
    if parser is None:
        raise ValueError(f"{source} 未实现解析器——宁可报错也不编造。")
    items = parser(html, result["url"])
    if not items:
        raise ValueError(
            f"{source} 页面可达但未解析出真实商品数据——宁可报错也不编造。")
    return {"items": items[:limit], "source_url": result["url"],
            "channel": result["channel"], "currency": spec["currency"],
            "source_label": spec["label"], "robots": robots_note,
            "keyword_used": keyword_used}
