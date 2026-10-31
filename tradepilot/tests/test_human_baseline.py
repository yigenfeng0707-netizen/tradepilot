# -*- coding: utf-8 -*-
"""人工维度录入工具的回归测试。

这些用例守的不是"代码能跑"，而是**账不能虚**：合计分钟不许四舍五入到整数、
跳过必须保持 null、agent 侧必须与人工同为单次口径。任何一条松掉，
BP 里的"量化对照"就变成自己给自己发奖。
"""
import importlib.util
import unittest
from pathlib import Path

from app import eval_metrics

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "human_baseline.py"
_spec = importlib.util.spec_from_file_location("human_baseline", _SCRIPT)
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)


class TestHumanBaselineRecord(unittest.TestCase):
    def test_total_keeps_raw_seconds(self) -> None:
        # 秒是权威值：分钟只做展示，算自动化率时不得用被舍入过的分钟数
        rec = hb.human_baseline_record([10, 10, 10, 10, 21.5], "张三")
        self.assertEqual(rec["task_time_s"], 61.5)
        self.assertEqual(rec["task_time_min"], 1.0)

    def test_realistic_stages_sum_matches_total(self) -> None:
        secs = [300, 120, 180, 240, 300]          # 1140s = 19.0 分钟
        rec = hb.human_baseline_record(secs, "张三")
        self.assertEqual(rec["task_time_s"], 1140)
        self.assertEqual(rec["task_time_min"], 19.0)
        self.assertAlmostEqual(sum(rec["stages_min"].values()), 19.0, places=1)

    def test_stage_names_match_sop(self) -> None:
        rec = hb.human_baseline_record([60] * 5, "李四")
        self.assertEqual(set(rec["stages_min"]), set(hb.STAGES))
        self.assertIn("市场扫描", rec["stages_min"])
        self.assertIn("Listing 文案", rec["stages_min"])

    def test_rejects_zero_timing(self) -> None:
        # 全 0 意味着根本没计时，写进去就是假基线
        with self.assertRaises(ValueError):
            hb.human_baseline_record([0, 0, 0, 0, 0], "王五")

    def test_rejects_wrong_stage_count(self) -> None:
        with self.assertRaises(ValueError):
            hb.human_baseline_record([60, 60], "王五")

    def test_optional_fields_stay_null_when_not_given(self) -> None:
        rec = hb.human_baseline_record([60] * 5, "赵六")
        self.assertIsNone(rec["data_points_collected"])
        self.assertIsNone(rec["sources_cited"])

    def test_records_who_and_when(self) -> None:
        rec = hb.human_baseline_record([60] * 5, "赵六")
        self.assertEqual(rec["performed_by"], "赵六")
        self.assertTrue(rec["performed_at"].startswith("20"))
        self.assertIn("time.monotonic", rec["timing_method"])


class TestSpotCheck(unittest.TestCase):
    def _sheet(self, verdicts):
        return [{"provenance_id": i, "human_verified": v}
                for i, v in enumerate(verdicts, 1)]

    def test_skipped_rows_keep_accuracy_null(self) -> None:
        # 4 条里 3 条已回访、1 条跳过 → 不能报 100%，必须 null
        acc = hb.apply_spot_check(self._sheet([True, True, True, None]))
        self.assertIsNone(acc["accuracy"])
        self.assertEqual(acc["checked"], 3)
        self.assertEqual(acc["total"], 4)

    def test_full_coverage_computes_accuracy(self) -> None:
        acc = hb.apply_spot_check(self._sheet([True, True, False, True]))
        self.assertEqual(acc["accuracy"], 0.75)
        self.assertEqual(acc["checked"], 4)

    def test_agrees_with_eval_metrics(self) -> None:
        sheet = self._sheet([True, False, None])
        self.assertEqual(hb.apply_spot_check(sheet),
                         eval_metrics.spot_check_accuracy(sheet))


class TestLaborBasis(unittest.TestCase):
    def test_agent_side_is_per_run_mean_not_batch_total(self) -> None:
        runs = [{"elapsed_s": 20.0}, {"elapsed_s": 40.0}]
        self.assertEqual(eval_metrics.per_run_mean_elapsed(runs), 30.0)
        self.assertEqual(hb.agent_elapsed_per_run(runs), 30.0)

    def test_empty_runs_gives_none(self) -> None:
        self.assertIsNone(eval_metrics.per_run_mean_elapsed([]))

    def test_automation_rate_uses_single_task_elapsed(self) -> None:
        # 人工 10 分钟，agent 单次 30 秒 → 自动化率 95%
        labor = eval_metrics.labor_cost_equiv({"task_time_min": 10.0}, 30.0, 80.0)
        self.assertAlmostEqual(labor["automation_rate"], 0.95, places=4)
        self.assertAlmostEqual(labor["labor_cost_equiv_cny"], 13.33, places=2)
        self.assertEqual(labor["agent_elapsed_s"], 30.0)
        self.assertIn("per_run_mean_s", labor["agent_elapsed_basis"])

    def test_null_baseline_keeps_everything_null(self) -> None:
        labor = eval_metrics.labor_cost_equiv(
            {"task_time_min": None, "performed_by": None}, 30.0, 80.0)
        self.assertIsNone(labor["labor_cost_equiv_cny"])
        self.assertIsNone(labor["automation_rate"])


if __name__ == "__main__":
    unittest.main()
