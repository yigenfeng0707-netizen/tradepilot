# -*- coding: utf-8 -*-
"""探针第五轮（收敛轮）：1688 / 当当 / 什么值得买 公开搜索页。"""
import re
import sys

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TARGETS = {
    "1688": "https://s.1688.com/selloffer/offer_search.htm?keywords=dog+bed",
    "dangdang": "http://search.dangdang.com/?key=dog+bed&act=input",
    "smzdm": "https://search.smzdm.com/?c=home&s=dog+bed",
}


def probe(name: str, url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1440, "height": 900},
                                  timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            html = page.content()
            text = page.inner_text("body")
            prices = re.findall(r"[¥￥]\s?([0-9]{1,6}(?:\.[0-9]{1,2})?)", text)
            print(f"[{name}] title={page.title()!r} len={len(html)} ¥prices={len(prices)}",
                  prices[:8], flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:120]}", flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    for name, url in TARGETS.items():
        probe(name, url)
