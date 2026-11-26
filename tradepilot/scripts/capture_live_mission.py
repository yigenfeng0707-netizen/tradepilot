# -*- coding: utf-8 -*-
"""线上 Demo 真实任务实录：下达任务 → 决策卡逐张流出 → 报告生成 → 存证弹层。

产出（D:/电商/tradepilot/报名素材/）：
  shots/live-00-dashboard.png   下达任务前
  shots/live-NN-decision.png    决策卡逐张（真实流式，非回放）
  shots/live-90-final.png       任务完成后整页
  shots/live-91-prov-modal.png  存证弹层
  video/live-mission.webm       完整真实任务录屏
  captures-live.json            元信息（任务号、决策卡数、存证号、关键结果）

与回放不同：这是真实跑一次任务（LLM 在环、真实抓取），耗时约 5–8 分钟。
"""
from __future__ import annotations

import asyncio
import json
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
        await page.goto(release_info.SHARE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        app = app_frame(page)
        if app is None:
            await page.wait_for_timeout(6000)
            app = app_frame(page)
        if app is None:
            print("no app frame")
            await browser.close()
            return 2
        await app.wait_for_load_state("domcontentloaded", timeout=30000)
        await app.wait_for_timeout(2500)
        await app.evaluate(
            "() => { document.querySelectorAll('.fixed.inset-0.z-50').forEach(e=>e.remove()); }")
        await page.wait_for_timeout(300)
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
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(SHOTS / "live-00-dashboard.png"))
        print("[SHOT] live-00-dashboard.png", flush=True)

        # 下达真实任务（默认目标：把宠物用品切入美国站）
        await app.click("#run")
        print("[RUN] 任务已下达，等待决策卡流式出现", flush=True)

        # 决策卡逐张截图（流式出现，数量变化即截）
        last, idx, stable = 0, 1, 0
        for _ in range(900):  # 最多 15 分钟
            n = await app.evaluate(
                "() => document.querySelectorAll('#decisions .decision, #decisions .card').length")
            done = await app.evaluate(
                "() => { const f = document.querySelector('#decision-foot');"
                " return f && f.textContent && (f.textContent.includes('完成')"
                " || f.textContent.includes('耗时') || f.textContent.includes('Done')); }")
            if n > last:
                last, stable = n, 0
                await page.wait_for_timeout(300)
                await page.screenshot(path=str(SHOTS / f"live-{idx:02d}-decision.png"))
                print(f"[SHOT] live-{idx:02d}-decision.png (cards={n})", flush=True)
                idx += 1
            else:
                stable += 1
            if done and stable >= 4:
                break
            await page.wait_for_timeout(500)

        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SHOTS / "live-90-final.png"))
        print("[SHOT] live-90-final.png", flush=True)

        meta = await app.evaluate("""() => ({
            decisions: document.querySelectorAll('#decisions .decision, #decisions .card').length,
            head_meta: (document.querySelector('#head-meta')||{}).textContent || '',
            prov_count: (document.querySelector('#prov-count')||{}).textContent || '',
            pricing: (document.querySelector('#pricing')||{}).textContent || '',
            compliance: (document.querySelector('#compliance')||{}).textContent || '',
            listing: (document.querySelector('#listing')||{}).textContent || '',
            audit: (document.querySelector('#audit')||{}).textContent || '',
            keywords: (document.querySelector('#keywords')||{}).textContent || '',
        })""")
        print("[META]", json.dumps(meta, ensure_ascii=False)[:600], flush=True)

        # 点开一条来源存证弹层
        links = await app.query_selector_all("#competitors button[data-prov], button.prov-link, button[data-prov]")
        prov_text = ""
        if links:
            await links[0].scroll_into_view_if_needed()
            await page.wait_for_timeout(400)
            await links[0].click()
            await page.wait_for_timeout(1500)
            try:
                await app.wait_for_selector("#prov-modal:not([hidden])", timeout=8000)
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(SHOTS / "live-91-prov-modal.png"))
                prov_text = (await app.text_content("#prov-body")) or ""
                print(f"[SHOT] live-91-prov-modal.png ({len(links)} 条存证链接)", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] 存证弹层未打开: {type(exc).__name__}", flush=True)

        await page.wait_for_timeout(1500)
        await context.close()
        await browser.close()

    (OUT / "captures-live.json").write_text(
        json.dumps({"meta": meta, "prov_body_head": prov_text[:600],
                    "decision_shots": idx - 1},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("[DONE] 采集完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
