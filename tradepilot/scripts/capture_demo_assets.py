# -*- coding: utf-8 -*-
"""为 OPC 报名材料采集真实素材（线上 Demo 实录）。

线上发布页被魔搭壳页 iframe 包装（顶级导航会 302 到 modelscope.cn），
所以：打开壳页 → 拿内层 app frame → 关掉遮罩 → 进入「运营大屏」→ 回放真实存档任务。

产出（D:/电商/tradepilot/报名素材/）：
  shots/00-dashboard.png   运营大屏（回放前）
  shots/NN-decision.png    决策卡逐张（回放过程中按数量变化截取）
  shots/90-final.png       回放完成后整页
  shots/91-prov-modal.png  存证弹层
  video/replay.webm        回放过程浏览器录屏（真实内容，供 90 秒视频剪辑）
  captures.json            采集元信息
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # noqa: E402

OUT = ROOT / "报名素材"
SHOTS = OUT / "shots"
VIDEO = OUT / "video"


def app_frame(page):
    for f in page.frames:
        if f.url.startswith(release_info.ORIGIN):
            return f
    return None


async def main() -> int:
    from playwright.async_api import async_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    VIDEO.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=1,
            record_video_dir=str(VIDEO),
            record_video_size={"width": 1600, "height": 1000},
        )
        page = await context.new_page()
        print(f"打开 {release_info.SHARE_URL}")
        await page.goto(release_info.SHARE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        app = app_frame(page)
        if app is None:
            await page.wait_for_timeout(6000)
            app = app_frame(page)
        if app is None:
            print("❌ 找不到应用 iframe")
            await browser.close()
            return 2
        print(f"应用 frame：{app.url[:80]}")

        # 等应用自身加载
        await app.wait_for_load_state("domcontentloaded", timeout=30000)
        await app.wait_for_timeout(2500)
        # 关掉壳页/应用可能的遮罩
        await app.evaluate(
            "() => { document.querySelectorAll('.fixed.inset-0.z-50').forEach(e=>e.remove()); }")
        await page.wait_for_timeout(300)

        # 进入「运营大屏」（若不在）
        has_dash = await app.evaluate("() => !!document.querySelector('#decisions')")
        if not has_dash:
            await app.locator("text=运营大屏").first.click()
            await page.wait_for_timeout(2500)
            await app.evaluate(
                "() => { document.querySelectorAll('.fixed.inset-0.z-50').forEach(e=>e.remove()); }")

        await app.wait_for_function(
            "() => { const h = document.querySelector('#health');"
            " return h && h.textContent && !h.textContent.includes('连接中'); }",
            timeout=30000)
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(SHOTS / "00-dashboard.png"))
        print("  00-dashboard.png")

        # 载入存档列表 → 选最新一条 → 回放
        sel = app.locator("#replay-select")
        await sel.click()
        await app.wait_for_function(
            "() => { const s = document.querySelector('#replay-select');"
            " return s && s.options.length > 1; }", timeout=30000)
        latest = await app.evaluate(
            "() => { const s = document.querySelector('#replay-select');"
            " return s.options[1] ? s.options[1].value : ''; }")
        if not latest:
            print("❌ 没有可回放的存档：先跑一次任务再截图")
            await browser.close()
            return 2
        print(f"回放存档 {latest}")
        await sel.select_option(value=latest)
        await app.click("#replay")
        await app.wait_for_function(
            "() => { const n = document.querySelector('#engine-note');"
            " return n && n.textContent.includes('回放存档'); }", timeout=45000)

        # 决策卡随回放逐张出现：按数量变化逐张截取（最多 12 张）
        last, idx = 0, 1
        stable = 0
        for _ in range(160):  # 最多 80s
            n = await app.evaluate(
                "() => document.querySelectorAll('#decisions .decision, #decisions .card').length")
            if n > last:
                last, stable = n, 0
                await page.wait_for_timeout(350)
                await page.screenshot(path=str(SHOTS / f"{idx:02d}-decision.png"))
                print(f"  {idx:02d}-decision.png（决策卡 {n} 张）")
                idx += 1
                if idx > 12:
                    break
            else:
                stable += 1
                done = await app.evaluate(
                    "() => { const r = document.querySelector('#head-meta');"
                    " return r && r.textContent && !r.textContent.includes('尚未执行任务'); }")
                if done and stable >= 4:
                    break
            await page.wait_for_timeout(500)

        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(SHOTS / "90-final.png"))
        print("  90-final.png（整页）")

        # 随机点开一条来源存证弹层
        links = await app.query_selector_all("#competitors a[data-prov], #decisions a[data-prov], a.prov")
        if not links:
            links = await app.query_selector_all("#competitors a, #decisions a")
        prov_text = ""
        if links:
            li = random.randrange(len(links))
            await links[li].scroll_into_view_if_needed()
            await page.wait_for_timeout(400)
            await links[li].click()
            await page.wait_for_timeout(800)
            try:
                await app.wait_for_selector("#prov-modal:not([hidden])", timeout=8000)
                await page.wait_for_timeout(600)
                await page.screenshot(path=str(SHOTS / "91-prov-modal.png"))
                prov_text = (await app.text_content("#prov-body")) or ""
                print(f"  91-prov-modal.png（第 {li+1}/{len(links)} 条存证）")
            except Exception as exc:  # noqa: BLE001
                print(f"  存证弹层未打开：{type(exc).__name__}")

        await page.wait_for_timeout(1200)
        await context.close()
        await browser.close()

    (OUT / "captures.json").write_text(
        json.dumps({"mission": latest, "prov_body_head": prov_text[:400],
                    "decisions_captured": idx - 1},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("✅ 采集完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
