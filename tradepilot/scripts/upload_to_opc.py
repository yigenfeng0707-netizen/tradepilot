# -*- coding: utf-8 -*-
"""协助上传：打开 OPC 报名页（有头浏览器）→ 挂上 6 份材料文件 → 保持窗口供本人操作。

身份字段（联系人/电话/邮箱）、确认勾选与「提交报名」由本人操作——
按项目红线 agent 不经手身份信息、不代 submitting（提交后报名信息锁定）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # 发布地址单一来源

FILES = [
    "D:/电商/OPC报名材料_四件套/01_项目介绍_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/02_Demo与90秒视频产品介绍_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/03_商业一页纸_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/04_OPC真实性说明_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/演示视频_90秒.mp4",
    "D:/电商/OPC报名材料_四件套/路演Deck_TradePilot_OPC海南城市赛.pptx",
]
URL = ("https://hub.opcquan.com/contests/event-b04af338f9e1/apply"
       "?community=f0a66104-c8bd-4042-b88b-e6cdaa129860")


async def main() -> int:
    from playwright.async_api import async_playwright

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    missing = [f for f in FILES if not Path(f).exists()]
    if missing:
        print("缺失文件：", missing)
        return 2
    total_mb = sum(Path(f).stat().st_size for f in FILES) / 1e6
    print(f"6 份材料共 {total_mb:.1f} MB（限制：单份 10MB / 视频 50MB、合计 60MB、最多 10 份）")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # 项目材料上传区（第二个 file input；第一个是项目 Logo）
        inputs = await page.query_selector_all("input[type=file]")
        print(f"文件上传控件数：{len(inputs)}")
        if len(inputs) >= 2:
            await inputs[1].set_input_files(FILES)
            await page.wait_for_timeout(2000)
            print("✅ 已挂上 6 份材料；请检查页面上的文件列表")
        else:
            print("⚠ 未找到材料上传控件，请手动拖拽文件")

        print("=" * 60)
        print("浏览器已打开。请本人完成：")
        print("  1. 核对/填写参赛主体、项目名称、联系人、联系方式、邮箱")
        print(f"  2. 演示链接：{release_info.ORIGIN}")
        print("  3. 项目简介（≤2000 字）")
        print("  4. 勾选「我确认提交信息真实有效」→ 提交报名")
        print("  5. 提交成功后截图保存到 归档/赛事资产-OPC海南2026-11/报名提交材料包/回执/")
        print("注意：11-18 已提交过报名（待审核）。若页面提示重复提交或锁定，")
        print("      请先去赛事服务群 @组委会 确认是「更新提交」还是需人工补料。")
        print("本窗口将保持打开；关掉浏览器或停掉后台任务即结束。")
        print("=" * 60)
        try:
            while True:
                await asyncio.sleep(30)
        except KeyboardInterrupt:
            pass
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
