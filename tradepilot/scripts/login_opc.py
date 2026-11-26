# -*- coding: utf-8 -*-
"""启动有头浏览器（CDP 9229 端口）打开 OPC-Hub 登录页，保持存活。

后续操作由其他进程通过 connect_over_cdp('http://127.0.0.1:9229') 连入执行。
"""
from __future__ import annotations

import asyncio
import sys

CDP_PORT = 9229
LOGIN = "https://hub.opcquan.com/login"


async def main() -> int:
    from playwright.async_api import async_playwright

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
                  "--no-sandbox"],
        )
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        print(f"[STATE] login page open (CDP :{CDP_PORT})", flush=True)
        try:
            while True:
                await asyncio.sleep(5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
