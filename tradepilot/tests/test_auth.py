# -*- coding: utf-8 -*-
"""认证模块的回归测试（M3）。

守的红线：
1. **密码绝不落可逆形式**：库里只有 argon2id 散列；导出接口不含散列与令牌；
2. **刷新令牌用一次换一次**：rotation 后旧令牌作废，重放失败；
3. **登录不泄露账号存在性**：邮箱不存在与密码错误同样失败、同样措辞；
4. **JWT 载荷不放敏感信息**：邮箱/公司名不进 JWT（base64 人人可读）；
5. **登录限速**：同 IP 连续失败锁定，防撞库。
"""
import dataclasses
import secrets
import sqlite3
import tempfile
import unittest

# 给 auth 模块一份独立的 Config（只换 data_dir），不污染全局单例：
# pytest 单进程收集所有测试，直接改 CONFIG.data_dir 会连累别的测试模块。
from app.config import CONFIG  # noqa: E402
from app import auth  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-auth-test-")
auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir)


def setUpModule():
    auth.init_db()


class TestPassword(unittest.TestCase):
    def test_hash_is_argon2id_and_not_reversible(self):
        h = auth.hash_password("correct horse battery")
        self.assertTrue(h.startswith("$argon2id$"))
        self.assertNotIn("correct horse", h)

    def test_verify_roundtrip_and_wrong_password(self):
        h = auth.hash_password("s3cret-pass")
        self.assertTrue(auth.verify_password("s3cret-pass", h))
        self.assertFalse(auth.verify_password("wrong", h))

    def test_password_policy_floor_is_8(self):
        self.assertIsNotNone(auth.password_problem("abc12"))
        self.assertIsNotNone(auth.password_problem("a" * 7))
        self.assertIsNone(auth.password_problem("a" * 8))
        self.assertIsNotNone(auth.password_problem("a" * 129))

    def test_hash_never_written_plaintext(self):
        """注册后库里必须只有散列，明文密码一个字符都不许留。"""
        auth.register("plain@t.cn", "super-secret-9")
        conn = sqlite3.connect(auth.db_file())
        row = conn.execute("SELECT password_hash FROM users WHERE email=?",
                           ("plain@t.cn",)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertNotIn("super-secret", row[0])
        self.assertTrue(row[0].startswith("$argon2id$"))


class TestRegisterAndLogin(unittest.IsolatedAsyncioTestCase):
    def test_register_creates_tenant_and_user(self):
        r = auth.register("a@t.cn", "password-8", "Acme")
        self.assertTrue(r["ok"])
        info = auth.user_info(r["user_id"])
        self.assertEqual(info["company"], "Acme")
        self.assertEqual(info["tenant_id"], r["tenant_id"])

    def test_duplicate_email_rejected(self):
        auth.register("dup@t.cn", "password-8")
        r = auth.register("dup@t.cn", "password-8b")
        self.assertFalse(r["ok"])
        self.assertIn("已注册", r["reason"])

    def test_weak_password_rejected_before_touching_db(self):
        r = auth.register("weak@t.cn", "short")
        self.assertFalse(r["ok"])
        self.assertIn("8 位", r["reason"])
        conn = sqlite3.connect(auth.db_file())
        n = conn.execute("SELECT COUNT(*) FROM users WHERE email='weak@t.cn'").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_authenticate_success_and_failure(self):
        auth.register("ok@t.cn", "password-8")
        self.assertIsNotNone(auth.authenticate("ok@t.cn", "password-8"))
        self.assertIsNone(auth.authenticate("ok@t.cn", "wrong-pass"))
        self.assertIsNone(auth.authenticate("ghost@t.cn", "password-8"))

    def test_email_is_normalized(self):
        auth.register("Case@T.CN", "password-8")
        self.assertIsNotNone(auth.authenticate("case@t.cn", "password-8"))


class TestTokens(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 测试类共享同一个库，邮箱必须唯一，否则注册撞唯一键
        self.email = f"tok-{secrets.token_hex(4)}@t.cn"
        r = auth.register(self.email, "password-8")
        self.assertTrue(r["ok"], r)
        self.user_id = r["user_id"]
        self.tenant_id = r["tenant_id"]

    def test_access_token_roundtrip(self):
        token = auth.issue_access_token(self.user_id, self.tenant_id)
        payload = auth.decode_access_token(token)
        self.assertEqual(payload["sub"], self.user_id)
        self.assertEqual(payload["tenant"], self.tenant_id)

    def test_jwt_payload_has_no_sensitive_fields(self):
        """JWT 是 base64 不是加密，邮箱/公司名不许放进去。"""
        token = auth.issue_access_token(self.user_id, self.tenant_id)
        self.assertNotIn("@", token)          # 邮箱至少会留下 @ 的痕迹
        import base64
        import json as j
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = j.loads(base64.urlsafe_b64decode(payload_b64))
        self.assertNotIn("email", payload)
        self.assertNotIn("company", payload)

    def test_expired_token_rejected(self):
        token = auth.issue_access_token(self.user_id, self.tenant_id, ttl_s=-10)
        self.assertIsNone(auth.decode_access_token(token))

    def test_refresh_token_rotation(self):
        pair = auth.session_pair(self.user_id, self.tenant_id)
        rt = pair["refresh_token"]
        rotated = auth.rotate_refresh_token(rt)
        self.assertIsNotNone(rotated)
        # 旧令牌必须立即失效——这是检测令牌被盗的机制
        self.assertIsNone(auth.rotate_refresh_token(rt))
        # 新令牌可以继续轮换
        self.assertIsNotNone(auth.rotate_refresh_token(rotated[1]))

    def test_refresh_token_stored_hashed(self):
        pair = auth.session_pair(self.user_id, self.tenant_id)
        conn = sqlite3.connect(auth.db_file())
        rows = [r[0] for r in conn.execute("SELECT token_hash FROM refresh_tokens")]
        conn.close()
        self.assertNotIn(pair["refresh_token"], rows)

    def test_revoked_token_cannot_rotate(self):
        rt = auth.session_pair(self.user_id, self.tenant_id)["refresh_token"]
        self.assertTrue(auth.revoke_refresh_token(rt))
        self.assertIsNone(auth.rotate_refresh_token(rt))

    def test_current_user_from_bearer(self):
        token = auth.issue_access_token(self.user_id, self.tenant_id)
        user = auth.current_user(f"Bearer {token}")
        self.assertEqual(user["user_id"], self.user_id)
        self.assertEqual(user["company"], "")

    def test_current_user_from_x_auth_token(self):
        """魔搭网关会拦截所有 Bearer 方案的 Authorization 头（实测只有 Bearer
        被拦，Basic/自定义头放行），前端改用 X-Auth-Token——后端必须两种都认。"""
        token = auth.issue_access_token(self.user_id, self.tenant_id)
        user = auth.current_user(None, x_auth_token=token)
        self.assertEqual(user["user_id"], self.user_id)
        # 两个头同时给时任意一个有效即可
        user2 = auth.current_user("Bearer not-a-token", x_auth_token=token)
        self.assertEqual(user2["user_id"], self.user_id)

    def test_current_user_rejects_bad_header(self):
        token = auth.issue_access_token(self.user_id, self.tenant_id)
        # 合法 token 作为对照：真正被拒的应是格式错误/空的头
        self.assertEqual(auth.current_user(f"Bearer {token}")["user_id"],
                         self.user_id)
        self.assertIsNone(auth.current_user(None))
        self.assertIsNone(auth.current_user("Basic abc"))
        self.assertIsNone(auth.current_user("Bearer not-a-token"))
        self.assertIsNone(auth.current_user(None, x_auth_token="not-a-token"))
        self.assertIsNone(auth.current_user(None, x_auth_token="  "))


class TestLoginRateLimit(unittest.IsolatedAsyncioTestCase):
    def test_lock_after_repeated_failures(self):
        ip = "203.0.113.7"
        for _ in range(auth.LOGIN_MAX_FAILS):
            auth.record_login_fail(ip)
        locked = auth.login_locked(ip)
        self.assertGreater(locked, 0)
        self.assertLessEqual(locked, auth.LOGIN_LOCK_S)

    def test_success_clears_failures(self):
        ip = "198.51.100.9"
        auth.record_login_fail(ip)
        auth.record_login_fail(ip)
        auth.clear_login_fails(ip)
        self.assertEqual(auth.login_locked(ip), 0)

    def test_old_failures_expire(self):
        ip = "192.0.2.5"
        auth.record_login_fail(ip)
        # 把失败时间戳搬到窗口之外
        auth._login_fails[ip] = [t - auth.LOGIN_LOCK_S - 1 for t in auth._login_fails[ip]]
        self.assertEqual(auth.login_locked(ip), 0)


class TestExportSafety(unittest.IsolatedAsyncioTestCase):
    def test_export_has_no_secrets(self):
        auth.register("exp@t.cn", "password-8")
        data = auth.export_all()
        blob = str(data)
        self.assertNotIn("$argon2id$", blob)
        self.assertNotIn("password", blob.lower().replace("password-8", ""))
        self.assertFalse(any("password_hash" in str(u) for u in data["users"]))


if __name__ == "__main__":
    unittest.main()
