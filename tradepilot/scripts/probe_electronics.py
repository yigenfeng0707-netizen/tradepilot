# -*- coding: utf-8 -*-
"""验证 electronics 修复：浏览器渲染 HTML → parse_dangdang。"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.tools.fetch import fetch_browser  # noqa: E402
from app.tools.sources import parse_dangdang  # noqa: E402

async def main() -> None:
    r = await fetch_browser("https://search.dangdang.com/?key=usb+cable&act=input")
    items = parse_dangdang(r["html"], r["url"])
    print(f"parse_dangdang → {len(items)} items")
    for it in items[:5]:
        print(f"  · ¥{it['price']:.2f}  {it['title'][:60]}")

asyncio.run(main())
