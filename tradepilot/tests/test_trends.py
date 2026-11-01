# -*- coding: utf-8 -*-
"""看板趋势数据的回归测试（M4 数据看板）。

守的红线：
1. **补零必须是零，不是缺数据**：没任务/没消耗的天在序列里显式为 0，
   图表画的平线 = 真的没有，不是数据被裁；
2. **只有真实日期进序列**：started_at 解析失败的任务不计入也不猜日期；
3. **天数有界**：days 夹在合理范围——无限放大等于请求全量扫描。

模块级 CONFIG 替换的老坑（见 test_pg_export）：多个测试文件都在模块级
替换会按收集顺序互相覆盖。这里统一 setUpModule 运行时接管 + tearDownModule
恢复，测试类内部再用独立目录隔离。
"""
import dataclasses
import json
import os
import tempfile
import unittest
from datetime import date

from app.config import CONFIG  # noqa: E402
from app import provenance, quota, store  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-trends-test-")
_taken: dict[str, object] = {}


def setUpModule():
    _taken["store"] = store.CONFIG
    _taken["quota"] = quota.CONFIG
    _taken["provenance"] = provenance.CONFIG
    store.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
    quota.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
    provenance.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
    quota.init_db()


def tearDownModule():
    store.CONFIG = _taken["store"]
    quota.CONFIG = _taken["quota"]
    provenance.CONFIG = _taken["provenance"]


class TestMissionsByDay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = store.CONFIG
        cls._dir = tempfile.mkdtemp(prefix="tradepilot-trends-case-")
        store.CONFIG = dataclasses.replace(CONFIG, data_dir=cls._dir, db_path="")

    @classmethod
    def tearDownClass(cls):
        store.CONFIG = cls._orig

    def _save(self, mid: str, started: str) -> None:
        mdir = os.path.join(self._dir, "missions")
        os.makedirs(mdir, exist_ok=True)
        with open(os.path.join(mdir, f"{mid}.json"), "w", encoding="utf-8") as fh:
            json.dump({"mission_id": mid, "started_at": started}, fh)

    def test_today_and_zero_fill(self):
        today = date.today().isoformat()
        self._save("mission-20261101-090001", f"{today}T09:00:00")
        self._save("mission-20261101-090002", f"{today}T10:00:00")
        hist = store.missions_by_day(days=7)
        self.assertEqual(len(hist), 7)
        self.assertEqual(hist[-1], {"day": today, "missions": 2})
        self.assertEqual(hist[0]["missions"], 0)        # 六天前：真实的 0

    def test_bad_started_at_not_counted(self):
        """started_at 坏了的任务不计入——宁可少算不可编日期。"""
        self._save("mission-20261101-090003", "not-a-date")
        hist = store.missions_by_day(days=3)
        self.assertEqual(sum(h["missions"] for h in hist), 0)

    def test_days_capped_for_scan(self):
        self.assertEqual(len(store.missions_by_day(days=30)), 30)


class TestQuotaHistory(unittest.TestCase):
    def setUp(self):
        with quota._db() as conn:
            conn.execute("DELETE FROM quota_usage")

    def test_zero_fill_and_real_days(self):
        quota.consume("tenant-A", 2.0)
        hist = quota.history(days=7)
        self.assertEqual(len(hist), 7)
        self.assertEqual(hist[-1]["weight"], 2.0)       # 今天
        self.assertEqual(hist[0]["weight"], 0.0)        # 六天前：上线前如实 0
        days = [h["day"] for h in hist]
        self.assertEqual(len(set(days)), 7)             # 每天一个槽，不重复

    def test_days_capped(self):
        self.assertEqual(len(quota.history(days=3)), 3)


class TestProvenanceKindStats(unittest.TestCase):
    def setUp(self):
        # 每个用例全新库：上一条的 record 不能污染下一条的"空库"断言
        self._orig = provenance.CONFIG
        self._dir = tempfile.mkdtemp(prefix="tradepilot-trends-pg-")
        provenance.CONFIG = dataclasses.replace(CONFIG, data_dir=self._dir,
                                                db_path="")

    def tearDown(self):
        provenance.CONFIG = self._orig

    def test_empty_db_empty_dict(self):
        self.assertEqual(provenance.kind_stats(), {})

    def test_counts_by_kind(self):
        provenance.record("page", "https://a.example/x", {"v": 1})
        provenance.record("page", "https://a.example/y", {"v": 2})
        provenance.record("item", "https://b.example/z", {"v": 3})
        kinds = provenance.kind_stats()
        self.assertEqual(kinds["page"], 2)
        self.assertEqual(kinds["item"], 1)


if __name__ == "__main__":
    unittest.main()
