# -*- coding: utf-8 -*-
"""租户 LLM 配额的回归测试（M4-计费地基）。

守的红线：
1. **超限不扣**：allowed=False 的那次请求不能产生任何用量变化——
   "拒绝了还记账"是账目事故；
2. **权重是累计的**：pipeline=4 意味着 100 的配额跑 25 次全链路就停，
   不是每次都从 0 算；
3. **匿名与关闭态放行**：匿名请求无租户概念；limit=0 = 配额关闭，
   两种情况都不拦——但都要有明确标记，不能默默放行让人误以为有隔离；
4. **天边界切换归零**：新的一天是新账本（mock 日期验证，不真等零点）。
"""
import dataclasses
import tempfile
import unittest
from unittest import mock

from app.config import CONFIG  # noqa: E402
from app import quota  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-quota-test-")
quota.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
quota.init_db()


class TestConsume(unittest.TestCase):
    def setUp(self):
        # 每个用例清表：tenant A/B 的账本互不相连
        with quota._db() as conn:
            conn.execute("DELETE FROM quota_usage")
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _set_limit(self, n: int):
        p = mock.patch.object(quota, "default_daily_limit", return_value=n)
        p.start()
        self._patches.append(p)

    def test_consume_accumulates_weight(self):
        self.assertTrue(quota.consume("tenant-A", 4.0)["allowed"])
        self.assertTrue(quota.consume("tenant-A", 4.0)["allowed"])
        s = quota.status("tenant-A")
        self.assertEqual(s["used"], 8.0)
        self.assertEqual(s["remaining"], 92.0)

    def test_over_limit_rejected_and_not_charged(self):
        """超限拒绝，且拒绝那次不计账——拒绝还记账是账目事故。"""
        self._set_limit(10)
        self.assertTrue(quota.consume("tenant-A", 8.0)["allowed"])
        denied = quota.consume("tenant-A", 4.0)
        self.assertFalse(denied["allowed"])
        # 拒绝后用量仍是 8，不是 12
        self.assertEqual(quota.status("tenant-A")["used"], 8.0)

    def test_partial_headroom_still_allows_smaller_request(self):
        self._set_limit(10)
        self.assertTrue(quota.consume("tenant-A", 8.0)["allowed"])
        q = quota.consume("tenant-A", 2.0)
        self.assertTrue(q["allowed"])       # 剩 2，正好够
        self.assertFalse(quota.consume("tenant-A", 1.0)["allowed"])  # 0 余量

    def test_tenants_ledgers_independent(self):
        self._set_limit(4)
        self.assertTrue(quota.consume("tenant-A", 4.0)["allowed"])
        q = quota.consume("tenant-B", 1.0)
        self.assertTrue(q["allowed"])
        self.assertEqual(q["used"], 1.0)

    def test_day_boundary_resets(self):
        self.assertTrue(quota.consume("tenant-A", 4.0)["allowed"])
        # 直接改库里的 day 模拟跨天：旧行留在昨天，今天是新账本
        with quota._db() as conn:
            conn.execute("UPDATE quota_usage SET day = '2020-01-01'")
        s = quota.status("tenant-A")
        self.assertEqual(s["used"], 0.0)

    def test_refund_floors_at_zero(self):
        """回滚不能把账本滚成负数。"""
        self.assertTrue(quota.consume("tenant-A", 1.0)["allowed"])
        quota.refund("tenant-A", 5.0)
        self.assertEqual(quota.status("tenant-A")["used"], 0.0)

    def test_refund_noop_for_anonymous(self):
        quota.refund("", 5.0)       # 不抛异常即通过

    def test_anonymous_and_disabled_pass_through(self):
        """匿名（无租户）与 limit=0（配额关闭）都放行，但口径明示。"""
        q = quota.consume("", 4.0)
        self.assertTrue(q["allowed"])
        self.assertFalse(q.get("limited", True))
        self._set_limit(0)
        q = quota.consume("tenant-A", 4.0)
        self.assertTrue(q["allowed"])
        self.assertFalse(q["limited"])      # 关闭态要看得出来，不能装作有隔离


class TestStats(unittest.TestCase):
    def setUp(self):
        with quota._db() as conn:
            conn.execute("DELETE FROM quota_usage")

    def test_stats_counts_active_tenants(self):
        quota.consume("tenant-A", 4.0)
        quota.consume("tenant-A", 1.0)
        quota.consume("tenant-B", 2.0)
        s = quota.stats()
        self.assertEqual(s["active_tenants"], 2)
        self.assertEqual(s["total_weight"], 7.0)


if __name__ == "__main__":
    unittest.main()
