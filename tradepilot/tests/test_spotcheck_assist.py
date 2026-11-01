# -*- coding: utf-8 -*-
"""spotcheck_assist 判定函数的回归测试。

守的同样是"账不能虚"：预检只产建议（assistant_check），**绝不产生
human_verified**；判定函数宁可返回 None 让人看，也不给出没有依据的
支持/不支持。任何一条松掉，事实抽检准确率就成了机器自证。
"""
import importlib.util
import json
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spotcheck_assist.py"
_spec = importlib.util.spec_from_file_location("spotcheck_assist", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _row(kind: str, payload: dict, url: str = "https://example.com/x") -> dict:
    return {"provenance_id": 1, "kind": kind, "url": url,
            "fetched_at": "2026-10-31T00:00:00+00:00", "payload": payload}


class TestFx(unittest.TestCase):
    def test_same_rate_supported(self):
        r = mod._check_fx(_row("fx", {"base": "USD", "quote": "CNY", "rate": 6.6529}),
                          json.dumps({"rates": {"CNY": 6.6529}}))
        self.assertTrue(r["suggestion"])

    def test_small_drift_still_supported(self):
        r = mod._check_fx(_row("fx", {"base": "USD", "quote": "CNY", "rate": 6.6529}),
                          json.dumps({"rates": {"CNY": 6.72}}))   # +1.0%
        self.assertTrue(r["suggestion"])

    def test_large_drift_not_supported(self):
        r = mod._check_fx(_row("fx", {"base": "USD", "quote": "CNY", "rate": 6.6529}),
                          json.dumps({"rates": {"CNY": 7.20}}))   # +8%
        self.assertFalse(r["suggestion"])

    def test_unparseable_is_none(self):
        r = mod._check_fx(_row("fx", {"base": "USD", "quote": "CNY", "rate": 6.65}), "<html>拦截页</html>")
        self.assertIsNone(r["suggestion"])


class TestPolicy(unittest.TestCase):
    def test_terms_hit_supported(self):
        page = "<p>Lithium Battery Guide for Shippers: UN38.3 testing required.</p>"
        r = mod._check_policy(_row("policy", {"matched_terms": ["lithium battery", "un38.3"], "excerpt": None}), page)
        self.assertTrue(r["suggestion"])

    def test_all_terms_missed_not_supported(self):
        r = mod._check_policy(_row("policy", {"matched_terms": ["lithium battery"], "excerpt": None}),
                              "<p>Cooking recipes</p>")
        self.assertFalse(r["suggestion"])

    def test_excerpt_missed_is_none_even_if_terms_hit(self):
        page = "<p>lithium battery rules apply</p>"
        r = mod._check_policy(_row("policy", {"matched_terms": ["lithium"], "excerpt": "Some exact clause text"}), page)
        self.assertIsNone(r["suggestion"])

    def test_page_only_without_evidence_is_none(self):
        r = mod._check_policy(_row("policy", {"matched_terms": [], "excerpt": None}), "<p>portal</p>")
        self.assertIsNone(r["suggestion"])

    def test_whitespace_normalized(self):
        page = "<p>lithium&nbsp;   battery  guide</p>"
        r = mod._check_policy(_row("policy", {"matched_terms": ["lithium battery"], "excerpt": None}), page)
        self.assertTrue(r["suggestion"])


class TestItem(unittest.TestCase):
    PAYLOAD = {"title": "LUOM 80Gbps Thunderbolt 5 Cable USB C to USB C 6.6ft",
               "price": 41.48}

    def test_title_and_price_hit(self):
        page = f"<h1>{self.PAYLOAD['title']}</h1><span>$41.48</span>"
        r = mod._check_item(_row("item", self.PAYLOAD), page)
        self.assertTrue(r["suggestion"])

    def test_price_changed_is_none(self):
        page = f"<h1>{self.PAYLOAD['title']}</h1><span>$39.99</span>"
        r = mod._check_item(_row("item", self.PAYLOAD), page)
        self.assertIsNone(r["suggestion"])

    def test_title_gone_not_supported(self):
        r = mod._check_item(_row("item", self.PAYLOAD), "<p>Item not found</p>")
        self.assertFalse(r["suggestion"])

    def test_missing_title_is_none(self):
        r = mod._check_item(_row("item", {"title": "", "price": 41.48}), "<p>anything</p>")
        self.assertIsNone(r["suggestion"])


class TestChallengePage(unittest.TestCase):
    """实测教训：Newegg 重抓返回 200 + "are you a human?"，绝不能判"事实不支持"。"""

    def test_newegg_challenge_detected(self):
        self.assertEqual(mod._looks_challenge("<title>Are you a human?</title><body>Verification</body>"),
                         "are you a human")

    def test_normal_page_not_flagged(self):
        self.assertIsNone(mod._looks_challenge("<title>LUOM Thunderbolt Cable</title><p>$41.48</p>"))

    def test_various_markers(self):
        for page, marker in (("<p>Please complete the captcha</p>", "captcha"),
                             ("<p>Just a moment...</p>", "just a moment"),
                             ("<p>Access Denied</p>", "access denied")):
            self.assertEqual(mod._looks_challenge(page), marker)


class TestRedLine(unittest.TestCase):
    def test_assist_does_not_touch_human_verified(self):
        """红线测试：整个模块不得出现给 human_verified 赋值的语句。"""
        src = _SCRIPT.read_text(encoding="utf-8")
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotIn('"human_verified" =', code)
            self.assertNotIn("'human_verified' =", code)
            self.assertNotRegex(code, r"\bhuman_verified\b\s*=[^=]")


if __name__ == "__main__":
    unittest.main()
