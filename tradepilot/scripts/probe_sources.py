# -*- coding: utf-8 -*-
"""数据源探针：Walmart(强隐身 Playwright) vs eBay(httpx) — 找出可用的真实数据源。"""
import asyncio
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


async def probe_ebay() -> None:
    url = "https://www.ebay.com/sch/i.html?_nkw=dog+bed&_ipg=25"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                                     follow_redirects=True, timeout=40) as c:
            r = await c.get(url)
            html = r.text
            prices = re.findall(r's-item__price[^>]*>.*?\$([0-9]+\.[0-9]{2})', html, re.S)
            titles = re.findall(r's-item__title[^>]*>([^<]{10,90})<', html)
            print(f"[ebay/httpx] HTTP {r.status_code} len={len(html)} prices={len(prices)} {prices[:6]}")
            print(f"  titles={len(titles)} sample={[t.strip()[:50] for t in titles[:2]]}")
    except Exception as e:
        print(f"[ebay/httpx] FAIL {type(e).__name__}: {e}")


def probe_walmart_stealth() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1440,900"],
        )
        ctx = browser.new_context(
            user_agent=UA, locale="en-US",
            viewport={"width": 1440, "height": 900},
            timezone_id="America/New_York",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto("https://www.walmart.com/search?q=dog+bed", timeout=60000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        html = page.content()
        title = page.title()
        prices = re.findall(r'"price":\s*([0-9]+\.[0-9]{2})', html) or re.findall(
            r"\$\s?([0-9]{1,3}\.[0-9]{2})", html)
        print(f"[walmart/stealth] title={title!r} len={len(html)} prices={len(prices)} {prices[:6]}")
        browser.close()


async def main() -> None:
    await probe_ebay()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, probe_walmart_stealth)
    except Exception as e:
        print(f"[walmart/stealth] FAIL {type(e).__name__}: {e}")


asyncio.run(main())
