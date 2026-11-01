# Copyright 2026 TradePilot project. Apache-2.0.
"""认证与多租户（M3）：argon2id 密码散列 + JWT 访问令牌 + 刷新令牌轮换。

为什么从零写而不是"升级"：前端登录页一直在调 `/auth/login`，但后端
从来没有这个接口——之前注册的用户名和**明文密码**直接落在浏览器
localStorage 里。那不是认证，是把密码抄在便利贴上贴在门框上。
本模块落地后，前端的 localStorage 兜底必须删掉。

安全决策（每条都有理由，改之前先想清楚）：

1. **argon2id** 存密码，不存任何可逆形式。`argon2-cffi` 的默认参数
   （time_cost=3, memory_cost=64MB）就是 OWASP 推荐档位，不自行调低——
   调低省的那点 CPU 换来的是离线爆破难度。
2. **访问令牌 30 分钟**，载荷只放 sub/tenant/typ/exp，不放邮箱和公司名
   （JWT 是 base64 不是加密，谁拿到都能读）。
3. **刷新令牌 30 天、落库存哈希、用一次换一次（rotation）**。被偷的
   refresh token 在第一次续期后即失效，重放会撞上 revoked 记录——
   这是能检测"令牌被盗"的最简单机制。
4. **JWT 密钥**：`TRADEPILOT_JWT_SECRET` > `data_dir/jwt_secret`（自动生成，
   0600）。多实例部署必须显式配 env，否则各实例密钥不同、令牌互相不认。
5. **登录限速**：同 IP 连续失败 10 次锁 5 分钟（进程内存态，多实例部署
   应换 Redis，但内存态也远好于没有）。
6. **多租户**：注册即建租户（公司名），user 挂 tenant_id。所有业务数据
   的租户隔离在 M3 后续逐步接入——先把身份层立住，避免一次性大改
   把存证/任务链路改崩。

SQLite 够用的理由：auth 读写频率极低（登录/注册/续期），单文件零运维；
等上 PostgreSQL 时表结构原样迁走即可（见 docs/production-integration.md 3.4）。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from .config import CONFIG

DB_NAME = "auth.db"

ACCESS_TOKEN_TTL_S = 30 * 60          # 30 分钟
REFRESH_TOKEN_TTL_S = 30 * 24 * 3600  # 30 天
JWT_ISSUER = "tradepilot"

# 登录限速：进程内存态。多实例部署换 Redis（键带实例前缀即可迁移）
LOGIN_MAX_FAILS = 10
LOGIN_LOCK_S = 5 * 60
_login_fails: dict[str, list[float]] = {}

_hasher = PasswordHasher()  # argon2id 默认参数，不调低


# ---------- 存储 ----------

def db_file() -> str:
    path = os.path.join(CONFIG.data_dir, DB_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_file(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """建表。幂等：每次启动都跑，已存在的表原样保留。"""
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'owner',
            disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token_hash TEXT PRIMARY KEY,      -- 只存 sha256，明文不落库
            user_id TEXT NOT NULL REFERENCES users(id),
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
        """)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    """密码策略：8 位下限。返回 None 表示合格。

    之前前端写 6 位、后端没有校验。6 位纯小写字母 3 亿种组合，
    单卡离线爆破是分钟级——下限必须抬到 8。
    """
    if len(password or "") < 8:
        return "密码长度不能少于 8 位"
    if len(password) > 128:
        return "密码过长（上限 128 位）"
    return None


# ---------- JWT ----------

def _jwt_secret() -> str:
    env = os.environ.get("TRADEPILOT_JWT_SECRET", "").strip()
    if env:
        return env
    # 开发态自动生成并持久化：进程重启不能让所有用户的令牌作废。
    # 多实例部署必须显式配 env，否则各实例密钥不同、令牌互不认。
    path = os.path.join(CONFIG.data_dir, "jwt_secret")
    try:
        with open(path, encoding="utf-8") as fh:
            secret = fh.read().strip()
        if secret:
            return secret
    except OSError:
        pass
    secret = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
    return secret


def issue_access_token(user_id: str, tenant_id: str,
                       ttl_s: int = ACCESS_TOKEN_TTL_S) -> str:
    now = int(time.time())
    payload = {"iss": JWT_ISSUER, "sub": user_id, "tenant": tenant_id,
               "typ": "access", "iat": now, "exp": now + ttl_s}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    """校验失败（过期/签名错/typ 不对）返回 None，不抛异常——调用方只关心能不能用。"""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"],
                             issuer=JWT_ISSUER)
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload


# ---------- 刷新令牌 ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh_token(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at)"
            " VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, time.time() + REFRESH_TOKEN_TTL_S,
             _now_iso()))
    return token


