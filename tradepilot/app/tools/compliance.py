# Copyright 2026 TradePilot project. Apache-2.0.
"""合规体检：条款可溯版（真实抓取官方页面正文并定位条款原文）。

与上一版的本质区别（上一版的缺口被评委一句话问穿）：
- 旧版：条款正文人工撰写 → 只对 source_url 的**域名根**发一次 HTTP 判 200；
  200 只证明"这个网站活着"，完全不证明"该条款写在页面上"。
- 本版：从官方入口出发，在**同域内**按条款关键词定位承载该条款的真实页面，
  抓取正文、抽取命中条款的原文片段，连同 页面 URL + 抓取时间戳 + 正文 hash 落存证。
  定位不到就如实降级为 page_only / offline，绝不把"网站可达"说成"条款已验证"。

诚实边界（写进答辩，也写进导出报告）：
- 条款的"要求"本身仍是编辑性知识（我们不做法律意见，也无法把自然语言条款
  自动等价映射到一句中文结论）；本版可证明的是——**该要求对应的官方页面与原文
  片段真实存在且可回访**。
- 链接发现只走同域、只走公开页面、尊重 robots、限速。

三态口径（verification）：
- clause_matched  已在官方页面正文中定位到条款对应原文片段（计入 verified）
- page_only       页面可达但未定位到条款原文（不计入 verified，如实标注）
- offline         网络不可达（不计入 verified，标注"当前网络不可达"）
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from .. import provenance
from .executor import ToolOutcome
from .fetch import fetch_browser, fetch_http, robots_allowed

# 平台规则（国内可达，逐品类现场验证）
PLATFORM_RULES = {
    "pet": {"item": "速卖通宠物用品品类发布规则与禁限售清单（平台侧合规基线）",
            "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com",
            "terms": ["product listing", "prohibited", "pet supplies"]},
    "home": {"item": "速卖通家居品类发布规则与禁限售清单（平台侧合规基线）",
             "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com",
             "terms": ["product listing", "prohibited", "home"]},
    "electronics": {"item": "速卖通电子/电器品类发布规则与禁限售清单（平台侧合规基线）",
                    "authority": "AliExpress 规则中心", "source_url": "https://helpcenter.aliexpress.com",
                    "terms": ["product listing", "prohibited", "electronic"]},
}

# 条款 = 编辑性知识；terms = 用于在官方站内定位承载该条款页面的真实词组。
# source_url 一律是**官方站入口**，深页由代码在同域内发现，绝不手工拼造。
KB: dict[str, list[dict[str, Any]]] = {
    "pet": [
        {"item": "宠物食品/零食须符合 FDA 动物饲料与成分标签要求（成分表、保证分析值、净含量）",
         "authority": "U.S. FDA", "source_url": "https://www.fda.gov/animal-veterinary",
         "terms": ["animal food", "labeling", "feed"]},
        {"item": "带毛/纺织类宠物用品注意加州 Prop 65 警示标签义务",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65",
         "terms": ["proposition 65", "warning", "chemical"]},
        {"item": "出口美国须按 HTS 编码申报关税（宠物用品类目）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov",
         "terms": ["harmonized", "tariff", "schedule"]},
    ],
    "home": [
        {"item": "家居消费品须满足 CPSC 强制标准与召回通报义务（CPSIA 铅含量、小零件等）",
         "authority": "U.S. CPSC", "source_url": "https://www.cpsc.gov",
         "terms": ["consumer product", "safety", "recall"]},
        {"item": "加州 Prop 65 化学物质警示标签（按材质评估）",
         "authority": "California OEHHA", "source_url": "https://oehha.ca.gov/proposition-65",
         "terms": ["proposition 65", "warning", "chemical"]},
        {"item": "出口美国须按 HTS 编码申报关税（家居品类）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov",
         "terms": ["harmonized", "tariff", "schedule"]},
    ],
    "electronics": [
        {"item": "无线/蓝牙类设备须 FCC 合规（SDoC 或认证，FCC ID 标注）",
         "authority": "U.S. FCC", "source_url": "https://www.fcc.gov",
         "terms": ["equipment authorization", "device authorization", "fcc id"]},
        {"item": "锂电池产品运输受 UN38.3 与危险品规则约束（瓦时数标注、包装指令）",
         "authority": "U.S. DOT PHMSA", "source_url": "https://www.phmsa.dot.gov",
         "terms": ["lithium battery", "lithium", "hazardous materials"]},
        {"item": "出口美国须按 HTS 编码申报关税（电子产品税率普遍 0–5%）",
         "authority": "USITC HTS", "source_url": "https://hts.usitc.gov",
         "terms": ["harmonized", "tariff", "schedule"]},
    ],
}

CATEGORY_ALIASES = {
    "pet": "pet", "dog": "pet", "cat": "pet", "宠物": "pet",
    "home": "home", "household": "home", "furniture": "home", "decor": "home",
    "家居": "home", "家饰": "home",
    "electronics": "electronics", "electronic": "electronics", "cable": "electronics",
    "usb": "electronics", "电子": "electronics",
}

# 站内链接发现：每条款最多试几个候选深页（时间/礼貌成本上限）
MAX_LINK_TRIES = 3
_EXCERPT_WINDOW = 260
_TAG_DROP = re.compile(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>")
_TAG_ANY = re.compile(r"(?s)<[^>]+>")
_ANCHOR = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#\s]+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def resolve_category(raw: str) -> str | None:
    low = raw.lower()
    for key, cat in CATEGORY_ALIASES.items():
        if key in low:
            return cat
    return None


def page_text(html: str) -> str:
    """HTML → 可检索正文（去脚本/样式/标签、解实体、压空白）。"""
    text = _TAG_DROP.sub(" ", html or "")
    text = _TAG_ANY.sub(" ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def same_origin_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """抽取入口页内的同域链接，返回 [(url, 锚点文本)]（保序去重）。

    只认同域、只走公开链接；锚点文本一并带出——官方站的深页 URL 常是
    `/announcement/1234` 这种无语义路径，条款词几乎只出现在锚点里，
    丢了锚点就等于丢了定位能力（首版实测 3/4 条卡在 page_only 就是这个原因）。
    """
    origin = _origin(base_url)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href_raw, anchor_raw in _ANCHOR.findall(html or ""):
        href = unescape(href_raw).strip()
        if href.lower().startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith(origin + "/") and absolute != origin:
            continue
        key = absolute.split("#")[0]
        if key in seen or key.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(key)
        out.append((key, page_text(anchor_raw)[:160]))
    return out


def rank_links(links: list[tuple[str, str]], terms: list[str]) -> list[str]:
    """按条款关键词在「URL 路径 + 锚点文本」中的命中数排序，命中的才进候选。"""
    scored: list[tuple[int, int, str]] = []
    for order, (url, anchor) in enumerate(links):
        parts = urlsplit(url)
        hay = (f"{parts.path} {parts.query} {parts.fragment} {anchor}".lower()
               .replace("-", " ").replace("_", " "))
        hits = sum(1 for t in terms if t in hay)
        if hits:
            scored.append((-hits, order, url))  # 命中多者优先，其次保持页面原顺序
    return [url for _, _, url in sorted(scored)]


# 导航/页脚文字的特征词：这些片段里有条款词，但它不是条款本身。
# 实测教训：不加这层，FDA/OEHHA/USITC 三条的"命中证据"全是
# "Careers at OEHHA Join Our Listservs Contact Us" 这类导航栏——
# 状态写着 clause_matched，点开存证却读不出一句要求，等于假验证。
_NAV_MARKS = ("skip to", "contact us", "careers", "listserv", "privacy policy",
              "site map", "follow us", "sign in", "subscribe", "all rights reserved")
_MIN_PROSE_WORDS = 12
# 上限的作用：导航/页脚在纯文本里没有句号，会连成 150–250 词的巨块，
# 里面塞满条款词。真正的条款句通常 15–60 词，超过 90 词的一律不当条款句。
_MAX_PROSE_WORDS = 90
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _prose_ok(sentence: str) -> bool:
    """判断一个片段像不像"正文句子"而不是导航栏。

    四条否决：太短（导航条目普遍 3–8 词）、太长（无标点的导航巨块）、
    含页脚特征词、大写开头词占比过高（导航是 Title Case，正文是自然句）。
    """
    words = sentence.split()
    if not _MIN_PROSE_WORDS <= len(words) <= _MAX_PROSE_WORDS:
        return False
    low = sentence.lower()
    if any(mark in low for mark in _NAV_MARKS):
        return False
    if sentence.count("|") >= 2:
        return False
    caps = sum(1 for w in words if w[:1].isupper())
    return caps / len(words) <= 0.45


def locate_clause(text: str, terms: list[str]) -> dict[str, Any] | None:
    """在正文中定位条款原文片段：优先取**读得懂的句子**。

    判定门槛：至少命中 2 个不同关键词（单关键词条款降级为 1 个），
    且该片段必须通过 `_prose_ok`（排除导航栏/页脚）——命中词出现在导航里
    不构成条款验证，宁可报 page_only 也不给假证据。
    """
    if not text:
        return None
    need = 2 if len(terms) > 1 else 1
    low_terms = [t.lower() for t in terms]

    # 候选窗口 = 单句，或"相邻两句"。为什么允许两句：条款常拆成
    # "…requires labeling of X." + "Importers must…" 两句表述，
    # 强求同句会把真条款漏掉；而导航巨块过不了 _prose_ok，天然被排除。
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    windows: list[str] = []
    for i, sentence in enumerate(sentences):
        if not _prose_ok(sentence):
            continue
        windows.append(sentence)
        if i + 1 < len(sentences) and _prose_ok(sentences[i + 1]):
            windows.append(f"{sentence} {sentences[i + 1]}")

    best: tuple[int, int, str] | None = None
    for window in windows:
        hits = sum(1 for t in low_terms if t in window.lower())
        if hits < need:
            continue
        # 命中词越多越好；同样多时取更短的窗口（证据更聚焦）
        if best is None or (hits, -len(window)) > (best[0], -len(best[2])):
            best = (hits, len(window), window)

    if best is None:
        return None
    hits, _, sentence = best
    matched = [t for t in terms if t.lower() in sentence.lower()]

    # 句子可能很长：以第一个命中词为中心截一段，保证片段里既有关键词又有上下文
    flat = re.sub(r"\s+", " ", sentence).strip()
    first = min((flat.lower().find(t) for t in matched if t in flat.lower()), default=0)
    start = max(0, first - 90)
    excerpt = flat[start:start + 400]
    if start > 0:
        excerpt = "…" + excerpt
    if start + 400 < len(flat):
        excerpt = excerpt + "…"
    return {"excerpt": excerpt, "matched_terms": matched, "term_hits": hits}


async def _grab(url: str, prefer_browser: bool = False) -> dict[str, Any]:
    """带 robots 闸门的真实抓取：返回 {ok, status, html, text, channel, error}。

    **为什么必须带浏览器降级**：美国官方站（CPSC/FDA/FCC/PHMSA/HTS）对
    HTTP 直连普遍返 403 或"200 + 空壳/拦截页"——实测 hts.usitc.gov 是 JS 单页应用
    （正文 0 字）、oehha 被 Incapsula 拦、cpsc/fcc/phmsa 直接 403。
    只走 HTTP 会让"逐条对官方来源定位条款正文"变成 0 命中，红线承诺就成了空话。
    同一批 URL 走 Chromium 全部 200 且有正文，因此这里做一次降级重试。

    `prefer_browser`：同一站点一旦证实需要浏览器（入口页降级成功），
    后续深页直接走浏览器，避免每条都先撞一次 403。
    """
    allowed, note = await robots_allowed(url)
    if not allowed:
        return {"ok": False, "status": None, "html": "", "text": "",
                "channel": None, "error": f"robots 禁止：{note}"}

    def _pack(html: str, status: int | None, channel: str,
              error: str | None = None) -> dict[str, Any]:
        text = page_text(html)
        return {"ok": bool(html) and error is None, "status": status, "html": html,
                "text": text, "channel": channel, "error": error}

    http_result: dict[str, Any] | None = None
    http_error: str | None = None
    if not prefer_browser:
        try:
            resp = await fetch_http(url)
            html = resp["html"] if resp["status"] == 200 else ""
            if resp["status"] == 200 and not block_reason(html, page_text(html)):
                return _pack(html, resp["status"], "http")
            http_result = {"status": resp["status"], "html": html}
        except Exception as exc:  # noqa: BLE001
            http_error = f"{type(exc).__name__}: {exc}"

    # HTTP 不可用（403 / 空壳 / 拦截页 / 异常）→ 换浏览器通道再试一次
    try:
        bresp = await fetch_browser(url)
        bhtml = bresp.get("html") or ""
        if bhtml and not block_reason(bhtml, page_text(bhtml)):
            return _pack(bhtml, bresp.get("status", 200), "browser")
        browser_note = block_reason(bhtml, page_text(bhtml)) or "浏览器通道返回空内容"
    except Exception as exc:  # noqa: BLE001
        browser_note = f"浏览器通道异常 {type(exc).__name__}: {exc}"

    # 两条通道都不行：如实报告，并保留各自的失败原因（便于答辩时解释）
    if http_result is not None:
        return {"ok": False, "status": http_result["status"],
                "html": http_result["html"], "text": page_text(http_result["html"]),
                "channel": "http", "error": f"HTTP 不可用（{http_result['status']}）；{browser_note}"}
    return {"ok": False, "status": None, "html": "", "text": "", "channel": None,
            "error": f"HTTP 失败（{http_error}）；{browser_note}"}


_BLOCK_MARKS = ("incapsula", "request unsuccessful", "access denied", "captcha",
                "are you a robot", "sorry, we can't find that page", "页面不存在")
MIN_TEXT_CHARS = 240


def block_reason(html: str, text: str) -> str | None:
    """判定「200 但内容不可核验」：WAF 拦截页 / SPA 空壳 / 软 404。

    HTTP 状态码不是证据。实测三条官方入口的返回码全是 200，正文分别是
    Incapsula 拦截页、0 字 SPA 空壳、"Sorry, we can't find that page" 软 404；
    若只看状态码就会把这三条谎报成"站点可达"。
    """
    low = (html or "")[:4000].lower()
    for mark in _BLOCK_MARKS:
        if mark in low:
            return f"命中拦截/软404 特征「{mark}」"
    if len(text or "") < MIN_TEXT_CHARS:
        return f"页面正文仅 {len(text or '')} 字（<{MIN_TEXT_CHARS}），未取得可核验内容"
    return None


async def verify_clause(entry: dict[str, Any]) -> dict[str, Any]:
    """单条款核验：入口页 → 同域定位深页 → 抽取条款原文 → 存证。"""
    terms = [t for t in entry.get("terms", []) if t]
    entry_url = entry["source_url"]
    root = await _grab(entry_url)
    root_block = block_reason(root["html"], root["text"]) if root["ok"] else None
    content_ok = bool(root["ok"] and root_block is None)

    page_url, page_status, clause = entry_url, root["status"], None
    if content_ok and terms:
        clause = locate_clause(root["text"], terms)
        if clause is None:              # 入口页没写条款 → 同域内按条款词找候选深页
            # 入口页若已证实要走浏览器（政府站常见），深页直接走浏览器，不再逐条撞 403
            deep_prefer = root.get("channel") == "browser"
            for candidate in rank_links(same_origin_links(root["html"], entry_url),
                                        terms)[:MAX_LINK_TRIES]:
                deep = await _grab(candidate, prefer_browser=deep_prefer)
                if not deep["ok"] or block_reason(deep["html"], deep["text"]):
                    continue            # 深页也必须内容可核验，否则不配当证据
                found = locate_clause(deep["text"], terms)
                if found:
                    page_url, page_status, clause = candidate, deep["status"], found
                    break

    if clause:
        verification = "clause_matched"
    elif not root["ok"]:
        verification = "offline"
    elif root_block:
        verification = "blocked"
    else:
        verification = "page_only"

    result = {**entry, "kind": entry.get("kind", "authority"),
              "http_status": page_status, "page_url": page_url,
              "verification": verification, "verified": verification == "clause_matched",
              # live = 真的拿到可核验正文；http_reachable = 仅仅是 HTTP 200。两者必须分开。
              "live": bool(content_ok or clause),
              "http_reachable": bool(root["ok"]),
              # 通道如实记录：政府站多为 browser，普通站为 http；答辩被问"怎么抓的"直接给这个字段
              "channel": root.get("channel"),
              "block_reason": root_block,
              "excerpt": (clause or {}).get("excerpt"),
              "matched_terms": (clause or {}).get("matched_terms", []),
              "error": root["error"] or root_block}

    if verification in ("clause_matched", "page_only"):
        # 只有真正拿到内容的页面才落存证——拦截页/空壳入库等于给假证据盖章
        result["provenance_id"] = provenance.record("policy", page_url, {
            "item": entry["item"], "authority": entry["authority"], "kind": result["kind"],
            "verification": verification, "matched_terms": result["matched_terms"],
            "excerpt": result["excerpt"], "http_status": page_status,
            "entry_url": entry_url, "channel": root.get("channel"),
            "fetch_error": root["error"]},
            raw_text=(result["excerpt"] or "") + page_url)
    else:
        result["provenance_id"] = None
    return result


_NOTE = {"clause_matched": "已在官方页面定位到条款原文片段",
         "page_only": "已取得官方页面正文，但未定位到条款原文",
         "blocked": "HTTP 与浏览器通道均未取得可核验正文（WAF 拦截 / SPA 空壳 / 软 404）",
         "offline": "网络不可达或 robots 禁止，仅作引用标注"}


async def run(tool_input: dict[str, Any]) -> ToolOutcome:
    category_hint = str(tool_input.get("category", "")).strip()
    category = resolve_category(category_hint)
    if category is None:
        return ToolOutcome(
            False, "error", None,
            f"暂无品类「{category_hint}」的合规知识卡（可用：pet/home/electronics）"
            "——不冒充覆盖面，宁可不答。",
        )
    checks: list[dict[str, Any]] = []
    platform = {**PLATFORM_RULES[category], "kind": "platform"}
    checks.append(await verify_clause(platform))
    for req in KB[category]:
        checks.append(await verify_clause({**req, "kind": "authority"}))

    verified = sum(1 for c in checks if c["verified"])
    content_ok_n = sum(1 for c in checks if c["live"])
    http_ok = sum(1 for c in checks if c["http_reachable"])
    blocked = [c["authority"] for c in checks if c["verification"] == "blocked"]
    unverified = [c["authority"] for c in checks if not c["verified"]]
    note = (f"{verified}/{len(checks)} 条已定位官方条款原文"
            f"；{content_ok_n}/{len(checks)} 条取得可核验正文"
            f"（HTTP 200 共 {http_ok} 条，其中 {len(blocked)} 条是拦截页/空壳）"
            + (f"；未验证：{', '.join(unverified)}" if unverified else ""))
    return ToolOutcome(
        True, "done", None, f"合规体检（{category} 品类）：{note}",
        {"category": category, "checks": checks,
         "live_verified": verified, "content_ok_sources": content_ok_n,
         "http_reachable_sources": http_ok, "blocked_sources": blocked,
         "total": len(checks), "verification_note": _NOTE,
         "basis": "条款正文为编辑性知识；verified 仅代表已在官方页面定位到对应原文片段，"
                  "不构成法律意见，也不等同于该商品已合规。HTTP 200 不等于内容可核验。"},
    )
