# -*- coding: utf-8 -*-
"""广告搜索词报告适配器的回归测试。

守的是"只映射、不补数"：表头不认识就跳过、数字解析不了就是 None、
任何情况下都不许冒出 search_volume 这种报告里根本没有的字段。
"""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from app.datasources import ads_report as ar
from app.tools import keywords


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


EN_REPORT = """Customer Search Term,Impressions,Clicks,7 Day Total Orders (#),Spend,7 Day Total Sales
dog bed for small dogs,"1,200",48,6,12.40,180.00
washable dog bed,800,32,4,9.10,120.00
dog bed,3000,150,9,40.00,270.00
Total,5000,230,19,61.50,570.00
"""

CN_REPORT = """客户搜索词,曝光量,点击量,7天总订单数(#),花费
狗床,1500,60,8,20.00
狗窝,900,30,3,10.00
"""


class TestNormalize(unittest.TestCase):
    def test_collapses_space_and_lowercases(self) -> None:
        self.assertEqual(ar.normalize_term("  Dog   Bed "), "dog bed")

    def test_does_not_stem(self) -> None:
        # 不做词干化：合并 dog bed / dog beds 等于篡改原始数据
        self.assertNotEqual(ar.normalize_term("dog beds"), ar.normalize_term("dog bed"))


class TestParseReport(unittest.TestCase):
    def setUp(self) -> None:
        self.report = ar.load_search_term_report(_rows(EN_REPORT), period="2026-10-01~2026-10-31")

    def test_configured_and_period_kept(self) -> None:
        self.assertTrue(self.report["configured"])
        self.assertEqual(self.report["period"], "2026-10-01~2026-10-31")

    def test_numbers_with_thousand_separator(self) -> None:
        self.assertEqual(self.report["terms"]["dog bed for small dogs"]["impressions"], 1200)

    def test_total_row_is_skipped(self) -> None:
        # Total 行的曝光是数字，但搜索词不是真实词——混进来会让曝光翻倍
        self.assertNotIn("total", self.report["terms"])
        self.assertEqual(self.report["summary_rows"], 1)
        self.assertEqual(self.report["skipped"], 0)   # 合计行是识别后剔除，不是解析失败

    def test_derived_rates(self) -> None:
        row = self.report["terms"]["dog bed"]
        self.assertAlmostEqual(row["ctr"], 0.05, places=4)     # 150/3000
        self.assertAlmostEqual(row["cvr"], 0.06, places=4)     # 9/150

    def test_no_search_volume_field_ever(self) -> None:
        blob = str(self.report)
        self.assertNotIn("search_volume", blob)

    def test_chinese_headers(self) -> None:
        rep = ar.load_search_term_report(_rows(CN_REPORT), period="2026-10")
        self.assertTrue(rep["configured"])
        self.assertEqual(rep["terms"]["狗床"]["clicks"], 60)

    def test_unknown_header_refuses_to_guess(self) -> None:
        rep = ar.load_search_term_report(_rows("foo,bar\n1,2\n"), period="2026-10")
        self.assertFalse(rep["configured"])
        self.assertIn("未识别", rep["reason"])
        self.assertEqual(rep["terms"], {})

    def test_missing_file_degrades(self) -> None:
        rep = ar.load_csv("__no_such_report__.csv", period="2026-10")
        self.assertFalse(rep["configured"])
        self.assertIn("不存在", rep["reason"])


class TestAttach(unittest.TestCase):
    TITLES = ["狗床小型犬狗窝四季通用可拆洗", "狗窝猫床夏季狗狗床宠物行军床"]

    def test_without_report_is_not_configured(self) -> None:
        data = keywords.build("dog bed", self.TITLES)
        ev = data["search_term_evidence"]
        self.assertEqual(ev["data_source"], "not_configured")
        self.assertIn("不提供", ev["note"])

    def test_without_report_no_volume_keys(self) -> None:
        data = keywords.build("dog bed", self.TITLES)
        blob = str(data)
        for fake in ("search_volume", "月搜索量"):
            self.assertNotIn(fake, blob)

    def test_with_report_matches_observed_terms(self) -> None:
        report = ar.load_search_term_report(
            _rows("Customer Search Term,Impressions,Clicks\ndog bed,3000,150\n"),
            period="2026-10")
        data = keywords.build("dog bed", self.TITLES, ads_report=report)
        self.assertEqual(data["search_term_evidence"]["data_source"], "ads_search_term_report")
        self.assertGreaterEqual(data["search_term_evidence"]["matched_terms"], 1)
        core = data["buckets"]["core"][0]
        self.assertEqual(core["ads"]["impressions"], 3000)

    def test_attach_never_invents_volume(self) -> None:
        report = ar.load_search_term_report(
            _rows("Customer Search Term,Impressions,Clicks\ndog bed,3000,150\n"),
            period="2026-10")
        data = keywords.build("dog bed", self.TITLES, ads_report=report)
        self.assertNotIn("search_volume", str(data))


class TestConfiguredPath(unittest.TestCase):
    """环境变量配置这条路径也要通——否则"可插拔"只是文档里的一句话。"""

    def setUp(self) -> None:
        from app.config import CONFIG
        self.cfg = CONFIG
        self._path = CONFIG.ads_report_path
        self._period = CONFIG.ads_report_period
        self.tmp = Path(tempfile.mkdtemp()) / "report.csv"
        self.tmp.write_text(EN_REPORT, encoding="utf-8")

    def tearDown(self) -> None:
        self.cfg.ads_report_path = self._path
        self.cfg.ads_report_period = self._period

    def test_unconfigured_returns_none(self) -> None:
        self.cfg.ads_report_path = ""
        self.assertIsNone(keywords._ads_report({}))

    def test_configured_loads_csv_with_period(self) -> None:
        self.cfg.ads_report_path = str(self.tmp)
        self.cfg.ads_report_period = "2026-10-01~2026-10-31"
        rep = keywords._ads_report({})
        self.assertTrue(rep["configured"])
        self.assertEqual(rep["period"], "2026-10-01~2026-10-31")
        self.assertIn("dog bed", rep["terms"])

    def test_tool_input_overrides_env(self) -> None:
        self.cfg.ads_report_path = "__missing__.csv"
        rep = keywords._ads_report({"ads_report_path": str(self.tmp),
                                    "ads_report_period": "2026-10"})
        self.assertTrue(rep["configured"])


if __name__ == "__main__":
    unittest.main()
