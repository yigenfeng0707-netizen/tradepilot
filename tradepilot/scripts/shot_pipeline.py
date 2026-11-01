# -*- coding: utf-8 -*-
"""真跑一次全链路并截图，确认 SSE 进度与结果渲染正常。

整链 3–4 分钟，脚本超时按 420s 给。
"""
import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8080/app/pipeline"
OUT = "shot_pipeline.png"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1700})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(URL, wait_until="networkidle")
        await page.get_by_role("button", name="运行全链路").click()

        # 等"执行总览"出现（done 事件后才渲染）
        try:
            await page.wait_for_selector("text=执行总览", timeout=420_000)
        except Exception:
            await page.screenshot(path="shot_pipeline_fail.png", full_page=True)
            print("TIMEOUT: 未在 420s 内完成，已存 shot_pipeline_fail.png")
            print("console errors:", errors)
            await browser.close()
            return 1

        await page.wait_for_timeout(800)
        await page.screenshot(path=OUT, full_page=True)
        body = await page.inner_text("body")
        print("screenshot:", OUT)
        for kw in ("完成步骤", "耗时", "失败步骤", "已定位原文", "口径"):
            print(f"  contains {kw}:", kw in body)
        print("console errors:", errors or "none")
        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
