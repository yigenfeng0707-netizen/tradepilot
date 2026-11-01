# -*- coding: utf-8 -*-
"""合规页截图：默认跑印尼（静态库，秒回），避免每次都等 50 秒官方抓取。"""
import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8080/app/compliance"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1500})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(URL, wait_until="networkidle")
        await page.get_by_role("button", name="检查合规").click()
        try:
            await page.wait_for_selector("text=条款与核验状态", timeout=180_000)
        except Exception:
            await page.screenshot(path="shot_compliance_fail.png", full_page=True)
            print("TIMEOUT，已存 shot_compliance_fail.png")
            print("console errors:", errors)
            await browser.close()
            return 1
        await page.wait_for_timeout(500)
        await page.screenshot(path="shot_compliance.png", full_page=True)
        body = await page.inner_text("body")
        print("screenshot: shot_compliance.png")
        for kw in ("条款总数", "已定位原文", "静态参考", "本报告没有覆盖什么", "关税计算"):
            print(f"  contains {kw}:", kw in body)
        print("console errors:", errors or "none")
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
