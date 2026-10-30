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

    def test_generic_words_no_longer_qualify(self) -> None:
        """泛化词（箱/架/柜/充电/电源）不再单独构成相关：必须命中主名词。"""
        self.assertTrue(relevant("铝合金 置物架 三层 免打孔", "home"), "置物架=主名词，应保留")
        self.assertFalse(relevant("车载后备箱整理箱 大号 塑料", "home"),
                         "只有泛化词\"箱\"，没有收纳箱/储物箱这类主名词")
        self.assertFalse(relevant("飞科 电动剃须刀 USB 充电线 套装", "electronics"),
                         "套装里的\"充电线\"不能把整机剃须刀带进线缆样本")

    def test_real_cable_title_is_kept(self) -> None:
        # 真实抓取到的当当标题（assets/audit_sample.json provenance 165）
        self.assertTrue(relevant(
            "Type-C插头安卓手机数据线 平板电脑尼龙编织USB充电线 1米 通用华为小米三星oppo vivo",
            "electronics", "usb c cable"))

    def test_shaver_never_enters_usb_c_cable_sample(self) -> None:
        """真实回归：搜 `usb c cable` 时当当返回飞科剃须刀 ¥180，把价格带上限抬高一个量级。

        标题原文来自 assets/audit_sample.json（provenance 177，真实抓取记录）：
        它同时命中主名词 "usb c"（Typc-C）与排除词 "剃须刀"——两层判定必须判为不相关。
        """
        shaver = ("飞科(FLYCO) 男士电动剃须刀全身水洗干湿双剃刮胡刀便携式双刀头"
                  "胡须刀USB Typc-C充电FS889")
        self.assertFalse(relevant(shaver, "electronics", "usb c cable"))
        self.assertFalse(relevant(shaver, "electronics"))  # 不传 keyword 也必须挡住

    def test_power_bank_is_not_a_cable(self) -> None:
        self.assertFalse(relevant("罗马仕 充电宝 20000毫安 Type-C 双向快充 移动电源",
                                  "electronics", "usb c cable"))
        # 但用户就是在搜充电宝时，该排除词必须失效，否则自己把自己过滤空
        self.assertTrue(relevant("罗马仕 充电宝 20000毫安 Type-C 双向快充 移动电源",
                                 "electronics", "power bank"))

    def test_cable_mentioning_device_in_specs_is_kept(self) -> None:
        """反向误杀回归：线缆标题里写"支持 4K 显示器/笔记本"不能被当成整机过滤掉。

        标题为 Newegg 实测抓到的真实商品（$10.99）。
        """
        title = ("Hannord USB C to USB C Cable 3.2 Gen 2, 240W USBC Cable PD Fast Charging "
                 "Cable, SuperSpeed 20 Gbps 90 Degree 4K Monitor")
        self.assertTrue(relevant(title, "electronics", "usb c cable"))

    def test_keyword_argument_is_backward_compatible(self) -> None:
        """新签名带 keyword；两参调用等价于 keyword=""，老调用点无需改动。"""
        cases = [
            ("狗床小型犬狗窝四季通用", "pet"),
            ("线条小狗床垫 宿舍寝室", "pet"),
            ("Type-C 数据线 快充", "electronics"),
            ("任意标题", ""),
        ]
        for title, category in cases:
            self.assertEqual(relevant(title, category), relevant(title, category, ""), title)


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
