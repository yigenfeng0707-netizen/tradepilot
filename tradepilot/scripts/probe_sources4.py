# -*- coding: utf-8 -*-
"""探针第四轮：京东公开搜索（真实采购端价格）+ frankfurter 汇率 API + 速卖通移动端。"""
import json
import re
import sys

sys.path.insert(0, ".")

import httpx
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def probe_fx() -> None:
    """真实汇率：frankfurter.app（欧洲央行数据，免费无 Key）。"""
    try:
        r = httpx.get("https://api.frankfurter.app/latest?from=USD&to=CNY", timeout=20)
        print("[fx/frankfurter]", r.status_code, r.text[:120])
    except Exception as e:  # noqa: BLE001
        print("[fx/frankfurter] FAIL", type(e).__name__, str(e)[:100])


def probe_jd() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1440, "height": 900},
                                  timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        try:
            page.goto("https://search.jd.com/Search?keyword=dog%20bed&enc=utf-8",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(4000)
            html = page.content()
            text = page.inner_text("body")
            prices = re.findall(r"[¥￥]\s?([0-9]{2,5}(?:\.[0-9]{1,2})?)", text)
            print(f"[jd] title={page.title()!r} len={len(html)} ¥prices={len(prices)} {prices[:8]}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[jd] FAIL {type(e).__name__}: {str(e)[:120]}", flush=True)
        finally:
            browser.close()


def probe_ali_mobile() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 414, "height": 896},
                                  timezone_id="America/New_York")
        page = ctx.new_page()
        try:
            page.goto("https://m.aliexpress.com/wholesale/dog-bed.html",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(9000)
            html = page.content()
            prices = re.findall(r"US \$([0-9]+\.[0-9]{2})", html)
            print(f"[ali-mobile] title={page.title()!r} len={len(html)} prices={len(prices)}",
                  prices[:6], flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[ali-mobile] FAIL {type(e).__name__}: {str(e)[:120]}", flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    probe_fx()
    probe_jd()
    probe_ali_mobile()
