#!/usr/bin/env python3
"""抽检预检助手：把 30 条人工回访从「自己开网页找证据」降到「看摘录按 y/n」。

对 assets/audit_sample.json 里每条未回访记录：
1. 用项目抓取栈重新抓 URL（先查 robots，不允许则跳过重抓）
2. 按 kind 给出建议判定与证据摘录，写入 assistant_check 字段
3. **绝不写 human_verified** —— 该字段只能由人在
   `human_baseline.py spotcheck` 交互确认时产生，这是方案红线。

用法：
    python scripts/spotcheck_assist.py             # 全部未回访条目
    python scripts/spotcheck_assist.py --limit 5   # 只预检 5 条

判定逻辑（保守：给不了把握的建议就留 None 让人看）：
- fx      : 重抓 API，汇率与记录值偏差 <=2% 视为支持（当日漂移容忍）
- policy  : matched_terms 命中页面文本 → 支持；有 excerpt 则摘录也要命中；
            page_only 且无词无摘录 → None（需人工）
- item    : 标题前 40 字符 + 价格字符串同时命中 → 支持；
            标题命中价格缺失 → None（可能改价）；标题缺失 → False
- 抓取失败/被拦/非 200 → None，注明原因
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tools.fetch import fetch, fetch_http, robots_allowed, shutdown_browser  # noqa: E402

SAMPLE_OUT = ROOT / "assets" / "audit_sample.json"

FX_TOLERANCE = 0.02          # 汇率容忍偏差（当日）
TITLE_PREFIX_LEN = 40        # 商品标题参与匹配的前缀长度

# 反爬挑战页标记：HTTP 200 但内容是"证明你是人"，绝不能当成"事实不支持"。
# 实测：Newegg 重抓时返回 200 + 55KB 的 "are you a human?" 页（同日首次抓取是真实页）。
_CHALLENGE_MARKERS = (
    "are you a human", "captcha", "verify you are", "perimeterx",
    "cf-challenge", "just a moment", "attention required",
    "access denied", "request blocked", "press & hold",
)


def _looks_challenge(html: str) -> str | None:
    low = _norm(html)
    for m in _CHALLENGE_MARKERS:
        if m in low:
            return m
    return None


def _norm(text: str) -> str:
    """页面/字段统一归一：解 HTML 实体 + 去标签 + 压空白 + 小写，抗排版差异。

    实测拦截点：页面里 "lithium&nbsp;battery" 或 "<b>lithium</b> battery"
    直接做字符串匹配会漏，必须先还原成纯文本。
    """
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_fx(row: dict, html: str) -> dict:
    try:
        cur = json.loads(html)
        rate = float(cur["rates"][row["payload"]["quote"]])
    except Exception as exc:
        return {"suggestion": None, "reason": f"响应无法解析为汇率 JSON：{exc}", "evidence": html[:120]}
    want = float(row["payload"]["rate"])
    drift = abs(rate - want) / want
    ok = drift <= FX_TOLERANCE
    return {
        "suggestion": ok,
        "reason": f"记录 {want}，现值 {rate}，偏差 {drift * 100:.2f}%（容忍 {FX_TOLERANCE * 100:.0f}%）",
        "evidence": f"USD/{row['payload']['quote']} = {rate}",
    }


def _check_policy(row: dict, html: str) -> dict:
    p = row["payload"]
    text = _norm(html)
    terms = [t for t in (p.get("matched_terms") or []) if t]
    excerpt = p.get("excerpt") or ""
    if not terms and not excerpt.strip():
        return {"suggestion": None, "reason": "page_only 且无匹配词/摘录，自动比对无据可依", "evidence": ""}
    hit = [t for t in terms if _norm(t) in text]
    if excerpt.strip():
        head = _norm(excerpt)[:80]
        excerpt_hit = head in text
    else:
        excerpt_hit = None
    if terms and not hit:
        return {"suggestion": False, "reason": "匹配词全部未在当前页面出现", "evidence": ""}
    if excerpt.strip() and excerpt_hit is False:
        return {"suggestion": None,
                "reason": "匹配词命中但摘录未命中（页面可能改版），需人工看",
                "evidence": f"命中词：{hit}"}
    return {"suggestion": True,
            "reason": f"匹配词命中 {len(hit)}/{len(terms)}" + ("" if excerpt_hit is None else "；摘录命中"),
            "evidence": f"命中词：{hit}"}


def _check_item(row: dict, html: str) -> dict:
    p = row["payload"]
    text = _norm(html)
    title_head = _norm(p.get("title") or "")[:TITLE_PREFIX_LEN]
    price = p.get("price")
    price_hit = False
    if price is not None:
        # 命中任一常见标价形态：$41.48 / 41.48 / USD 41.48
        price_str = f"{price:.2f}" if isinstance(price, float) else str(price)
        price_hit = (price_str in text) or (f"${price_str}" in text)
    if not title_head:
        return {"suggestion": None, "reason": "payload 缺标题，无法自动比对", "evidence": ""}
    if title_head not in text:
        return {"suggestion": False, "reason": "商品标题前缀未出现在当前页面（可能下架/改版）", "evidence": ""}
    if not price_hit:
        return {"suggestion": None, "reason": "标题命中但记录价格未出现（可能改价），需人工看", "evidence": title_head}
    return {"suggestion": True, "reason": "标题与价格均命中", "evidence": title_head}


_CHECKERS = {"fx": _check_fx, "policy": _check_policy, "item": _check_item}


async def assist_one(row: dict) -> dict:
    """返回 assistant_check 字典；只读页面，不写 human_verified。"""
    base = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    allowed, why = await robots_allowed(row["url"])
    if not allowed:
        return base | {"suggestion": None, "reason": f"robots 不允许重抓（{why}）；原判定基于首次合规抓取", "evidence": ""}
    try:
        if row["kind"] == "fx":
            # 汇率 API 返回小体积 JSON，fetch() 的 _looks_blocked 会把
            # "字节 < 4000" 误判成拦截页而触发浏览器兜底——这里直接走快路径。
            result = await fetch_http(row["url"])
        else:
            result = await fetch(row["url"])
    except Exception as exc:
        return base | {"suggestion": None, "reason": f"重抓失败：{type(exc).__name__}: {exc}", "evidence": ""}
    status, html = result.get("status"), result.get("html") or ""
    if status != 200 or not html.strip():
        return base | {"suggestion": None, "reason": f"重抓非 200/空页（status={status}, {len(html)}B, {result.get('channel')}）",
                       "evidence": ""}
    marker = _looks_challenge(html)
    if marker:
        return base | {"suggestion": None,
                       "reason": f"重抓命中反爬挑战页（标记：{marker}），无法核对；原判定基于首次合规抓取",
                       "evidence": ""}
    checker = _CHECKERS.get(row["kind"])
    if checker is None:
        return base | {"suggestion": None, "reason": f"未知类型 {row['kind']}", "evidence": ""}
    out = checker(row, html)
    out["channel"] = result.get("channel")
    return base | out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="本轮最多预检多少条")
    args = ap.parse_args()

    rows = json.loads(SAMPLE_OUT.read_text(encoding="utf-8"))
    todo = [r for r in rows if r.get("human_verified") is None
            and not r.get("assistant_check")]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("没有待预检条目（全部已有建议或已人工回访）。")
        return 0

    print(f"预检 {len(todo)}/{len(rows)} 条（只写 assistant_check，不碰 human_verified）\n")
    t0 = time.time()
    for i, row in enumerate(todo, 1):
        row["assistant_check"] = await assist_one(row)
        s = row["assistant_check"]
        mark = {True: "✓ 支持", False: "✗ 不支持", None: "? 需人工"}[s["suggestion"]]
        print(f"[{i}/{len(todo)}] #{row['provenance_id']} {row['kind']:<6} {mark}  {s['reason'][:70]}")
        SAMPLE_OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    shutdown_browser()
    n_true = sum(1 for r in todo if r["assistant_check"]["suggestion"] is True)
    n_false = sum(1 for r in todo if r["assistant_check"]["suggestion"] is False)
    n_none = len(todo) - n_true - n_false
    print(f"\n用时 {time.time() - t0:.0f}s | 建议：{n_true} 支持 / {n_false} 不支持 / {n_none} 需人工")
    print("下一步：python scripts/human_baseline.py spotcheck  —— 逐条看摘录按 y/n 确认（建议仅供核对，判定权在人）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
