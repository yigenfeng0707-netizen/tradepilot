# Copyright 2026 TradePilot project. Apache-2.0.
"""用真实数据重拍 README 里的运营大屏截图（docs/img/dashboard.png）。

## 为什么要做成脚本

这张图原来是一张 2026-10-30 手工截的 png：后来界面加了「本步耗时」「抓取通道」
「外部复核」这些字段，图还停在旧版——README 展示的和代码跑出来的不是一个东西。
手工截图必然过期，所以把"怎么截出来的"固化下来：跑一次就对得上。

## 做法

打开线上发布的应用（带分享码）→ 等健康检查通过 → 从「回放已存档任务」里选最新一条
→ 回放 → 整页截图。回放的是**真实抓取结果**（含当时存证号与时间戳），
所以图里的数字都是可核对的，不是摆拍。

    python scripts/shot_dashboard.py                      # 默认截线上应用
    python scripts/shot_dashboard.py --base http://127.0.0.1:8080 --no-sharecode
    python scripts/shot_dashboard.py --out /tmp/shot.png --width 1440

前置：需要 Playwright 的 Chromium（`playwright install chromium`），或用
`TRADEPILOT_CHROMIUM` 指定可执行文件路径。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_info  # noqa: E402  发布地址的单一来源


def _chromium() -> str:
    """优先环境变量，其次 Playwright 默认缓存目录里最新的一个。"""
    env = os.environ.get("TRADEPILOT_CHROMIUM", "").strip()
    if env and Path(env).exists():
        return env
    cache = Path.home() / "AppData" / "Local" / "ms-playwright"
    if not cache.exists():
        cache = Path.home() / ".cache" / "ms-playwright"
    for exe in sorted(cache.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True):
        return str(exe)
    for exe in sorted(cache.glob("chromium-*/chrome-linux/chrome"), reverse=True):
        return str(exe)
    return ""            # 交给 Playwright 自己找


async def shot(base: str, sharecode: str, out: Path, width: int) -> int:
    from playwright.async_api import async_playwright

    query = f"?sharecode={sharecode}" if sharecode else ""
    url = f"{base.rstrip('/')}/{query}"
    print(f"打开 {url}")

    async with async_playwright() as p:
        kwargs = {"headless": True}
        exe = _chromium()
        if exe:
            kwargs["executable_path"] = exe
        browser = await p.chromium.launch(**kwargs)
        page = await browser.new_page(viewport={"width": width, "height": 1000},
                                      device_scale_factor=1)
        # 注意：不能用 networkidle——页面有常驻 WebSocket 与轮询，网络永远不会 idle，
        # 等它必然 60s 超时。改成 domcontentloaded + 显式等健康检查灯。
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 健康检查灯亮起来再继续（否则截到的是"连接中…"）
        await page.wait_for_function(
            "() => { const h = document.querySelector('#health');"
            " return h && h.textContent && !h.textContent.includes('连接中'); }",
            timeout=30000)

        # 载入存档列表 → 选最新一条 → 回放
        sel = page.locator("#replay-select")
        await sel.click()
        await page.wait_for_function(
            "() => { const s = document.querySelector('#replay-select');"
            " return s && s.options.length > 1; }", timeout=30000)
        latest = await page.evaluate(
            "() => { const s = document.querySelector('#replay-select');"
            " return s.options[1] ? s.options[1].value : ''; }")
        if not latest:
            print("没有可回放的存档：先跑一次任务再截图")
            await browser.close()
            return 2
        print(f"回放存档 {latest}")
        await sel.select_option(value=latest)
        await page.click("#replay")
        await page.wait_for_function(
            "() => { const n = document.querySelector('#engine-note');"
            " return n && n.textContent.includes('回放存档'); }", timeout=45000)

        # Canvas 是同步绘制的，但给一个渲染帧的余量，避免截到半张图
        await page.wait_for_timeout(1200)
        out.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out), full_page=True)
        await browser.close()

    print(f"已保存 {out}（{out.stat().st_size // 1024} KB）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=release_info.ORIGIN)
    ap.add_argument("--sharecode", default=release_info.SHARECODE)
    ap.add_argument("--no-sharecode", action="store_true", help="本地调试用")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "dashboard.png"))
    ap.add_argument("--width", type=int, default=1600)
    args = ap.parse_args()
    code = "" if args.no_sharecode else args.sharecode
    return asyncio.run(shot(args.base, code, Path(args.out), args.width))


if __name__ == "__main__":
    sys.exit(main())
