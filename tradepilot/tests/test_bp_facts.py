# Copyright 2026 TradePilot project. Apache-2.0.
"""BP 口径层的离线用例：锁住"数字只能来自评测 JSON 与代码"这条门禁。

回归对象是真实事故：上一版 BP 里写死了 "5/5 步" 与 "Agent 侧来源可溯率 100%"，
代码早已改成 7 步且当时根本没有可溯率算法，文档不会自己跟着变。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_bp  # noqa: E402


def _write(tmp: str, payload: dict) -> Path:
    p = Path(tmp) / "eval_results.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    return p


BASE_EVAL = {
    "meta": {"mode": "rules", "repeats": 2, "generated_at": "2026-10-30T01:00:00+00:00"},
    "summary": {"pet": {"runs": 2, "steps_success_rate": 1.0, "items_mean": 8.0,
                        "compliance_verified_mean": 0.75, "llm_planned_rate": 0.9,
                        "llm_override_total": 1, "audit_score_mean": 88.0}},
    "provenance_totals": {"total": 233, "distinct_sources": 22},
    "human_baseline": {"task_time_min": None},
}


class TestBpFacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = build_bp.EVAL_JSON
        build_bp.EVAL_JSON = _write(self._tmp.name, BASE_EVAL)

    def tearDown(self):
        build_bp.EVAL_JSON = self._orig
        self._tmp.cleanup()

    def test_numbers_come_from_the_json(self):
        f = build_bp.measured_facts()
        self.assertEqual(f["mode"], "rules")
        self.assertEqual(f["repeats"], 2)
        self.assertEqual(f["generated_at"], "2026-10-30")
        self.assertEqual(f["provenance_totals"]["total"], 233)

    def test_missing_traceability_dimension_is_none_not_inherited(self):
        # 旧 JSON 没有这个维度 → 必须是 None（BP 会印"待重跑"），不能沿用旧稿的 100%
        f = build_bp.measured_facts()
        self.assertIsNone(f["trace_rate"])
        self.assertIsNone(f["spot_accuracy"])

    def test_present_traceability_dimension_is_read_through(self):
        payload = dict(BASE_EVAL)
        payload["source_traceability_overall"] = {"facts_total": 6, "facts_traceable": 6,
                                                 "rate": 1.0}
        payload["fact_spot_check_accuracy"] = {"accuracy": 0.9, "reason": "已全量人工回访核对"}
        build_bp.EVAL_JSON = _write(self._tmp.name, payload)
        f = build_bp.measured_facts()
        self.assertEqual(f["trace_rate"], 1.0)
        self.assertEqual(f["trace_detail"], "6/6")
        self.assertEqual(f["spot_accuracy"], 0.9)

    def test_code_facts_come_from_code_not_consts(self):
        from app.planner import STEPS
        f = build_bp.measured_facts()
        self.assertEqual(f["tool_n"], len(STEPS))
        self.assertEqual(f["steps_standard"], len(STEPS) - 1)   # 选品雷达按目标触发
        self.assertEqual([n for n, _ in f["steps"]], [s.name for s in STEPS])
        self.assertGreater(f["tests"], 100)
        self.assertGreater(f["loc"], 1000)

    def test_missing_eval_file_refuses_to_build(self):
        build_bp.EVAL_JSON = Path(self._tmp.name) / "nope.json"
        with self.assertRaises(SystemExit):
            build_bp.measured_facts()


if __name__ == "__main__":
    unittest.main()
