# -*- coding: utf-8 -*-
"""当当搜索页结构诊断：看价格与标题的真实 DOM 形态。"""
import re
import sys

sys.path.insert(0, ".")

from app.tools.fetch import fetch  # noqa: E402


async def main() -> None:
    r = await fetch("http://search.dangdang.com/?key=dog+bed&act=input")
    html = r["html"]
    print("channel:", r["channel"], "len:", len(html), "url:", r["url"])
    print("=== price_n 出现次数:", html.count("price_n"))
    i = html.find("price_n")
    if i > 0:
        print("=== price_n 上下文（首个）:")
        print(html[max(0, i - 600):i + 200].replace("\n", " ")[:800])
    print()
    print("=== ¥ 出现次数:", html.count("¥") + html.count("￥"))
    for pat in [r'title="([^"]{10,60})"', r'class="pic"[^>]*', r'dd_name="(?[^"]*)"']:
        hits = re.findall(pat, html)
        print(f"pat {pat!r}: {len(hits)}", [h[:40] for h in hits[:5]])
    titles = re.findall(r'title="([^"]{10,70})"', html)
    print("=== title 属性样本:")
    for t in titles[:8]:
        print("  |", t)


import asyncio  # noqa: E402
asyncio.run(main())
