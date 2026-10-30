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
import os
import time
import urllib.robotparser as robotparser
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import CONFIG

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BROWSER_FIRST = ("walmart.com", "temu.com", "aliexpress.com", "newegg.com")
# 拦截页特征：只认具体话术，不能再放裸 "robot"——正常商品页的 charset 区就有
# <meta name="robots">，会把 Newegg 这种 1.5MB 真实结果页误判成拦截页。
_CHALLENGE_MARKS = ("robot or human", "verify you are human", "are you a robot",
                    "captcha", "just a moment", "checking your browser",
                    "unusual traffic", "access denied")
# 商品页信号：命中即认为拿到的是一屏真实结果，不是挑战页
_PRODUCT_SIGNALS = ("price", "product", "itemprop", "sku")

_last_hit: dict[str, float] = {}
_lock = asyncio.Lock()
_robots_cache: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}
_ROBOTS_TTL_S = 3600.0


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def robots_allowed(url: str) -> tuple[bool, str]:
    """robots.txt 合规闸门：返回 (是否允许, 说明)。

    这是作品"只抓公开页、尊重 robots"声明的代码实现——声明必须可执行，
    否则就是话术。404/不可达按惯例视为允许（RFC 9309）；明确 Disallow 则拒绝。
    """
    if not CONFIG.respect_robots:
        return True, "robots 校验已关闭（TRADEPILOT_RESPECT_ROBOTS=0）"
    origin = _origin(url)
    cached = _robots_cache.get(origin)
    now = time.monotonic()
    if cached and now - cached[0] < _ROBOTS_TTL_S:
        parser = cached[1]
    else:
        parser = None
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(f"{origin}/robots.txt", headers={"User-Agent": UA})
                if resp.status_code == 200:
                    parser = robotparser.RobotFileParser()
                    parser.parse(resp.text.splitlines())
        except Exception:  # noqa: BLE001 — 取不到 robots 不阻断，按惯例放行
            parser = None
        _robots_cache[origin] = (now, parser)
    if parser is None:
        return True, "robots.txt 不可达，按 RFC 9309 惯例放行"
    if parser.can_fetch(UA, url):
        return True, "robots.txt 允许"
    return False, f"robots.txt 禁止抓取 {url}"


def _domain(url: str) -> str:
    return url.split("/")[2].lower() if "://" in url else url.lower()


def _looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    if len(html) < 4000:
        return True
    if any(sig in low for sig in _PRODUCT_SIGNALS):  # 有真实商品字段就不是挑战页
        return False
    return any(mark in low for mark in _CHALLENGE_MARKS)


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


def _browser_executable() -> str | None:
    """浏览器路径选择：显式配置 > 容器内系统浏览器 > Playwright 自带（返回 None）。"""
    if CONFIG.chromium_path and os.path.exists(CONFIG.chromium_path):
        return CONFIG.chromium_path
    for path in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.exists(path):
            return path
    return None


# 浏览器复用：Playwright 同步 API 绑定创建它的线程，因此固定用单线程池，
# 进程内只启动一次 Chromium（启动开销 ~1.5s/次，在 200s 级任务里占比很高）。
_browser_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tp-browser")
_browser_state: dict[str, Any] = {"playwright": None, "browser": None}


def _get_browser() -> Any:
    from playwright.sync_api import sync_playwright

    browser = _browser_state.get("browser")
    if browser is not None and browser.is_connected():
        return browser
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=CONFIG.browser_headless,
        executable_path=_browser_executable(),
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    _browser_state["playwright"] = playwright
    _browser_state["browser"] = browser
    return browser


def shutdown_browser() -> None:
    """进程退出时释放浏览器（Cloud Run 优雅停机用）。"""
    def _close() -> None:
        try:
            if (b := _browser_state.get("browser")) is not None:
                b.close()
            if (p := _browser_state.get("playwright")) is not None:
                p.stop()
        except Exception:  # noqa: BLE001,S110 — 停机清理失败不影响退出
            pass
        finally:
            _browser_state["browser"] = None
            _browser_state["playwright"] = None

    try:
        _browser_pool.submit(_close).result(timeout=15)
    except Exception:  # noqa: BLE001,S110
        pass


def _fetch_browser_sync(url: str) -> dict[str, Any]:
    browser = _get_browser()
    context = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(url, timeout=CONFIG.fetch_timeout_s * 1000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)  # 等客户端水合
        return {"url": page.url, "status": 200, "html": page.content()}
    finally:
        context.close()  # 只回收上下文，浏览器进程复用


async def fetch_browser(url: str) -> dict[str, Any]:
    """强路径：Playwright 无头浏览器（固定单线程池，浏览器进程复用）。"""
    await _throttle(_domain(url))
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_browser_pool, _fetch_browser_sync, url)


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
