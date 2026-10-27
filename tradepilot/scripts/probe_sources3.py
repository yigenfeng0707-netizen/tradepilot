# -*- coding: utf-8 -*-
"""数据源探针第三轮：chewy / newegg / wayfair / bing-shopping（完整输出，不截断）。"""
import re
import sys

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TARGETS = {
    "chewy": "https://www.chewy.com/s?query=dog+bed",
    "newegg": "https://www.newegg.com/p/pl?d=dog+bed",
    "wayfair": "https://www.wayfair.com/keyword.php?keyword=dog+bed",
    "bing": "https://www.bing.com/shop?q=dog+bed&market=en-US",
}


def probe(name: str, url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1440,900"],
        )
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1440, "height": 900},
                                  timezone_id="America/New_York")
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            html = page.content()
            title = page.title()
            prices = re.findall(r"\$\s?([0-9]{1,4}\.[0-9]{2})", html)
            print(f"[{name}] title={title!r} len={len(html)} $prices={len(prices)} {prices[:8]}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:150]}", flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    for name, url in TARGETS.items():
        probe(name, url)
