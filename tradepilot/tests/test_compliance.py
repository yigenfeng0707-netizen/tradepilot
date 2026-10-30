"""合规体检（条款可溯版）的离线单测。

覆盖真实抓取逻辑里最容易出错、也最容易被评委追问的三处：
正文归一化、同域链接发现、条款原文定位的**判定门槛**。
门槛（单个关键词命中不算已验证）是本模块的诚实底线，必须有机器可复算的用例。
网络路径通过打桩 compliance._grab 覆盖，单测不联网。
"""
from __future__ import annotations  # noqa: F401

import asyncio
import unittest
from unittest import mock

from app.tools import compliance as C

ENTRY = "https://www.fda.gov/animal-veterinary"
DEEP = "https://www.fda.gov/animal-feed-labeling"

ENTRY_HTML = (
    "<html><head><title>FDA</title><script>var animal food = 1;</script>"
    "<style>.safety{color:red}</style></head><body>"
    '<a href="/animal-feed-labeling">feed labeling</a>'
    '<a href="https://other.example.com/prohibited">external</a>'
    '<a href="mailto:a@b.c">mail</a><a href="#top">frag</a>'
    '<a href="https://www.fda.gov/animal-veterinary">self</a>'
    "<p>&amp; 标签 requirements for pet food</p></body></html>"
)

CLAUSE_TEXT = (
    "The Animal Food and Feed Program requires labeling of the product name, "
    "net quantity, and guaranteed analysis for all animal feed sold in the "
    "United States. Importers must comply with the Federal Food, Drug, and "
    "Cosmetic Act. Unrelated cooking tips appear here."
)

SINGLE_TERM_TEXT = "This page mentions labeling only once and nothing else relevant."

# 正文足够长但没有条款内容 → 才是真正的 page_only
LONG_NOISE_TEXT = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed tempor incididunt "
    "ut labore et dolore magna enim ad minim veniam quis nostrud exercitation "
    "ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure dolor "
    "nulla paratur foras in quid emm leniste vercincto videli possunt undia."
)
# 三条官方入口的实测形态：WAF 拦截页 / SPA 空壳 / 软 404，状态码全是 200
WAF_HTML = ("<html><head><title>Attention</title></head><body><h1>Request unsuccessful</h1>"
            "<p>Incapsula incident ID: 673000740074180103-165419441383871105</p></body></html>")
SOFT_404_HTML = ("<html><body><h1>Sorry, we can't find that page</h1>"
                 "<p>But we still have lots for you to discover ~ BACK TO HOMEPAGE</p></body></html>")
SPA_HTML = "<html><head><title>HTS</title></head><body><div id=\"root\"></div></body></html>"

TERMS = ["animal feed", "labeling", "guaranteed analysis"]


def fake_grab(pages: dict[str, dict]):
    """按 URL 查表的 _grab 替身：未登记的 URL 一律视为不可达。"""
    async def _grab(url: str) -> dict:
        return pages.get(url) or {"ok": False, "status": None, "html": "",
                                  "text": "", "error": "not registered"}
    return _grab


def page(html: str, text: str) -> dict:
    return {"ok": True, "status": 200, "html": html, "text": text, "error": None}


def urls(link_pairs: list[tuple[str, str]]) -> list[str]:
    return [url for url, _ in link_pairs]


