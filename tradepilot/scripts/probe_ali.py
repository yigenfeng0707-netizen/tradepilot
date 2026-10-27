# -*- coding: utf-8 -*-
"""AliExpress 深度诊断：模态框/区域/内嵌 JSON 价格形态。"""
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
    page = ctx.new_page()
    page.goto("https://www.aliexpress.com/w/wholesale-dog-bed.html",
              timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(9000)
    # 处理常见弹层（ ship-to / 优惠通知 ），尽力而为
    for sel in ("button:has-text('Ship to')", "button:has-text('OK')",
                "button:has-text('Accept')", "[data-role='close']", "button.close"):
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=1500)
                page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001,S110
            pass
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(4000)
    html = page.content()
    text = page.inner_text("body")
    browser.close()

print("=== html len:", len(html), "text len:", len(text))
for pat in [r"US \$[0-9]+(?:\.[0-9]{2})?", r"\$[0-9]+\.[0-9]{2}", r"CNY", r"salePrice",
            r"formatedAmount", r"minPrice|maxPrice", r'"price"']:
    hits = re.findall(pat, html)
    print(f"html pat {pat!r}: {len(hits)}", hits[:5])
lines = [l.strip() for l in text.splitlines() if 15 < len(l.strip()) < 80]
print("=== 可见行样本:")
for l in lines[:15]:
    print("  |", l)
