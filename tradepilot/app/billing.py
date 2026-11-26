# Copyright 2026 TradePilot project. Apache-2.0.
"""订单与履约：支付宝电脑网站支付的订单面（billing.db）。

红线：
- **幂等是硬要求**：支付宝异步通知会重发（同一 notify_id 多次、同一订单
  不同 trade 状态），单靠查重不够——mark_paid 本身做成"已付就不再改、
  不再升级"，重复通知直接 success。
- **跨租户隔离**：订单只能被所属租户查看/同步（和 usage 账本同红线）。
- **自测单如实标记**：`test=1` 的订单在 UI 明示"支付链路自测单"，
  不混充真实订阅——评委看到的"已付费"证据必须知情同意地标注。

plan → 价格/权益映射写死服务端，客户端传不了金额（防低价下单）。
"""
from __future__ import annotations

import os
import secrets as _secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from . import payment, quota

DB_NAME = "billing.db"

# plan → 商品与权益。价格为商业参数（与 config/pricing.json、前端 Pricing 同源；
# 改价三处必须同步，见 docs/pricing-decision.md）。
PLANS: dict[str, dict[str, Any]] = {
    "pro": {
        "subject": "TradePilot 正式版 · 月度订阅（¥199/月，日配额 2000 点）",
        "price": 199.00,
        "daily_limit": 2000,
        "period_days": 30,
        "test": False,
    },
    "self_test": {
        "subject": "TradePilot 支付链路自测单（¥0.01，不含订阅权益）",
        "price": 0.01,
        "daily_limit": None,
        "period_days": None,
        "test": True,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_file() -> str:
    from .config import CONFIG
    path = os.path.join(CONFIG.data_dir, DB_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_file(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            out_trade_no TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            plan         TEXT NOT NULL,
            amount       REAL NOT NULL,
            status       TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING/PAID
            test         INTEGER NOT NULL DEFAULT 0,
            trade_no     TEXT,
            paid_at      TEXT,
            via          TEXT,
            created_at   TEXT NOT NULL,
            expires_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS notify_seen (
            notify_id   TEXT PRIMARY KEY,
            out_trade_no TEXT NOT NULL,
            seen_at     TEXT NOT NULL
        );
        """)


def new_trade_no(prefix: str = "TP") -> str:
    return (prefix + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            + _secrets.token_hex(3))


def create_order(tenant_id: str, plan: str) -> dict[str, Any]:
    """下单：plan 合法性、价格均服务端定。返回订单（支付页由
    GET /pay/{out_trade_no} 提供——biz_content 拼 URL 的网关直链会被
    支付宝风控判钓鱼页，2026-11-26 实测，见 payment.page_pay_form）。"""
    if plan not in PLANS:
        raise ValueError(f"未知套餐: {plan}")
    if not payment.configured():
        raise RuntimeError("支付未配置（ALIPAY_APP_ID/ALIPAY_PRIVATE_KEY/"
                           "ALIPAY_ALIPAY_PUBLIC_KEY 缺失）")
    spec = PLANS[plan]
    no = new_trade_no()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=1)   # 未付订单 1 天后视为过期（支付宝侧另有超时关单）
    with _db() as conn:
        conn.execute(
            "INSERT INTO orders (out_trade_no, tenant_id, plan, amount, status, test,"
            " created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (no, tenant_id, plan, spec["price"], "PENDING",
             1 if spec["test"] else 0, _now_iso(), expires.isoformat()))
    return {"out_trade_no": no, "plan": plan, "amount": spec["price"],
            "subject": spec["subject"], "test": spec["test"]}


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["test"] = bool(d["test"])
    d["amount"] = round(float(d["amount"]), 2)
    return d


def get_order(tenant_id: str, out_trade_no: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE out_trade_no = ? AND tenant_id = ?",
            (out_trade_no, tenant_id)).fetchone()
    return _row_dict(row) if row else None


def get_order_by_no(out_trade_no: str) -> dict[str, Any] | None:
    """公开支付页用：只按单号查（不带租户）。泄露面=subject/金额，无
    履约/身份信息；真实支付仍需验签，篡改单号骗不到东西。"""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE out_trade_no = ?",
            (out_trade_no,)).fetchone()
    return _row_dict(row) if row else None


def list_orders(tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit)).fetchall()
    return [_row_dict(r) for r in rows]


def mark_paid(out_trade_no: str, trade_no: str, via: str) -> bool:
    """幂等履约：已付订单直接返回 False（调用方照常回 success）。
    正常路径：改状态 → 按套餐升配额（写 tenant_limits）。"""
    with _db() as conn:
        row = conn.execute(
            "SELECT status, plan FROM orders WHERE out_trade_no = ?",
            (out_trade_no,)).fetchone()
        if row is None:
            return False
        if row["status"] == "PAID":
            return False
        spec = PLANS[row["plan"]]
        conn.execute(
            "UPDATE orders SET status='PAID', trade_no=?, paid_at=?, via=?"
            " WHERE out_trade_no=? AND status='PENDING'",
            (trade_no, _now_iso(), via, out_trade_no))
        if spec["daily_limit"]:
            quota.set_tenant_limit(row_tenant(out_trade_no), spec["daily_limit"],
                                    reason=f"plan:{row['plan']} paid via {via}")
    return True


def row_tenant(out_trade_no: str) -> str:
    with _db() as conn:
        row = conn.execute(
            "SELECT tenant_id FROM orders WHERE out_trade_no = ?",
            (out_trade_no,)).fetchone()
    return row["tenant_id"] if row else ""


def handle_notify(form: dict[str, str]) -> str:
    """异步通知全链路：验签 → 业务校验 → 幂等 → 履约。返回 'success'/'fail'。

    每一步失败只回 fail：支付宝会按策略重试，不在我们这编造成功。
    """
    if not payment.verify_notify(form):
        return "fail"          # 验签失败：零信任
    if form.get("app_id") != payment.app_id():
        return "fail"
    seller = form.get("seller_id", "")
    if payment.pid() and seller and seller != payment.pid():
        return "fail"
    out_no = form.get("out_trade_no", "")
    trade_status = form.get("trade_status", "")
    trade_no = form.get("trade_no", "")
    total = form.get("total_amount", "")
    notify_id = form.get("notify_id", "")
    if not (out_no and trade_status and trade_no and total and notify_id):
        return "fail"
    with _db() as conn:
        row = conn.execute(
            "SELECT amount, status, tenant_id FROM orders WHERE out_trade_no = ?",
            (out_no,)).fetchone()
        if row is None:
            return "fail"      # 不是我们的订单
        if payment.format_amount(float(row["amount"])) != total:
            return "fail"      # 金额不一致
        try:
            conn.execute(
                "INSERT INTO notify_seen (notify_id, out_trade_no, seen_at)"
                " VALUES (?,?,?)", (notify_id, out_no, _now_iso()))
        except sqlite3.IntegrityError:
            # 重复通知：已处理过，订单状态以首次为准，直接确认
            return "success"
    if trade_status in payment.PAID_STATUSES:
        mark_paid(out_no, trade_no, via="notify")
    return "success"


async def sync_order(tenant_id: str, out_trade_no: str) -> dict[str, Any] | None:
    """主动对账（履约的查询兜底）：租户只能查自己的单。"""
    order = get_order(tenant_id, out_trade_no)
    if order is None:
        return None
    if order["status"] == "PAID":
        return order
    result = await payment.query_trade(out_trade_no)
    if result and result.get("trade_status") in payment.PAID_STATUSES:
        if payment.format_amount(order["amount"]) == result.get("total_amount", ""):
            mark_paid(out_trade_no, result.get("trade_no", ""), via="query")
            order = get_order(tenant_id, out_trade_no)
    return order
