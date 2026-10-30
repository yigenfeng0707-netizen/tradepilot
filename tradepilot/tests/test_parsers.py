"""解析器回归测试：用离线 HTML 快照锁住解析逻辑。

为什么重要：目标站点改一次 DOM，正则就静默失效——线上表现为"抓不到数据"，
而工具会如实报错，看起来像网络问题。有快照回归才能区分"站点变了"和"代码坏了"。
"""
from __future__ import annotations

import unittest

from app.tools.fetch import _looks_blocked
from app.tools.sources import parse_dangdang, parse_newegg, parse_walmart

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


# Newegg 真实结果页形态（2026-10-30 本机实测抓取 window.__initialState__ 后裁出的片段）：
# 价格/标题/商品号都在服务端内联 JSON 里，单品链接在 <a href="…/p/…"> 上。
# 注意第 2 条是旧编码商品：Item=9SIABU5GEX2388 / ParentItem=12-356-245，
# 但它真实单品页是 /p/N82E16812356245——拼 ParentItem 会 404，必须取 DOM 里的链接。
NEWEGG_HTML = """
<!DOCTYPE html><html><head>
<meta name="robots" content="index,follow">
<title>usb c cable - Newegg.com</title></head><body>
<div class="item-cells-wrap">
  <div class="item-container goods-container" data-itemnumber="9SIACCUKM38151">
    <a href="https://www.newegg.com/anker-usb-c-cable-black/p/181-00KG-000K0" class="goods-img">
      <img src="https://c1.neweggimages.com/productimage/nb300/ACCUS2509090FK82T4E.jpg"></a>
    <a href="https://www.newegg.com/anker-usb-c-cable-black/p/181-00KG-000K0" class="goods-title"
       title="Anker USB C to USB C Cable, Type-C 60W Fast Charging Cable (6FT, 5Pack)">Anker USB C Cable</a>
    <li class="price-current">$<strong>18</strong><sup>.99</sup></li>
  </div>
  <div class="item-container goods-container" data-itemnumber="9SIABU5GEX2388">
    <a href="https://www.newegg.com/p/N82E16812356245" class="goods-img">
      <img src="https://c1.neweggimages.com/productimage/nb300/12-356-245-01.jpg"></a>
    <li class="price-current">$<strong>17</strong><sup>.99</sup></li>
  </div>
</div>
<script defer="">window.__initialState__ = {"NavigationList":[{"Name":"Department","NValue":40000000}],
"TotalCount":45,"SortOption":[{"ElementValue":0,"Name":"Featured Items"}],"Products":[
{"IsCombo":false,"ProductNumber":"9SIACCUKM38151","ItemCell":{"Item":"9SIACCUKM38151",
 "UnitCost":19.99,"FinalPrice":18.99,"Active":"1","IsActivated":true,
 "Description":{"Title":"Anker USB C to USB C Cable, Type-C 60W Fast Charging Cable (6FT, 5Pack) for iPhone 16 Series, iPad mini 6 and More (Black)","LineDescription":"Anker USB C to USB C Cable"},
 "Instock":true,"MapPrice":0,"ParentItem":"181-00KG-000K0","IsBlockSeller":false}},
{"IsCombo":false,"ProductNumber":"9SIABU5GEX2388","ItemCell":{"Item":"9SIABU5GEX2388",
 "UnitCost":17.99,"FinalPrice":17.99,"Active":"1","IsActivated":true,
 "Description":{"Title":"Nippon Labs 20USBCGEN2-6MM-G 6ft USB Type C 3.1 (G2) Male to USB-C Male Cable"},
 "Instock":true,"MapPrice":0,"ParentItem":"12-356-245","IsBlockSeller":false}}]};</script>
</body></html>
"""

# 字段缺失（没有 FinalPrice/UnitCost，也没有标题）：不能猜价格，整条丢弃
NEWEGG_NO_FIELDS_HTML = """
<script>window.__initialState__ = {"Products":[
{"ItemCell":{"Item":"ABC-1","Description":{"Title":"Cable Without Any Real Price Field"}}},
{"ItemCell":{"Item":"ABC-2","FinalPrice":0,"Description":{"Title":"Zero Priced Placeholder Item"}}}]};</script>
"""

