# Copyright 2026 TradePilot project. Apache-2.0.
"""LLM token 用量计量（M4 计费地基）。

与配额（quota.py）的分工：**配额管"拦"，usage 管"量"**。
- 配额按请求权重（商业单位），余额口径给用户看；
- usage 按 token（成本单位），是定价与账单的原料。两套口径不合并——
  一个是界面语言，一个是成本语言，混在一起两头都说不清。

归属：llm.chat 通过 request_context 读当前租户，**不改任何 agent 签名**。
匿名（未登录/ws）调用记入 `anonymous` 键——成本真实发生，匿名不是免记录。
usage 字段缺失/解析失败如实记 0 token（调用本身仍是计费事件：calls +1）。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .config import CONFIG

DB_NAME = "usage.db"
ANONYMOUS = "anonymous"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def db_file() -> str:
    path = os.path.join(CONFIG.data_dir, DB_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_file(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            tenant_id TEXT NOT NULL,
            day       TEXT NOT NULL,
            calls     INTEGER NOT NULL DEFAULT 0,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, day)
        );
        """)


@contextmanager
def _db():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def record(tenant_id: str | None, prompt_tokens: int, completion_tokens: int) -> None:
    """记账一次 LLM 调用。token 数来自网关响应的 usage 字段；拿不到就 0。"""
    tid = tenant_id or ANONYMOUS
    with _db() as conn:
        conn.execute(
            "INSERT INTO llm_usage (tenant_id, day, calls, prompt_tokens,"
            " completion_tokens, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(tenant_id, day) DO UPDATE SET"
            " calls = calls + 1,"
            " prompt_tokens = prompt_tokens + excluded.prompt_tokens,"
            " completion_tokens = completion_tokens + excluded.completion_tokens,"
            " updated_at = excluded.updated_at",
            (tid, _today(), 1, max(0, int(prompt_tokens)),
             max(0, int(completion_tokens)), _now_iso()))


def extract_usage(response_json: dict[str, Any]) -> tuple[int, int]:
    """从 OpenAI 兼容响应取 usage；缺字段返回 (0, 0)——不猜不编。
    负数在源头钳 0（record 还会再钳一次，防御一致）。"""
    u = response_json.get("usage") if isinstance(response_json, dict) else None
    if not isinstance(u, dict):
        return 0, 0
    try:
        return (max(0, int(u.get("prompt_tokens") or 0)),
                max(0, int(u.get("completion_tokens") or 0)))
    except (TypeError, ValueError):
        return 0, 0


def tenant_stats(tenant_id: str, days: int = 7) -> dict[str, Any]:
    """单个租户的用量（用户自查面）——只看得到自己的账，不是全局账。"""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    tid = tenant_id or ANONYMOUS
    with _db() as conn:
        today_row = conn.execute(
            "SELECT COALESCE(SUM(calls),0) c, COALESCE(SUM(prompt_tokens),0) p,"
            " COALESCE(SUM(completion_tokens),0) k FROM llm_usage"
            " WHERE tenant_id = ? AND day = ?", (tid, _today())).fetchone()
        hist = conn.execute(
            "SELECT day, SUM(calls) c, SUM(prompt_tokens) p,"
            " SUM(completion_tokens) k FROM llm_usage"
            " WHERE tenant_id = ? AND day >= ? GROUP BY day ORDER BY day",
            (tid, (today - timedelta(days=days - 1)).isoformat())).fetchall()
    return {"day": _today(),
            "today": {"calls": int(today_row["c"]),
                      "prompt_tokens": int(today_row["p"]),
                      "completion_tokens": int(today_row["k"])},
            "by_day": [{"day": str(r["day"]), "calls": int(r["c"]),
                        "prompt_tokens": int(r["p"]),
                        "completion_tokens": int(r["k"])} for r in hist]}


def stats(days: int = 7) -> dict[str, Any]:
    """今日 + 近 N 天汇总（运营面）。"""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    with _db() as conn:
        today_row = conn.execute(
            "SELECT COALESCE(SUM(calls),0) c, COALESCE(SUM(prompt_tokens),0) p,"
            " COALESCE(SUM(completion_tokens),0) k FROM llm_usage WHERE day = ?",
            (_today(),)).fetchone()
        hist = conn.execute(
            "SELECT day, SUM(calls) c, SUM(prompt_tokens) p,"
            " SUM(completion_tokens) k FROM llm_usage"
            " WHERE day >= ? GROUP BY day ORDER BY day",
            ((today - timedelta(days=days - 1)).isoformat(),)).fetchall()
    return {"day": _today(),
            "today": {"calls": int(today_row["c"]),
                      "prompt_tokens": int(today_row["p"]),
                      "completion_tokens": int(today_row["k"])},
            "by_day": [{"day": str(r["day"]), "calls": int(r["c"]),
                        "prompt_tokens": int(r["p"]),
                        "completion_tokens": int(r["k"])} for r in hist]}
