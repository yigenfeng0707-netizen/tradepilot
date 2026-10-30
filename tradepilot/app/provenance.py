# Copyright 2026 TradePilot project. Apache-2.0.
"""来源存证（Provenance Store）：作品反幻觉红线的地基。

规则（对应《电商Agent安全评审Checklist》A/C 节）：
- 每一条真实抓取的数据点入库时必须携带 来源 URL + 抓取时间戳 + 内容摘要；
- 下游工具（定价/合规/Listing）只允许消费存证在册的数据点，引用不存在的
  provenance_id 会被闸门拦截（held），从机制上杜绝编造；
- 页面展示数值由服务端从存证回填，模型/前端只传 ID。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import CONFIG

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG.db_file())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,            -- page / item / price / policy
            url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload TEXT NOT NULL          -- JSON: 该数据点的真实字段
        )"""
    )
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """连接即用即关：`with conn` 只提交事务，不关闭句柄，长跑会漏文件描述符。"""
    conn = _conn()
    try:
        with conn:          # 事务：异常自动回滚
            yield conn
    finally:
        conn.close()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def record(kind: str, url: str, payload: dict[str, Any], raw_text: str | None = None) -> int:
    """登记一条真实抓取的数据点，返回 provenance_id。"""
    digest = content_hash(raw_text or json.dumps(payload, ensure_ascii=False, sort_keys=True))
    with _LOCK, _db() as conn:
        cur = conn.execute(
            "INSERT INTO provenance (kind, url, fetched_at, content_hash, payload) VALUES (?,?,?,?,?)",
            (kind, url, _now(), digest, json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def exists(ids: list[int]) -> set[int]:
    """来源闸门的查询面：返回其中真实在册的 id。"""
    if not ids:
        return set()
    marks = ",".join("?" for _ in ids)
    with _LOCK, _db() as conn:
        rows = conn.execute(
            f"SELECT id FROM provenance WHERE id IN ({marks})", [int(i) for i in ids]
        ).fetchall()
    return {int(r[0]) for r in rows}


def get(pid: int) -> dict[str, Any] | None:
    with _LOCK, _db() as conn:
        row = conn.execute(
            "SELECT kind, url, fetched_at, payload FROM provenance WHERE id=?", (pid,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": pid,
        "kind": row[0],
        "url": row[1],
        "fetched_at": row[2],
        "payload": json.loads(row[3]),
    }


def stats() -> dict[str, Any]:
    with _LOCK, _db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
        urls = conn.execute("SELECT COUNT(DISTINCT url) FROM provenance").fetchone()[0]
    return {"total": total, "distinct_sources": urls}
