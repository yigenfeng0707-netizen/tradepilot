# -*- coding: utf-8 -*-
"""进程内监控（M3-⑤）的回归测试。

守的红线：
1. **路径参数必须归一**：每个 mission_id / provenance id 一个计数桶的话，
   桶数随业务量无限膨胀，聚合毫无价值；
2. **5xx 才算错误**：4xx 是调用方的错（404、参数错），混进错误率会把
   "系统坏了"和"用户用错了"搅在一起；
3. **告警判定有样本下限**：总请求 < 20 不判 high_error_rate——
   三条请求错一条就报警的系统只会教人忽略告警；
4. **RSS 读不到就 null**：不猜。Linux（生产目标）读 /proc，Windows 开发机如实 null。
"""
import unittest

from app import monitoring


class TestPathNormalization(unittest.TestCase):
    def test_mission_id_normalized(self):
        self.assertEqual(
            monitoring.normalize_path("/api/missions/mission-20261101-090001"),
            "/api/missions/{mission_id}")
        self.assertEqual(
            monitoring.normalize_path("/api/missions/mission-20261101-090001/export"),
            "/api/missions/{mission_id}/export")

    def test_provenance_pid_normalized(self):
        self.assertEqual(monitoring.normalize_path("/api/provenance/12"),
                         "/api/provenance/{pid}")

    def test_plain_path_untouched(self):
        self.assertEqual(monitoring.normalize_path("/api/health"), "/api/health")


class TestCounting(unittest.TestCase):
    def setUp(self):
        monitoring.reset_for_tests()

    def test_counts_and_5xx_only_are_errors(self):
        monitoring.record_request("/api/health", 200, 0.01)
        monitoring.record_request("/api/health", 200, 0.02)
        monitoring.record_request("/api/health", 404, 0.005)   # 调用方的错
        monitoring.record_request("/api/health", 500, 0.3)     # 系统的错
        snap = monitoring.snapshot()
        route = snap["routes"]["/api/health"]
        self.assertEqual(route["count"], 4)
        self.assertEqual(route["errors_5xx"], 1)
        self.assertEqual(snap["requests"]["errors_5xx"], 1)

    def test_param_routes_aggregate_into_one_bucket(self):
        for i in range(5):
            monitoring.record_request(f"/api/missions/mission-20261101-00000{i}",
                                      200, 0.01)
        snap = monitoring.snapshot()
        self.assertEqual(snap["routes"]["/api/missions/{mission_id}"]["count"], 5)

    def test_latency_percentiles(self):
        for ms in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            monitoring.record_request("/api/x", 200, ms / 1000)
        route = monitoring.snapshot()["routes"]["/api/x"]
        # 10 个样本：p50 取索引 round(0.5*9)=4 → 50ms；p95 索引 round(8.55)=9 → 100ms
        self.assertEqual(route["latency_p50_s"], 0.05)
        self.assertEqual(route["latency_p95_s"], 0.1)

    def test_high_error_rate_alarm_needs_sample_floor(self):
        # 样本太小：3 条错 2 条也不报——噪声不配触发告警
        monitoring.record_request("/api/x", 500, 0.1)
        monitoring.record_request("/api/x", 500, 0.1)
        monitoring.record_request("/api/x", 200, 0.01)
        self.assertEqual(monitoring.snapshot()["alarms"], [])
        # 越过样本下限：20 条里 15 条 5xx，必须报
        for _ in range(17):
            monitoring.record_request("/api/x", 500, 0.1)
        self.assertIn("high_error_rate", monitoring.snapshot()["alarms"])

    def test_low_error_rate_no_alarm(self):
        for _ in range(100):
            monitoring.record_request("/api/x", 200, 0.01)
        monitoring.record_request("/api/x", 500, 0.1)   # 1%
        self.assertEqual(monitoring.snapshot()["alarms"], [])


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        monitoring.reset_for_tests()

    def test_rss_is_mb_or_none(self):
        rss = monitoring.snapshot()["process_rss_mb"]
        # Linux 上是正数；Windows 开发机上如实 None——两者都合法
        self.assertTrue(rss is None or (isinstance(rss, float) and rss > 0))

    def test_empty_snapshot_is_consistent(self):
        snap = monitoring.snapshot()
        self.assertEqual(snap["requests"]["total"], 0)
        self.assertEqual(snap["requests"]["error_rate"], 0.0)
        self.assertEqual(snap["routes"], {})
        self.assertEqual(snap["alarms"], [])


if __name__ == "__main__":
    unittest.main()
