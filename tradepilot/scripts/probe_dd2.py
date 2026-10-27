# -*- coding: utf-8 -*-
"""当当浏览器渲染后 DOM 结构诊断。"""
import re
import sys

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                              viewport={"width": 1440, "height": 900},
                              timezone_id="Asia/Shanghai")
    page = ctx.new_page()
    page.goto("https://search.dangdang.com/?key=dog+bed&act=input",
              timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    html = page.content()
    browser.close()

print("len:", len(html))
# 找一个包含价格的商品块
i = html.find("search_now_price")
j = html.find("price_n")
print("search_now_price at", i, "| price_n at", j)
start = min(x for x in (i, j) if x > 0)
print("=== 价格元素上下文:")
print(html[start - 200:start + 300].replace("\n", " ")[:500])
print()
# 商品 li 块
m = re.search(r'<li[^>]*dd_name="itemlist[^"]*"[^>]*>', html) or re.search(r'<ul[^>]*class="bigimg"[^>]*>', html)
print("li/ul 锚点:", m.group(0) if m else "未找到")
# 标题形态
for pat in [r'<a[^>]+dd_name="itemlist-title"[^>]*title="([^"]{8,80})"',
            r'<a[^>]+title="([^"]{8,80})"[^>]+dd_name',
            r'class="search_now_price"[^>]*>[^¥￥]*[¥￥]\s?([0-9.]+)',
            r'class="price_n"[^>]*>[^¥￥]*[¥￥]\s?([0-9.]+)']:
    hits = re.findall(pat, html)
    print(f"pat {pat[:48]!r}: {len(hits)}", hits[:4])
