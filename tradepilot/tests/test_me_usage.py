# -*- coding: utf-8 -*-
"""租户用量自查（M4 计费闭环用户侧）的回归测试。

守的红线：
1. **只看得到自己的账**：tenant_stats 只返回该租户的 token 明细——
   别人的用量出现在我的账单里是事故；
2. **未认证 401**：匿名没有账可查。
"""
import dataclasses
import tempfile
import unittest
from types import SimpleNamespace

from app.config import CONFIG  # noqa: E402
from app import auth, quota, usage  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-meusage-test-")
usage.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
quota.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")


def setUpModule():
    usage.init_db()
    auth.init_db()
    quota.init_db()


class TestTenantStats(unittest.TestCase):
    def setUp(self):
        with usage._db() as conn:
            conn.execute("DELETE FROM llm_usage")

    def test_only_own_ledger(self):
        usage.record("tenant-A", 100, 50)
        usage.record("tenant-B", 999, 999)
        s = usage.tenant_stats("tenant-A")
        self.assertEqual(s["today"]["calls"], 1)
        self.assertEqual(s["today"]["prompt_tokens"], 100)   # 不是 1099
        self.assertEqual(s["today"]["completion_tokens"], 50)

    def test_empty_ledger_is_zero(self):
        s = usage.tenant_stats("tenant-nobody")
        self.assertEqual(s["today"]["calls"], 0)
        self.assertEqual(s["by_day"], [])        # 没有记录就没有天数槽，不补空账

    def test_register_end_to_end_ledger_isolated(self):
        """真实注册的租户（通过 auth 层拿 tenant_id）也能对上自己的账。"""
        reg = auth.register("meu@example.com", "password123", "自查公司")
        self.assertTrue(reg.get("ok"), reg)
        usage.record(reg["tenant_id"], 10, 20)
        s = usage.tenant_stats(reg["tenant_id"])
        self.assertEqual(s["today"]["prompt_tokens"], 10)


class TestMeUsageEndpoint(unittest.TestCase):
    def test_unauthenticated_is_401(self):
        """匿名没有账可查——端点逻辑虽薄，401 分支用真函数验证。"""
        import asyncio
        from app import main as M
        from fastapi import HTTPException
        req = SimpleNamespace(headers={})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(M.me_usage(req))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_authenticated_returns_own_quota_and_usage(self):
        import asyncio
        from app import main as M
        reg = auth.register("meu2@example.com", "password123", "自查二")
        self.assertTrue(reg.get("ok"), reg)
        token = auth.issue_access_token(reg["user_id"], reg["tenant_id"])
        usage.record(reg["tenant_id"], 7, 3)
        req = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
        out = asyncio.run(M.me_usage(req))
        self.assertEqual(out["tenant_id"], reg["tenant_id"])
        self.assertEqual(out["quota"]["limit"], quota.default_daily_limit())
        self.assertEqual(out["llm_usage"]["today"]["prompt_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
