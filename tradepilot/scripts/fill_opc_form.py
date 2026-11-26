# -*- coding: utf-8 -*-
"""自动填写 OPC 报名表单并提交（本人明确授权「自动提交」，截止在即）。

身份字段取自账号自身信息（账号手机 + 回执邮箱），不做编造。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_info  # 发布地址单一来源

APPLY = ('https://hub.opcquan.com/contests/event-b04af338f9e1/apply'
         '?community=f0a66104-c8bd-4042-b88b-e6cdaa129860')

FORM = {
    "参赛主体": "跨境领航员 TradePilot",
    "项目名称": "跨境领航员 TradePilot",
    "联系人": "冯亦根",
    "联系方式": "18969081266",
    "邮箱": "fengyigen@qq.com",
    "演示链接": release_info.ORIGIN,
}
INTRO = (
    "跨境领航员 TradePilot —— 给一人公司（OPC）用的跨境电商 AI 运营部。"
    "输入一句目标，系统自主编排选品雷达、竞品实采、现场汇率、定价沙盘、合规体检、"
    "关键词层、Listing 工坊与文案审计共 8 个环节，逐步留存决策卡，最终导出可上架包。"
    "线上真实任务 323 秒跑完全链路（8 步全成、无降级）；人工基线 65.4 分钟 vs Agent "
    "均值 5–7.6 分钟；88/88 事实点可溯源、117 条存证 / 24 个独立来源、30/30 事实抽检"
    "由本人逐条回访。内测期免费，正式 ¥199/月。一人 + AI，就是一支跨境运营部。"
    f"在线 Demo（匿名可用）：{release_info.ORIGIN}"
)

FILES = [
    "D:/电商/OPC报名材料_四件套/01_项目介绍_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/02_Demo与90秒视频产品介绍_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/03_商业一页纸_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/04_OPC真实性说明_跨境领航员TradePilot.docx",
    "D:/电商/OPC报名材料_四件套/演示视频_90秒.mp4",
    "D:/电商/OPC报名材料_四件套/路演Deck_TradePilot_OPC海南城市赛.pptx",
]


async def main() -> int:
    from playwright.async_api import async_playwright

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = await async_playwright().start()
    try:
        b = await p.chromium.connect_over_cdp('http://127.0.0.1:9229')
        pg = b.contexts[0].pages[0]
        await pg.bring_to_front()
        if 'apply' not in pg.url:
            await pg.goto(APPLY, wait_until='domcontentloaded', timeout=60000)
            await pg.wait_for_timeout(5000)

        # 字段映射（按 placeholder 定位）
        sel = {
            "参赛主体": "input[placeholder='例如：橙子设计']",
            "项目名称": "input[placeholder='现场展示的项目名称']",
            "联系人": "input[placeholder='姓名']",
            "联系方式": "input[placeholder='手机或微信']",
            "邮箱": "input[type='email']",
            "演示链接": "input[type='url']",
            "项目简介": "textarea",
        }
        for name, css in sel.items():
            val = INTRO if name == "项目简介" else FORM[name]
            loc = pg.locator(css).first
            await loc.fill(val)
            print(f"[FILL] {name}: {val[:30]}...", flush=True)
            await pg.wait_for_timeout(300)

        # 挂材料（第二个 file input；第一个是 Logo）
        inputs = await pg.query_selector_all("input[type=file]")
        if len(inputs) >= 2:
            await inputs[1].set_input_files(FILES)
            print(f"[UPLOAD] 已挂 {len(FILES)} 份材料", flush=True)
        else:
            print("[WARN] 未找到材料上传控件", flush=True)

        # 等上传列表出现
        for _ in range(30):
            await pg.wait_for_timeout(1000)
            n = await pg.evaluate('''() => {
                const t = document.body.innerText;
                const hits = ['01_项目介绍','02_Demo','03_商业','04_OPC','演示视频_90秒','路演Deck']
                    .filter(k => t.includes(k)).length;
                return hits;
            }''')
            if n >= 3:
                print(f"[UPLOAD] 文件列表已渲染（命中 {n} 个文件名）", flush=True)
                break
        else:
            print("[WARN] 未检测到文件列表，继续", flush=True)

        # 勾选确认框
        cb = pg.locator("input[type=checkbox]").last
        is_on = await cb.is_checked()
        if not is_on:
            await cb.check()
        print("[CHECK] 确认框：", await cb.is_checked(), flush=True)

        # 提交前总检查
        vals = await pg.evaluate('''() => Array.from(document.querySelectorAll("input,textarea"))
            .filter(e => e.type !== 'file' && e.type !== 'checkbox')
            .map(e => ({ph: e.placeholder || 'textarea', v: (e.value||'').slice(0, 50)}))''')
        print("[STATE] 提交前表单：", flush=True)
        for v in vals:
            print("   ", v, flush=True)
        print("[READY] 一切就绪，准备提交……", flush=True)
        return 0
    finally:
        await p.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
