# -*- coding: utf-8 -*-
"""真跑一次内容生成并截图，确认前端渲染的是后端真实产出。

教训：接口 curl 200 不等于页面正常——上一轮 Vite base 配错，接口全好、
页面整页白屏。前端接完必须真截图。
"""
import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8080/app/content-generation"
OUT = "shot_cg.png"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1600})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(URL, wait_until="networkidle")
        await page.fill('input[placeholder="请输入产品名称"]', "自动喂食器")
        await page.get_by_role("button", name="生成内容").click()

        # 单次生成 60–90 秒，留足余量；等结果区块出现
        try:
            await page.wait_for_selector("text=平台合规", timeout=240_000)
        except Exception:
            await page.screenshot(path="shot_cg_fail.png", full_page=True)
            print("TIMEOUT: 未在 240s 内拿到结果，已存 shot_cg_fail.png")
            print("console errors:", errors)
            await browser.close()
            return 1

        await page.wait_for_timeout(800)
        await page.screenshot(path=OUT, full_page=True)
        body = await page.inner_text("body")
        print("screenshot:", OUT)
        print("has [ADD] in page:", "[ADD" in body)
        print("has 兜底 warning:", "兜底" in body)
        print("console errors:", errors or "none")
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
