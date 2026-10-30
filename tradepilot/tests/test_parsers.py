"""解析器回归测试：用离线 HTML 快照锁住解析逻辑。

为什么重要：目标站点改一次 DOM，正则就静默失效——线上表现为"抓不到数据"，
而工具会如实报错，看起来像网络问题。有快照回归才能区分"站点变了"和"代码坏了"。
"""
from __future__ import annotations

import unittest

from app.tools.fetch import _looks_blocked
from app.tools.sources import parse_dangdang, parse_walmart

DANGDANG_HTML = """
<html><body><ul class="bigimg">
<li>
  <a href="https://product.dangdang.com/123456.html" title="宠物狗狗四季通用可拆洗记忆棉狗床中大型犬垫">
    <img src="x.jpg">
  </a>
  <p class="price"><span class="search_now_price">&yen;129.00</span></p>
</li>
<li>
  <a href="https://product.dangdang.com/234567.html" title="加厚保暖猫窝冬季宠物窝垫小型犬睡垫">
  </a>
  <p class="price"><span class="price_n">&yen;89.50</span></p>
</li>
<li>
  <a href="https://product.dangdang.com/999.html" title="排序">排序</a>
  <p class="price"><span class="search_now_price">&yen;1.00</span></p>
</li>
</ul></body></html>
"""

BLOCKED_HTML = "<html><body>Please verify you are human</body></html>"


class TestDangdangParser(unittest.TestCase):
    def test_parses_two_price_markers(self) -> None:
        items = parse_dangdang(DANGDANG_HTML, "https://search.dangdang.com/?key=dog")
        self.assertEqual(len(items), 2, "两种价格标记各应解析出一条")
        prices = sorted(i["price"] for i in items)
        self.assertEqual(prices, [89.5, 129.0])
        for item in items:
            self.assertIn("dangdang.com", item["source_url"])
            self.assertGreater(len(item["title"]), 10)

    def test_drops_short_titles_like_sort_controls(self) -> None:
        items = parse_dangdang(DANGDANG_HTML, "https://search.dangdang.com/")
        self.assertNotIn("排序", [i["title"] for i in items])

    def test_returns_empty_on_unknown_layout(self) -> None:
        self.assertEqual(parse_dangdang("<html><body>no items</body></html>", "u"), [])


class TestWalmartParser(unittest.TestCase):
    def test_parses_embedded_json_price(self) -> None:
        html = ('<script>var x=[{"id":"123","name":"Dog Bed Large","price":39.99},'
                '{"productId":"456","title":"Cat Cave","price":25.5}];</script>')
        items = parse_walmart(html, "https://www.walmart.com/search?q=dog")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["price"], 39.99)
        self.assertEqual(items[1]["title"], "Cat Cave")


class TestBlockedDetection(unittest.TestCase):
    def test_short_page_is_treated_as_blocked(self) -> None:
        self.assertTrue(_looks_blocked(BLOCKED_HTML))

    def test_large_page_with_price_is_not_blocked(self) -> None:
        # 拦截页判定的启发式含"过短即拦截"，真实搜索页体量远大于阈值
        big = DANGDANG_HTML + ("<div>filler</div>" * 400)
        self.assertFalse(_looks_blocked(big))


if __name__ == "__main__":
    unittest.main()
