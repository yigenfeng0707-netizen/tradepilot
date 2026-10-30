"""搜索结果清洗：价格带不能被书籍、跨品类、重复条目污染。

真实案例：在当当搜 "dog bed" 返回绘本《The Dog on the Bed》（¥259）；
搜 "狗窝" 返回《童年的狗窝》（¥14.4，三条重复）——都会让定价沙盘的对照结论失真。
清洗是剔除非目标品类，不是编造：剔除条数如实上报，原始来源页可复核。
"""
from __future__ import annotations

import unittest

from app.tools.competitor import _dedupe
from app.tools.sources import is_book, relevant, resolve_keyword


class TestBookFilter(unittest.TestCase):
    def test_books_are_filtered(self) -> None:
        for title in [
            "The Dog on the Bed: A Canine Alphabet [ISBN:9781554551]",
            "童年的狗窝 梅子涵 9787530764862 新蕾出版社",
            "狗窝/少年中国人文阅读书系 东西 著 辽宁师范大学出版社",
            "神探狗狗的冒险 10 英文原版 课外阅读章节书",
        ]:
            self.assertTrue(is_book(title), title)

    def test_real_products_are_kept(self) -> None:
        for title in [
            "狗窝可拆洗四季通用狗狗床超级大猫窝大型中型犬夏天宠物床垫",
            "狗床小型犬狗窝四季通用可拆洗中型犬宠物床离地小狗睡觉专用",
        ]:
            self.assertFalse(is_book(title), title)


class TestCategoryRelevance(unittest.TestCase):
    def test_pet_tokens(self) -> None:
        self.assertTrue(relevant("狗床小型犬狗窝四季通用", "pet"))

    def test_unknown_category_keeps_everything(self) -> None:
        self.assertTrue(relevant("任意标题", ""))

    def test_cross_category_is_dropped(self) -> None:
        self.assertFalse(relevant("Type-C 数据线 快充", "pet"))

    def test_human_bedding_with_pet_print_is_dropped(self) -> None:
        self.assertFalse(relevant("线条小狗床垫托浮支撑释压床垫垫子租房卷包记忆棉宿舍寝室", "pet"))
        self.assertTrue(relevant("狗床小型犬狗窝四季通用可拆洗", "pet"))


class TestDedupe(unittest.TestCase):
    def test_same_title_and_price_collapsed(self) -> None:
        items = [
            {"title": "童年的狗窝 梅子涵", "price": 14.4},
            {"title": "童年的狗窝 梅子涵", "price": 14.4},
            {"title": "童年的狗窝 梅子涵", "price": 14.6},
        ]
        self.assertEqual(len(_dedupe(items)), 2)


class TestKeywordLocalization(unittest.TestCase):
    def test_english_keyword_mapped_for_chinese_source(self) -> None:
        self.assertEqual(resolve_keyword("dangdang", "dog bed"), "狗床")

    def test_explicit_keyword_cn_wins(self) -> None:
        self.assertEqual(resolve_keyword("dangdang", "dog bed", "宠物床"), "宠物床")

    def test_english_source_keeps_keyword(self) -> None:
        self.assertEqual(resolve_keyword("walmart", "dog bed"), "dog bed")


if __name__ == "__main__":
    unittest.main()
