# -*- coding: utf-8 -*-
"""Bing Shopping 页面结构诊断：价格到底以什么形态存在。"""
import re
import sys

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
                                args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(user_agent=UA, locale="en-US",
                              viewport={"width": 1440, "height": 900},
                              timezone_id="America/New_York")
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page = ctx.new_page()
    page.goto("https://www.bing.com/shop?q=dog+bed&market=en-US",
              timeout=45000, wait_until="networkidle")
    page.wait_for_timeout(5000)
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(3000)
    html = page.content()
    text = page.inner_text("body")
    browser.close()

print("=== html len:", len(html), "text len:", len(text))
for pat in [r"\$[0-9]{1,4}(?:\.[0-9]{2})?", r"USD\s?[0-9]", r"US\s?\$", r"price",
            r"[0-9]{1,3}\.[0-9]{2}"]:
    hits = re.findall(pat, text)
    print(f"text pat {pat!r}: {len(hits)} hits", hits[:6])
# 标题形态：找商品卡标题行
lines = [l.strip() for l in text.splitlines() if 25 < len(l.strip()) < 90]
print("=== 商品行样本（前 12 行）:")
for l in lines[:12]:
    print("  |", l)
