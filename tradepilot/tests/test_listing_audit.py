"""文案审计规则测试：每条规则都要能命中、能给出修复建议。

审计是"生成之后"的唯一确定性防线，规则错杀或漏杀都会直接影响作品可信度，
所以用正反例成对锁住。
"""
from __future__ import annotations

import asyncio
import unittest

from app.tools import listing_audit

GOOD = {
    "title": "PawNest Memory Foam Dog Bed, Washable Cover, Medium 30 x 20 in, Grey",
    "bullets": [
        "SUPPORTIVE MEMORY FOAM CORE: 3-inch high-density foam cushions joints for "
        "senior dogs and large breeds, tested to 60 lb static load.",
        "WASHABLE ZIP COVER: Removable microsuede cover machine-washes cold and "
        "air-dries, keeping the sleeping area clean between baths.",
        "NON-SLIP BASE: Textured oxford bottom grips hardwood and tile floors so the "
        "bed stays put when your dog steps in and out.",
        "EASY TO CARRY: Integrated side handle and 4.2 lb weight make it simple to move "
        "between rooms or load into a car for travel.",
        "SIZING GUIDE: Medium measures 30 x 20 x 3 in for dogs up to 45 lb; measure "
        "your pet while sleeping before ordering.",
    ],
    "description": "A medium memory foam dog bed with a washable cover and non-slip base.",
}

BAD = {
    "title": "BEST SELLER #1 Dog Bed FDA Approved 狗狗床 eco-friendly hypoallergenic!!!",
    "bullets": ["cure anxiety", "short", "x", "y", "z"],
    "description": "",
}


def audit(copy: dict, **kw) -> dict:
    return listing_audit.audit(copy, keyword=kw.pop("keyword", "dog bed"), **kw)


class TestCleanListing(unittest.TestCase):
    def test_good_copy_passes(self) -> None:
        r = audit(GOOD, marketplace="US", category="pet")
        self.assertGreaterEqual(r["score"], 70, r["issues"])
        self.assertEqual(r["counts"]["critical"], 0, r["issues"])
        self.assertTrue(r["passed"])

    def test_keyword_in_title_head(self) -> None:
        r = audit(GOOD, marketplace="US")
        rules = [i["rule"] for i in r["issues"]]
        self.assertNotIn("核心词未进标题前 80 字符", rules)


class TestHighRiskClaims(unittest.TestCase):
    def setUp(self) -> None:
        self.r = audit(BAD, marketplace="US", category="pet")
        self.rules = {i["rule"] for i in self.r["issues"]}

    def test_detects_ranking_and_platform_and_medical_claims(self) -> None:
        self.assertIn("绝对化排名宣称", self.rules)
        self.assertIn("官方背书暗示", self.rules)
        self.assertIn("医疗疗效宣称", self.rules)

    def test_detects_green_claims_without_certification(self) -> None:
        self.assertIn("无认证环保宣称", self.rules)

    def test_critical_blocks_publish(self) -> None:
        self.assertFalse(self.r["passed"])
        self.assertEqual(self.r["verdict"], "不可上架：存在必改项")
        self.assertEqual(self.r["grade"], "D")

    def test_every_issue_has_actionable_fix(self) -> None:
        for issue in self.r["issues"]:
            self.assertTrue(issue["fix"].strip(), issue)

    def test_competitor_brand_is_critical(self) -> None:
        r = audit(GOOD, marketplace="US", brand_blocklist=["PawNest"])
        self.assertIn("疑似侵权品牌词", {i["rule"] for i in r["issues"]})


class TestFieldLimits(unittest.TestCase):
    def test_jp_title_limit_is_100(self) -> None:
        long_title = ("PawNest Memory Foam Dog Bed with Washable Removable Cover, "
                      "Non-Slip Base, Medium Size 30 x 20 x 3 inch, Grey")
        self.assertGreater(len(long_title), 100)
        r = audit(dict(GOOD, title=long_title), marketplace="JP")
        self.assertEqual(r["limits"]["title"], 100)
        self.assertIn("标题超限", {i["rule"] for i in r["issues"]})

    def test_bullet_count_mismatch_is_warning(self) -> None:
        copy = dict(GOOD, bullets=GOOD["bullets"][:3])
        r = audit(copy, marketplace="US")
        self.assertIn("五点数量不符", {i["rule"] for i in r["issues"]})

    def test_search_terms_byte_limit(self) -> None:
        r = audit(GOOD, marketplace="US", search_terms="word " * 60)
        self.assertIn("Search Terms 超字节", {i["rule"] for i in r["issues"]})

    def test_missing_search_terms_is_info(self) -> None:
        r = audit(GOOD, marketplace="US")
        info = [i for i in r["issues"] if i["rule"] == "缺后台 Search Terms"]
        self.assertTrue(info)
        self.assertEqual(info[0]["level"], "info")


class TestLocalization(unittest.TestCase):
    def test_chinese_residue_blocks_english_site(self) -> None:
        r = audit(BAD, marketplace="US")
        self.assertIn("目标站语言残留", {i["rule"] for i in r["issues"]})

    def test_metric_units_flagged_for_us(self) -> None:
        copy = dict(GOOD, title="PawNest Dog Bed 76 cm Wide, Grey")
        r = audit(copy, marketplace="US")
        self.assertIn("度量单位未本地化", {i["rule"] for i in r["issues"]})


class TestPlaceholders(unittest.TestCase):
    def test_unfilled_placeholder_blocks_publish(self) -> None:
        copy = dict(GOOD, title="PawNest Dog Bed — [ADD: core attribute]")
        r = audit(copy, marketplace="US")
        self.assertIn("占位符未填", {i["rule"] for i in r["issues"]})
        self.assertFalse(r["passed"])


class TestToolWrapper(unittest.TestCase):
    def test_missing_copy_is_error(self) -> None:
        out = asyncio.run(listing_audit.run({"copy": {}}))
        self.assertEqual(out.status, "error")

    def test_summary_reports_score_and_counts(self) -> None:
        out = asyncio.run(listing_audit.run({"copy": GOOD, "keyword": "dog bed",
                                             "marketplace": "US"}))
        self.assertEqual(out.status, "done")
        self.assertIn("分", out.summary)
        self.assertIn("score", out.data)


if __name__ == "__main__":
    unittest.main()
