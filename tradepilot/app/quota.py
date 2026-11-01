# Copyright 2026 TradePilot project. Apache-2.0.
"""租户 LLM 配额（M4-计费地基）。

口径（写死在这里，前端与文档同步）：
- **按请求扣，不数内部 LLM 调用次数**。一次全链路（pipeline）内部会调
  多次 LLM，但商业单位是"一次任务"——按权重扣（pipeline=4、文案=2、
  其余=1，权重表在 main.py），用户看到的余额和账单口径一致。
  按 token 计费等 llm.chat 返回 usage 后再演进，不在本模块猜。
- **只约束登录租户**。匿名请求没有租户概念，继续走运维侧的全局控制
  （限流/LLM 密钥本身的用量上限）——匿名配额是无主之债，扣在谁头上？
- **天边界是 UTC 日期**（与 auth 的时间口径一致）。
- 存储 SQLite `quota.db`（与 auth.db 同目录）。读写频率低、单实例口径；
  上 Postgres 时随认证表一起迁（列清单见 init）。

默认上限：环境变量 `TRADEPILOT_TENANT_DAILY_LLM_LIMIT`，默认 100。
这是**商业参数**不是技术常量——运营改 env 即可，不进代码。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .config import CONFIG

DB_NAME = "quota.db"


def default_daily_limit() -> int:
    raw = os.environ.get("TRADEPILOT_TENANT_DAILY_LLM_LIMIT", "").strip()
    try:
        return max(0, int(raw)) if raw else 100
    except ValueError:
        return 100


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
        CREATE TABLE IF NOT EXISTS quota_usage (
            tenant_id TEXT NOT NULL,
            day       TEXT NOT NULL,           -- UTC 日期
            weight    REAL NOT NULL DEFAULT 0, -- 累计权重
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


def status(tenant_id: str) -> dict[str, Any]:
    """当前用量快照（不扣减）。limit=0 表示配额关闭（不限量），明示口径。"""
    limit = default_daily_limit()
    if not tenant_id:
        return {"limited": False, "note": "匿名请求无租户配额"}
    with _db() as conn:
        row = conn.execute(
            "SELECT weight FROM quota_usage WHERE tenant_id = ? AND day = ?",
            (tenant_id, _today())).fetchone()
    used = float(row["weight"]) if row else 0.0
    return {"limited": limit > 0, "used": used, "limit": limit,
            "remaining": max(0.0, limit - used) if limit > 0 else None}


def consume(tenant_id: str, weight: float = 1.0) -> dict[str, Any]:
    """原子扣减一次请求权重。超限返回 allowed=False，**不扣**。

    原子性靠 SQLite 单写者 + 同一事务里读改写；中间件串行调用本函数，
    无并发竞争面（多实例部署时应换 Postgres 的 UPDATE ... RETURNING）。
    """
    limit = default_daily_limit()
    if not tenant_id or limit <= 0:
        s = status(tenant_id)
        return {"allowed": True, **s}
    with _db() as conn:
        row = conn.execute(
            "SELECT weight FROM quota_usage WHERE tenant_id = ? AND day = ?",
            (tenant_id, _today())).fetchone()
        used = float(row["weight"]) if row else 0.0
        if used + weight > limit:
            return {"allowed": False, "used": used, "limit": limit,
                    "remaining": max(0.0, limit - used)}
        conn.execute(
            "INSERT INTO quota_usage (tenant_id, day, weight, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(tenant_id, day)"
            " DO UPDATE SET weight = weight + excluded.weight,"
            " updated_at = excluded.updated_at",
            (tenant_id, _today(), weight, _now_iso()))
    return {"allowed": True, "used": used + weight, "limit": limit,
            "remaining": max(0.0, limit - used - weight)}


def refund(tenant_id: str, weight: float) -> None:
    """回滚一次扣减（接口用错没进业务时不该收费）。floors at 0。

    只用于中间件对 404/405/422（路由不存在/方法不对/参数校验失败）的
    回滚——这些情况 LLM 成本为零。进了 handler 的失败（业务 4xx/5xx）
    不回滚：LLM 成本已真实发生。
    """
    if not tenant_id:
        return
    with _db() as conn:
        conn.execute(
            "UPDATE quota_usage SET weight = MAX(0, weight - ?), updated_at = ?"
            " WHERE tenant_id = ? AND day = ?",
            (weight, _now_iso(), tenant_id, _today()))


def history(days: int = 14) -> list[dict[str, Any]]:
    """近 N 天每日消耗（看板用）。没有记录的天补 0——图表上的"平线"必须
    是真的没用量，不是缺数据被裁掉。历史自配额上线起有，之前的 days
    天如实为 0（不回填、不估算）。"""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    with _db() as conn:
        rows = conn.execute(
            "SELECT day, SUM(weight) w FROM quota_usage"
            " WHERE day >= ? GROUP BY day",
            ((today - timedelta(days=days - 1)).isoformat(),)).fetchall()
    by_day = {str(r["day"]): float(r["w"]) for r in rows}
    return [{"day": (today - timedelta(days=days - 1 - i)).isoformat(),
             "weight": by_day.get((today - timedelta(days=days - 1 - i)).isoformat(), 0.0)}
            for i in range(days)]


def stats() -> dict[str, Any]:
    """运营侧总览：今天有多少租户在用、消耗多少。"""
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT tenant_id) tenants,"
            " COALESCE(SUM(weight), 0) total"
            " FROM quota_usage WHERE day = ?", (_today(),)).fetchone()
    return {"day": _today(), "active_tenants": int(row["tenants"]),
            "total_weight": float(row["total"]),
            "limit_per_tenant": default_daily_limit()}
