# -*- coding: utf-8 -*-
"""文案生成侧的语言与结构约束。

这两条是实测逼出来的：不传目标站语言时，模型会给美国站写整段中文五点
（标题却是英文），审计判 critical；不限制条目数时会给 6 条卖点，
平台硬上限是 5 条。生成侧能确定性修的，就不要留给审计去扣分。
"""
import unittest

from app.tools import listing

CN_COPY = {
    "title": "Dog Bed, Washable Pet Bed",
    "bullets": ["品类定位：切入美国站主流价格带。", "四季通用设计：适配全年使用场景。"],
    "description": "可拆洗结构，便于日常清洁。",
}
EN_COPY = {
    "title": "Dog Bed, Washable Pet Bed for Small and Medium Dogs",
    "bullets": ["Washable cover for easy cleaning.", "Elevated design keeps the bed off cold floors."],
    "description": "A category-level draft for US buyers.",
}


class TestLanguageGuard(unittest.TestCase):
    def test_chinese_copy_is_rejected_for_us(self) -> None:
        issue = listing.copy_language_issue(CN_COPY, "US")
        self.assertIsNotNone(issue)
        self.assertIn("中文", issue)

    def test_english_copy_passes(self) -> None:
        self.assertIsNone(listing.copy_language_issue(EN_COPY, "US"))

    def test_cjk_anywhere_is_caught(self) -> None:
        """只要有一处中文就算不合格——标题英文、五点中文正是实测踩到的形态。"""
        copy = dict(EN_COPY)
        copy["bullets"] = EN_COPY["bullets"] + ["合规基线：遵循平台规则。"]
        self.assertIsNotNone(listing.copy_language_issue(copy, "UK"))

    def test_other_marketplaces_are_not_forced_to_english(self) -> None:
        # 德语/日语站的字符集不同，这条规则只对英文站生效
        self.assertIsNone(listing.copy_language_issue(CN_COPY, "DE"))
        self.assertIsNone(listing.copy_language_issue(CN_COPY, "JP"))


class TestNormalizeCopy(unittest.TestCase):
    def test_bullets_are_capped_at_five(self) -> None:
        copy = {"title": "t", "bullets": [f"b{i}" for i in range(6)], "description": ""}
        self.assertEqual(len(listing.normalize_copy(copy)["bullets"]), 5)

    def test_empty_bullets_are_dropped(self) -> None:
        copy = {"title": "t", "bullets": ["a", "  ", "", "b"], "description": ""}
        self.assertEqual(listing.normalize_copy(copy)["bullets"], ["a", "b"])

    def test_original_copy_is_not_mutated(self) -> None:
        copy = {"title": "t", "bullets": ["a", "b"], "description": ""}
        listing.normalize_copy(copy)
        self.assertEqual(copy["bullets"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