def rotate_refresh_token(token: str) -> tuple[str, str] | None:
    """用旧 refresh 换新的一对 (access, refresh)。**用一次换一次**。

    令牌无效/过期/已撤销返回 None。旧令牌在这里被标 revoked——
    如果同一个旧令牌被第二次使用，走 revoke 路径并返回 None，
    意味着它大概率被盗，用户重新登录即可。
    """
    h = _token_hash(token or "")
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, revoked FROM refresh_tokens"
            " WHERE token_hash = ?", (h,)).fetchone()
        if row is None:
            return None
        if row["revoked"] or row["expires_at"] < time.time():
            return None
        user = conn.execute(
            "SELECT id, tenant_id, disabled FROM users WHERE id = ?",
            (row["user_id"],)).fetchone()
        if user is None or user["disabled"]:
            return None
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
                     (h,))
    new_refresh = issue_refresh_token(user["id"])
    access = issue_access_token(user["id"], user["tenant_id"])
    return access, new_refresh


def revoke_refresh_token(token: str) -> bool:
    h = _token_hash(token or "")
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE refresh_tokens SET revoked = 1"
            " WHERE token_hash = ? AND revoked = 0", (h,))
        return cur.rowcount > 0


# ---------- 登录限速 ----------

def _client_key(ip: str) -> str:
    return (ip or "unknown").strip() or "unknown"


def login_locked(ip: str) -> int:
    """该 IP 是否处于登录锁定中；返回剩余秒数，0 表示未锁。"""
    now = time.time()
    fails = [t for t in _login_fails.get(_client_key(ip), []) if now - t < LOGIN_LOCK_S]
    _login_fails[_client_key(ip)] = fails
    if len(fails) >= LOGIN_MAX_FAILS:
        return int(LOGIN_LOCK_S - (now - fails[0])) + 1
    return 0


def record_login_fail(ip: str) -> None:
    _login_fails.setdefault(_client_key(ip), []).append(time.time())


def clear_login_fails(ip: str) -> None:
    _login_fails.pop(_client_key(ip), None)


# ---------- 用户 / 租户 ----------

def register(email: str, password: str, company: str = "") -> dict[str, Any]:
    """注册：建租户 + 建用户。邮箱唯一，重复注册返回带 reason 的失败。"""
    email = (email or "").strip().lower()
    company = (company or "").strip()
    problem = password_problem(password)
    if problem:
        return {"ok": False, "reason": problem}
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return {"ok": False, "reason": "邮箱格式不正确"}

    tenant_id = _new_id("tenant")
    user_id = _new_id("user")
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO tenants (id, name, company, created_at) VALUES (?,?,?,?)",
                (tenant_id, company or email.split("@")[0], company, _now_iso()))
            conn.execute(
                "INSERT INTO users (id, tenant_id, email, password_hash, created_at)"
                " VALUES (?,?,?,?,?)",
                (user_id, tenant_id, email, hash_password(password), _now_iso()))
    except sqlite3.IntegrityError:
        return {"ok": False, "reason": "该邮箱已注册，请直接登录"}
    return {"ok": True, "user_id": user_id, "tenant_id": tenant_id, "email": email}


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    """登录成功返回用户信息，失败返回 None。

    邮箱不存在与密码错误**返回同样的 None**：错误信息里区分这两种情况
    等于帮人确认"这个邮箱注册过没有"。
    """
    email = (email or "").strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, email, password_hash, disabled FROM users"
            " WHERE email = ?", (email,)).fetchone()
    if row is None or row["disabled"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {"user_id": row["id"], "tenant_id": row["tenant_id"],
            "email": row["email"]}


def user_info(user_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.email, u.role, u.tenant_id, t.company"
            " FROM users u JOIN tenants t ON t.id = u.tenant_id"
            " WHERE u.id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {"user_id": row["id"], "email": row["email"], "role": row["role"],
            "tenant_id": row["tenant_id"], "company": row["company"]}


def session_pair(user_id: str, tenant_id: str) -> dict[str, Any]:
    return {"access_token": issue_access_token(user_id, tenant_id),
            "refresh_token": issue_refresh_token(user_id),
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_TTL_S}


def current_user(authorization: str | None) -> dict[str, Any] | None:
    """从 Authorization: Bearer <token> 解析当前用户。

    返回 None 表示未认证——由调用方决定是 401 还是匿名降级
    （M3 过渡期业务接口先匿名可用，身份层先立住）。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    payload = decode_access_token(authorization[7:].strip())
    if payload is None:
        return None
    return user_info(str(payload.get("sub") or ""))


def stats() -> dict[str, Any]:
    with _connect() as conn:
        tenants = conn.execute("SELECT COUNT(*) c FROM tenants").fetchone()["c"]
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) c FROM refresh_tokens"
            " WHERE revoked = 0 AND expires_at > ?", (time.time(),)).fetchone()["c"]
    return {"tenants": tenants, "users": users, "active_refresh_tokens": active}


def export_all() -> dict[str, Any]:
    """全量导出（测试/迁移用）。绝不包含密码散列与令牌哈希。"""
    with _connect() as conn:
        tenants = [dict(r) for r in conn.execute("SELECT id, name, company, created_at FROM tenants")]
        users = [dict(r) for r in conn.execute(
            "SELECT id, tenant_id, email, role, created_at FROM users")]
    return {"tenants": tenants, "users": users}


if __name__ == "__main__":
    init_db()
    print(json.dumps(stats(), ensure_ascii=False))
