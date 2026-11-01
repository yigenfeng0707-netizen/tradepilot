# Copyright 2026 TradePilot project. Apache-2.0.
"""店铺授权关系（M4 多租户安全收口）。

回答一个问题：**这个店铺是谁授权的？**

- `TOKENS`（marketplace_auth）是进程内 token 缓存，key 是 (platform,
  shop_id)——它管"token 还新鲜吗"，不管"店铺属于谁"；
- 这里持久化 (tenant_id, platform, shop_id) 授权关系——"哪个店铺授权过"
  从此可考（这正是 shopee.exchange_auth_code docstring 里"静默入库会
  不可考"顾虑的答案：**关系显式记录**，token 缓存与归属记录分离）；
- 店铺级调用的租户校验用 `is_authorized`：租户 A 用 B 授权的 shop_id
  构造调用会被拒绝。

存储 SQLite `shops.db`（data_dir 下，与 auth/quota/usage 并列，
上 Postgres 时随认证表一起迁）。

已知边界（联调确认项）：Lazada /auth/token/create 的响应可能不含
shop_id 字段（有 account/country_codes）——shop_id 为空时记录空串
占位（租户级单店铺），真实形态联调后在 runbook 确认，**不猜字段**。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .config import CONFIG

DB_NAME = "shops.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        CREATE TABLE IF NOT EXISTS shop_authorizations (
            tenant_id  TEXT NOT NULL,
            platform   TEXT NOT NULL,
            shop_id    TEXT NOT NULL DEFAULT '',
            authorized_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, platform, shop_id)
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


def authorize(tenant_id: str, platform: str, shop_id: str = "") -> None:
    """记录一次授权关系。重复授权幂等（INSERT OR IGNORE + 刷新时间）。"""
    if not tenant_id or not platform:
        return
    with _db() as conn:
        conn.execute(
            "INSERT INTO shop_authorizations"
            " (tenant_id, platform, shop_id, authorized_at) VALUES (?,?,?,?)"
            " ON CONFLICT(tenant_id, platform, shop_id)"
            " DO UPDATE SET authorized_at = excluded.authorized_at",
            (tenant_id, platform, shop_id, _now_iso()))


def shops_of(tenant_id: str, platform: str | None = None) -> list[dict[str, Any]]:
    """租户已授权的店铺清单。"""
    if not tenant_id:
        return []
    sql = ("SELECT platform, shop_id, authorized_at FROM shop_authorizations"
           " WHERE tenant_id = ?")
    args: list[Any] = [tenant_id]
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    with _db() as conn:
        rows = conn.execute(sql + " ORDER BY authorized_at DESC", args).fetchall()
    return [{"platform": r["platform"], "shop_id": r["shop_id"],
             "authorized_at": r["authorized_at"]} for r in rows]


def is_authorized(tenant_id: str, platform: str, shop_id: str) -> bool:
    """租户校验闸门：店铺级调用前查这个。"""
    if not tenant_id:
        return False
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM shop_authorizations"
            " WHERE tenant_id = ? AND platform = ? AND shop_id = ?",
            (tenant_id, platform, shop_id)).fetchone()
    return row is not None