# JSON 在传输中被截断：应退到 "ItemCell" 切片配对，而不是整页返回空
NEWEGG_TRUNCATED_HTML = """
<div data-itemnumber="9SIACCUKM38151">
  <a href="https://www.newegg.com/p/181-00KG-000K0">x</a></div>
<script>window.__initialState__ = {"Products":[{"ItemCell":{"Item":"9SIACCUKM38151",
 "FinalPrice":18.99,"Description":{"Title":"Anker USB C to USB C Cable 6ft 60W (Truncated Page)"}
"""


class TestNeweggParser(unittest.TestCase):
    def test_parses_products_from_initial_state(self) -> None:
        items = parse_newegg(NEWEGG_HTML, "https://www.newegg.com/p/pl?d=usb+c+cable")
        self.assertEqual(len(items), 2, "两条真实商品卡都应解析出来")
        self.assertEqual([i["price"] for i in items], [18.99, 17.99])
        self.assertIn("Anker USB C to USB C Cable", items[0]["title"])
        self.assertIn("Nippon Labs", items[1]["title"])

    def test_item_url_is_real_product_page(self) -> None:
        items = parse_newegg(NEWEGG_HTML, "https://www.newegg.com/p/pl?d=usb+c+cable")
        self.assertEqual(items[0]["source_url"],
                         "https://www.newegg.com/anker-usb-c-cable-black/p/181-00KG-000K0")
        # 旧编码商品必须用页面里的真实链接，而不是拼出来的 /p/12-356-245
        self.assertEqual(items[1]["source_url"], "https://www.newegg.com/p/N82E16812356245")
        for item in items:
            self.assertIn("/p/", item["source_url"])
            self.assertNotEqual(item["source_url"], "https://www.newegg.com/p/pl?d=usb+c+cable")

    def test_missing_price_or_title_returns_empty(self) -> None:
        self.assertEqual(parse_newegg(NEWEGG_NO_FIELDS_HTML, "u"), [])

    def test_empty_and_unknown_html_returns_empty(self) -> None:
        self.assertEqual(parse_newegg("", "u"), [])
        self.assertEqual(parse_newegg("<html><body>no state here</body></html>", "u"), [])

    def test_truncated_state_falls_back_to_slice_matching(self) -> None:
        items = parse_newegg(NEWEGG_TRUNCATED_HTML, "https://www.newegg.com/p/pl?d=usb+c+cable")
        self.assertEqual(len(items), 1, "JSON 被截断时应走切片兜底，而不是整页放弃")
        self.assertEqual(items[0]["price"], 18.99)
        self.assertEqual(items[0]["source_url"], "https://www.newegg.com/p/181-00KG-000K0")


class TestBlockedDetection(unittest.TestCase):
    def test_short_page_is_treated_as_blocked(self) -> None:
        self.assertTrue(_looks_blocked(BLOCKED_HTML))

    def test_large_page_with_price_is_not_blocked(self) -> None:
        # 拦截页判定的启发式含"过短即拦截"，真实搜索页体量远大于阈值
        big = DANGDANG_HTML + ("<div>filler</div>" * 400)
        self.assertFalse(_looks_blocked(big))

    def test_meta_robots_and_late_price_is_not_blocked(self) -> None:
        """回归：Newegg 真实搜索页 1.5MB，前 20k 里只有 <meta name="robots">、没有 price。

        旧逻辑用裸 "robot" 当挑战页标记，把这种正常结果页误判成拦截页，
        于是真实数据一个都进不来（scan 直接报"该源在当前网络不可达"）。
        """
        head = '<html><head><meta name="robots" content="index,follow"><title>usb c cable</title></head><body>'
        middle = '<ul class="item-cells-wrap"><li class="price-current">$<strong>18</strong></li></ul>'
        page = head + ("<div>nav menu filler</div>" * 1000) + middle + \
            ("<div>item row filler</div>" * 60000) + \
            '<script>window.__initialState__ = {"Products":[{"ItemCell":{"FinalPrice":18.99}}]};</script></body></html>'
        self.assertGreater(len(page), 1_400_000, "夹具要对齐实测页体量（约 1.5MB）")
        self.assertLess(page[:20000].lower().find("price"), 0, "夹具成立前提：前 20k 不含 price")
        self.assertFalse(_looks_blocked(page))

    def test_challenge_page_without_product_signals_is_blocked(self) -> None:
        page = ('<html><body><h1>Verify you are human</h1>' +
                "<div>please wait while we check your browser</div>" * 200 + "</body></html>")
        self.assertGreater(len(page), 4000, "夹具不是被'过短即拦截'顺带判掉的")
        self.assertTrue(_looks_blocked(page))


if __name__ == "__main__":
    unittest.main()