class TestPageText(unittest.TestCase):
    def test_strips_script_style_and_tags(self) -> None:
        text = C.page_text(ENTRY_HTML)
        self.assertNotIn("var animal food", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("<a href", text)

    def test_unescapes_entities_and_collapses_whitespace(self) -> None:
        self.assertEqual(C.page_text("<p>a &amp; b&nbsp;&nbsp;&nbsp;c</p>"), "a & b c")

    def test_empty_and_none_are_safe(self) -> None:
        self.assertEqual(C.page_text(""), "")
        self.assertEqual(C.page_text(None), "")


class TestLinkDiscovery(unittest.TestCase):
    def test_keeps_only_same_origin_absolute_links(self) -> None:
        links = C.same_origin_links(ENTRY_HTML, ENTRY)
        self.assertIn(DEEP, urls(links))
        joined = " ".join(urls(links))
        self.assertNotIn("other.example.com", joined)
        self.assertNotIn("mailto:", joined)

    def test_carries_anchor_text_for_clause_scoring(self) -> None:
        got = dict(C.same_origin_links(ENTRY_HTML, ENTRY))
        self.assertEqual(got[DEEP], "feed labeling")

    def test_drops_fragment_and_the_entry_itself(self) -> None:
        found = urls(C.same_origin_links(ENTRY_HTML, ENTRY))
        self.assertNotIn(ENTRY, found)
        self.assertNotIn(ENTRY + "#top", found)

    def test_rank_orders_by_term_hits_and_drops_zero(self) -> None:
        ranked = C.rank_links(
            [("https://x.gov/a/feed", ""), ("https://x.gov/nonsense", ""),
             ("https://x.gov/animal-feed-labeling", "")],
            ["feed", "labeling"])
        self.assertEqual(ranked, ["https://x.gov/animal-feed-labeling", "https://x.gov/a/feed"])

    def test_anchor_text_rescues_semanticless_paths(self) -> None:
        """官方站常见 /announcement/1234 式路径：条款词只在锚点里，必须仍能排上。"""
        ranked = C.rank_links(
            [("https://oehha.ca.gov/announcement/1234", "Proposition 65 Warning Requirements"),
             ("https://oehha.ca.gov/news/5678", "conference recap")],
            ["proposition 65", "warning"])
        self.assertEqual(ranked, ["https://oehha.ca.gov/announcement/1234"])


class TestLocateClause(unittest.TestCase):
    def test_two_distinct_terms_are_a_hit(self) -> None:
        found = C.locate_clause(CLAUSE_TEXT, TERMS)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(len(found["matched_terms"]), 2)
        self.assertIn("guaranteed analysis", found["excerpt"].lower())

    def test_single_term_match_is_not_verification(self) -> None:
        """诚实底线：页面里碰巧出现一个词，不得当成条款已验证。"""
        self.assertIsNone(C.locate_clause(SINGLE_TERM_TEXT, TERMS))

    def test_single_term_clause_accepts_one_hit(self) -> None:
        found = C.locate_clause(SINGLE_TERM_TEXT, ["labeling"])
        self.assertIsNotNone(found)

    def test_no_terms_never_matches(self) -> None:
        self.assertIsNone(C.locate_clause(CLAUSE_TEXT, []))
        self.assertIsNone(C.locate_clause("", TERMS))


class TestVerifyClause(unittest.TestCase):
    """三态口径（clause_matched / page_only / offline）与存证落库条件。"""

    def _verify(self, pages: dict[str, dict], entry: dict | None = None) -> dict:
        target = entry or {"item": "宠物食品标签要求", "authority": "U.S. FDA",
                           "source_url": ENTRY, "terms": TERMS, "kind": "authority"}
        with mock.patch.object(C, "_grab", fake_grab(pages)):
            return asyncio.run(C.verify_clause(target))

    def test_clause_on_entry_page_is_verified(self) -> None:
        out = self._verify({ENTRY: page(ENTRY_HTML, CLAUSE_TEXT)})
        self.assertEqual(out["verification"], "clause_matched")
        self.assertTrue(out["verified"])
        self.assertEqual(out["page_url"], ENTRY)
        self.assertIsNotNone(out["provenance_id"])
        self.assertIn("labeling", " ".join(out["matched_terms"]))

    def test_clause_located_on_same_origin_deep_page(self) -> None:
        out = self._verify({ENTRY: page(ENTRY_HTML, LONG_NOISE_TEXT),
                            DEEP: page("<html><body>deep page</body></html>", CLAUSE_TEXT)})
        self.assertEqual(out["verification"], "clause_matched")
        self.assertEqual(out["page_url"], DEEP)

    def test_substantive_page_without_clause_is_page_only(self) -> None:
        """拿到真正文、但条款不在上面：page_only，仍落存证（页面确实抓到了）。"""
        out = self._verify({ENTRY: page("<p>" + LONG_NOISE_TEXT + "</p>", LONG_NOISE_TEXT)})
        self.assertEqual(out["verification"], "page_only")
        self.assertFalse(out["verified"])
        self.assertTrue(out["live"])
        self.assertIsNone(out["excerpt"])
        self.assertIsNotNone(out["provenance_id"])

    def test_waf_block_page_is_not_reported_as_reachable(self) -> None:
        """关键用例：HTTP 200 + Incapsula 拦截页 ≠ 站点可达（改造前就被这样谎报过）。"""
        out = self._verify({ENTRY: page(WAF_HTML, C.page_text(WAF_HTML))})
        self.assertEqual(out["verification"], "blocked")
        self.assertTrue(out["http_reachable"])   # HTTP 层面确实 200
        self.assertFalse(out["live"])            # 但内容不可核验
        self.assertFalse(out["verified"])
        self.assertIsNone(out["provenance_id"])  # 拦截页不配进存证
        self.assertIn("incapsula", out["block_reason"])

    def test_soft_404_and_empty_spa_are_blocked(self) -> None:
        for html in (SOFT_404_HTML, SPA_HTML):
            out = self._verify({ENTRY: page(html, C.page_text(html))})
            self.assertEqual(out["verification"], "blocked", html[:40])
            self.assertIsNone(out["provenance_id"])

    def test_offline_is_not_verified_and_not_recorded(self) -> None:
        out = self._verify({})
        self.assertEqual(out["verification"], "offline")
        self.assertFalse(out["verified"])
        self.assertFalse(out["live"])
        self.assertFalse(out["http_reachable"])
        self.assertIsNone(out["provenance_id"])

    def test_blocked_deep_page_cannot_produce_a_clause_match(self) -> None:
        """深页必须是可核验内容；WAF 深页即使带关键词也不得算已验证。"""
        out = self._verify({ENTRY: page(ENTRY_HTML, LONG_NOISE_TEXT),
                            DEEP: page(WAF_HTML, C.page_text(WAF_HTML))})
        self.assertEqual(out["verification"], "page_only")
        self.assertEqual(out["page_url"], ENTRY)


def stub_checks(verified_flags: list[bool], live_flags: list[bool]):
    """按顺序回放 (verified, live) 的 verify_clause 替身，隔离全部网络路径。"""
    seq = iter(zip(verified_flags, live_flags))

    async def fake_verify(entry: dict) -> dict:
        verified, live = next(seq)
        state = ("clause_matched" if verified
                 else "page_only" if live else "blocked")
        return {**entry, "kind": entry.get("kind", "authority"),
                "verified": verified, "live": live, "verification": state,
                "http_reachable": True, "block_reason": None if live else "命中拦截特征",
                "http_status": 200, "page_url": entry["source_url"],
                "excerpt": None, "matched_terms": [], "provenance_id": None}

    return fake_verify


class TestComplianceTool(unittest.TestCase):
    def test_category_aliases_resolve(self) -> None:
        for raw in ("宠物", "dog", "Pet supplies", "家居", "usb cable", "电子"):
            self.assertIsNotNone(C.resolve_category(raw), raw)
        self.assertIsNone(C.resolve_category("汽车轮胎"))

    def test_unknown_category_refuses_instead_of_guessing(self) -> None:
        out = asyncio.run(C.run({"category": "汽车轮胎"}))
        self.assertFalse(out.ok)
        self.assertEqual(out.status, "error")
        self.assertIn("宁可不答", out.summary)

    def test_only_clause_matched_counts_as_verified(self) -> None:
        """站点可达数与条款已验证数必须分开计——这是本次改造的核心口径。"""
        with mock.patch.object(C, "verify_clause",
                               stub_checks([True, True, False, False],
                                           [True, True, True, False])):
            out = asyncio.run(C.run({"category": "pet"}))
        self.assertTrue(out.ok)
        self.assertEqual(out.data["total"], 4)                  # 1 平台规则 + 3 官方条款
        self.assertEqual(out.data["live_verified"], 2)          # 仅定位到原文的计入
        self.assertEqual(out.data["content_ok_sources"], 3)     # 拿到可核验正文的
        self.assertEqual(out.data["http_reachable_sources"], 4)  # 而 HTTP 200 是 4 条
        self.assertEqual(len(out.data["blocked_sources"]), 1)
        self.assertIn("2/4", out.summary)
        self.assertIn("不构成法律意见", out.data["basis"])

    def test_knowledge_cards_all_carry_terms_and_official_https(self) -> None:
        """新加条款若漏了 terms，会永远停在 page_only——用契约测试挡住。"""
        entries = [e for group in C.KB.values() for e in group] + list(C.PLATFORM_RULES.values())
        self.assertEqual(len(entries), 12)
        for entry in entries:
            self.assertTrue(entry.get("terms"), entry["item"])
            self.assertTrue(entry["source_url"].startswith("https://"), entry["item"])
            self.assertTrue(entry.get("authority") and entry.get("item"), entry["item"])
