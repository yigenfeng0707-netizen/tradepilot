# Copyright 2026 TradePilot project. Apache-2.0.
"""双通道抓取层：httpx 快路径 + Playwright 无头浏览器强路径。

策略表（per-domain）：
- walmart.com 等有反爬的电商站点 → 直接走浏览器（拦截页实测 15190 字节同响应）；
- 官方政策站点（fda.gov / cpsc.gov / cbp.gov 等）→ httpx 快路径即可；
- 其余域名先 httpx，若疑似拦截页（内容过短/含 challenge 特征）自动升级浏览器。

礼貌约束：同主机最小间隔（config.per_host_min_interval_s）、UA 真实、只抓公开页。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..config import CONFIG

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BROWSER_FIRST = ("walmart.com", "temu.com", "aliexpress.com")
_CHALLENGE_MARKS = ("robot", "challenge", "captcha", "verify you are human")

_last_hit: dict[str, float] = {}
_lock = asyncio.Lock()


def _domain(url: str) -> str:
    return url.split("/")[2].lower() if "://" in url else url.lower()


def _looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    if len(html) < 4000 and len(low) < 4000:
        return True
    return any(mark in low for mark in _CHALLENGE_MARKS) and "price" not in low


async def _throttle(domain: str) -> None:
    async with _lock:
        now = time.monotonic()
        wait = CONFIG.per_host_min_interval_s - (now - _last_hit.get(domain, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_hit[domain] = time.monotonic()


async def fetch_http(url: str) -> dict[str, Any]:
    """快路径：httpx + 真实浏览器头。"""
    await _throttle(_domain(url))
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True, timeout=CONFIG.fetch_timeout_s,
    ) as client:
        resp = await client.get(url)
        return {"url": str(resp.url), "status": resp.status_code, "html": resp.text}


async def fetch_browser(url: str) -> dict[str, Any]:
    """强路径：Playwright 无头浏览器（线程池中跑同步 API）。"""
    await _throttle(_domain(url))
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_browser_sync, url)


def _fetch_browser_sync(url: str) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # 使用系统 chromium（魔搭云环境无 Playwright 自带浏览器）
        browser = p.chromium.launch(
            headless=CONFIG.browser_headless,
            channel="chromium",  # 使用系统 chromium
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        page = browser.new_page(user_agent=UA, locale="en-US",
                                viewport={"width": 1440, "height": 900})
        try:
            page.goto(url, timeout=CONFIG.fetch_timeout_s * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)  # 等客户端水合
            return {"url": page.url, "status": 200, "html": page.content()}
        finally:
            browser.close()


async def fetch(url: str) -> dict[str, Any]:
    """按域名策略抓取，返回 {url, status, html, channel}。可能抛异常，由执行器兜底。"""
    domain = _domain(url)
    if any(domain.endswith(d) for d in BROWSER_FIRST):
        result = await fetch_browser(url)
        result["channel"] = "browser"
        return result
    result = await fetch_http(url)
    result["channel"] = "http"
    if _looks_blocked(result["html"]):
        upgraded = await fetch_browser(url)
        upgraded["channel"] = "browser+fallback"
        return upgraded
    return result
