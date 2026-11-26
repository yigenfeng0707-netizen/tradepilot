# -*- coding: utf-8 -*-
"""补拍：来源存证弹层（live-91-prov-modal.png）。

回放最新任务 → 点开竞品表第一条「存证#N」→ 截图。回放是真实抓取结果的归档。
单独成脚本：上次 live 采集在打印 meta 时因 GBK 崩溃，存证弹层那步没跑。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # noqa: E402

OUT = ROOT / "报名素材"
SHOTS = OUT / "shots"


def app_frame(page):
    for f in page.frames:
        if f.url.startswith(release_info.ORIGIN):
            return f
    return None


async def main() -> int:
    from playwright.async_api import async_playwright

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    SHOTS.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        await page.goto(release_info.SHARE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        app = app_frame(page)
        await app.wait_for_load_state("domcontentloaded", timeout=30000)
        await app.wait_for_timeout(2500)
        await app.evaluate(
            "() => { document.querySelectorAll('.fixed.inset-0.z-50').forEach(e=>e.remove()); }")
        await page.wait_for_timeout(300)
        has_dash = await app.evaluate("() => !!document.querySelector('#decisions')")
        if not has_dash:
            await app.locator("text=运营大屏").first.click()
            await page.wait_for_timeout(2500)
        await app.wait_for_function(
            "() => { const h = document.querySelector('#health');"
            " return h && h.textContent && !h.textContent.includes('连接中'); }",
            timeout=30000)

        # 回放最新任务让结果区有数据
        sel = app.locator("#replay-select")
        await sel.click()
        await app.wait_for_function(
            "() => { const s = document.querySelector('#replay-select');"
            " return s && s.options.length > 1; }", timeout=30000)
        latest = await app.evaluate(
            "() => { const s = document.querySelector('#replay-select');"
            " return s.options[1] ? s.options[1].value : ''; }")
        await sel.select_option(value=latest)
        await app.click("#replay")
        await app.wait_for_function(
            "() => { const n = document.querySelector('#engine-note');"
            " return n && n.textContent.includes('回放存档'); }", timeout=45000)
        await page.wait_for_timeout(2500)

        links = await app.query_selector_all(
            "#competitors button[data-prov], button.prov-link, button[data-prov]")
        print(f"存证链接数：{len(links)}")
        if not links:
            print("未找到存证链接")
            await browser.close()
            return 2
        await links[0].scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await links[0].click()
        await page.wait_for_timeout(1500)
        await app.wait_for_selector("#prov-modal:not([hidden])", timeout=8000)
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(SHOTS / "live-91-prov-modal.png"))
        body = (await app.text_content("#prov-body")) or ""
        print("存证弹层已截: live-91-prov-modal.png")
        print("存证内容前 200 字：", body[:200])
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
