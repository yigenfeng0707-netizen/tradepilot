# Copyright 2026 TradePilot project. Apache-2.0.
"""外部复核适配层测试：重点是"它不能污染本地审计结论"。

这些用例刻意都在**不联网**的前提下跑：所有 HTTP 交互要么被 patch，
要么只测纯函数（归一化、去重、比对）。真实联调见
`scripts/check_listinggood.py`，那是有 Key 时手工跑的一次性验证。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import httpx

from app.config import CONFIG
from app.datasources import listinggood as LG


def _shallow_body() -> dict:
    """实测抓下来的真实响应形状（含它那条 'stops' 误报和重复的 BPA free）。"""
    return {
        "cleaned": {"title": "Stainless Steel Dog Bowl", "bullets": []},
        "report": {
            "score": 25, "level": "High risk", "total": 3,
            "by_severity": {"high": 3},
            "violations": [
                {"category": "医疗健康宣称", "matched": "stops", "severity": "high",
                 "suggestion": "删除疗效表述", "field": "bullets"},
                {"category": "母婴安全宣称", "matched": "BPA free", "severity": "high",
                 "suggestion": "改用中性表述", "field": "bullets"},
                {"category": "母婴安全宣称", "matched": "BPA free", "severity": "high",
                 "suggestion": "改用中性表述", "field": "bullets"},
            ],
            "summary": "Found 3 potential violation(s)",
        },
    }


def _deep_result() -> dict:
    return {
        "score": 85, "level": "安全",
        "summary": "整体合规性较好，材质真实性需供应链凭证支持。",
        "issues": [
            {"severity": "info", "area": "标题 / 五点1",
             "finding": "材质具体标称风险：文案声明为 304 stainless steel。",
             "evidence": "标题: 'Stainless Steel Dog Bowl'",
             "remediation": "保留供应商材质证明书。",
             "source": "Product Authenticity & Anti-Counterfeiting Policy"},
            {"severity": "medium", "area": "整体",
             "finding": "加州 Prop 65 合规提示。",
             "evidence": "品类: Pet Supplies",
             "remediation": "后台检查 Prop 65 申报。",
             "source": "California Proposition 65"},
        ],
    }


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._mode = CONFIG.listinggood_mode
        self._key = CONFIG.listinggood_api_key

    def tearDown(self) -> None:
        CONFIG.listinggood_mode = self._mode
        CONFIG.listinggood_api_key = self._key


class TestModeGate(_Base):
    def test_no_key_always_off(self):
        """没有 Key 时，把模式设成 deep 也必须降级为 off——不能空跑一个计费接口。"""
        CONFIG.listinggood_api_key = ""
        CONFIG.listinggood_mode = "deep"
        self.assertEqual(LG.mode(), "off")
        self.assertFalse(LG.available())

    def test_invalid_mode_falls_back_to_off(self):
        CONFIG.listinggood_api_key = "lg_live_x"
        CONFIG.listinggood_mode = "turbo"
        self.assertEqual(LG.mode(), "off")

    def test_default_is_off(self):
        CONFIG.listinggood_api_key = "lg_live_x"
        CONFIG.listinggood_mode = "off"
        self.assertEqual(LG.mode(), "off")

    def test_key_plus_mode_enables(self):
        CONFIG.listinggood_api_key = "lg_live_x"
        CONFIG.listinggood_mode = "check"
        self.assertEqual(LG.mode(), "check")
        self.assertTrue(LG.available())


class TestNormalize(unittest.TestCase):
    def test_shallow_maps_high_to_critical(self):
        issues, dup = LG._norm_shallow(_shallow_body()["report"])
        self.assertEqual(issues[0]["severity"], "critical")
        self.assertEqual(issues[0]["evidence"], "stops")

    def test_shallow_dedupes_repeated_hits(self):
        """实测它会把同一个命中记两遍；不去重会让"3 项高危"显得比实际严重。"""
        issues, dup = LG._norm_shallow(_shallow_body()["report"])
        self.assertEqual(dup, 1)
        self.assertEqual(len(issues), 2)

    def test_deep_keeps_policy_source(self):
        issues, dup = LG._norm_deep(_deep_result())
        self.assertEqual(dup, 0)
        self.assertEqual(issues[0]["source"],
                         "Product Authenticity & Anti-Counterfeiting Policy")
        # medium → warning，info → info
        self.assertEqual([i["severity"] for i in issues], ["info", "warning"])

    def test_copy_text_assembles_title_bullets_desc(self):
        text = LG._copy_text({"title": "Dog Bowl",
                              "bullets": ["a", "b"], "description": "desc"})
        self.assertIn("Dog Bowl", text)
        self.assertIn("1. a", text)
        self.assertIn("2. b", text)
        self.assertIn("desc", text)

    def test_copy_text_skips_empty_bullets(self):
        text = LG._copy_text({"title": "T", "bullets": ["a", "", "  "], "description": ""})
        self.assertNotIn("2.", text)


class TestOverlap(unittest.TestCase):
    def test_long_external_evidence_matches_short_local(self):
        """外部证据是长句、本地是片段，必须能认出来是同一处问题。"""
        self.assertTrue(LG._overlaps(
            "标题: 'Stainless Steel Dog Bowl'; 五点1: 'BPA free materials'",
            "BPA free"))

    def test_stopwords_alone_do_not_match(self):
        self.assertFalse(LG._overlaps("the and for with", "nothing relevant here"))

    def test_unrelated_text_no_match(self):
        self.assertFalse(LG._overlaps("Prop 65 warning required", "标题超限"))


class TestCrossCheck(_Base):
    def _local(self, score: int = 90, issues: list | None = None) -> dict:
        crit = [i for i in (issues or []) if i.get("level") == "critical"]
        return {"score": score, "passed": score >= 80 and not crit,
                "issues": issues or []}

    def test_off_is_skipped_not_unavailable(self):
        """off 是"按设计没问"，不能报成"问了没拿到"——两者含义完全不同。"""
        ext = LG.Review(status="off", mode="off", note="未启用")
        cc = LG.cross_check(self._local(), ext)
        self.assertEqual(cc["verdict"], "skipped")
        self.assertIn("未启用", cc["reason"])

    def test_network_error_is_unavailable(self):
        ext = LG.Review(status="error", mode="check", summary="timeout")
        cc = LG.cross_check(self._local(), ext)
        self.assertEqual(cc["verdict"], "unavailable")

    def test_agree_when_both_pass(self):
        ext = LG.Review(status="ok", score=94, level="A", issues=[])
        cc = LG.cross_check(self._local(90), ext)
        self.assertEqual(cc["verdict"], "agree")
        self.assertEqual(cc["external_only_n"], 0)

    def test_agree_when_both_fail(self):
        local = self._local(60, [{"level": "critical", "rule": "标题超限",
                                  "evidence": "250 字符 > 上限 200"}])
        ext = LG.Review(status="ok", score=25, level="High risk",
                        issues=[{"severity": "critical", "evidence": "stops"}])
        self.assertEqual(LG.cross_check(local, ext)["verdict"], "agree")

    def test_divergent_and_external_only_surfaced(self):
        """本地全绿、外部报了高危 —— 这才是交叉校验的价值，必须点名。"""
        local = self._local(92)
        ext = LG.Review(status="ok", score=25, level="High risk",
                        issues=[{"severity": "critical", "evidence": "stops"}])
        cc = LG.cross_check(local, ext)
        self.assertEqual(cc["verdict"], "divergent")
        self.assertEqual(cc["external_only_n"], 1)
        self.assertEqual(cc["external_only"][0]["evidence"], "stops")

    def test_no_averaging_of_scores(self):
        """两个引擎方法不同，平均分没有物理意义：分数必须各自原样保留。"""
        local = self._local(92)
        ext = LG.Review(status="ok", score=25, level="High risk", issues=[])
        cc = LG.cross_check(local, ext)
        self.assertEqual(cc["local_score"], 92)
        self.assertEqual(cc["external_score"], 25)
        self.assertNotIn("score", cc)      # 不存在合成分这个字段


class TestNeverRaises(_Base):
    def test_http_error_degrades_to_error_review(self):
        """外部服务挂了，审计必须照常出结果——这是硬要求。"""
        CONFIG.listinggood_api_key = "lg_live_x"
        CONFIG.listinggood_mode = "check"

        class Boom:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                raise httpx.ConnectError("boom")

        with mock.patch.object(LG.httpx, "AsyncClient", Boom):
            out = asyncio.run(LG.review({"title": "T", "bullets": []}, "US"))
        self.assertEqual(out.status, "error")
        self.assertFalse(out.ok)
        self.assertIn("ConnectError", out.summary)

    def test_unexpected_exception_also_degrades(self):
        CONFIG.listinggood_api_key = "lg_live_x"
        CONFIG.listinggood_mode = "check"

        async def boom(*a, **k):
            raise RuntimeError("unexpected")

        with mock.patch.object(LG, "_shallow", boom):
            out = asyncio.run(LG.review({"title": "T"}, "US"))
        self.assertEqual(out.status, "error")
        self.assertIn("RuntimeError", out.summary)


class TestAuditIntegration(_Base):
    def test_run_adds_external_block_and_keeps_score(self):
        """off 模式下：审计分数必须与纯函数 audit() 完全一致，且带 skipped 结论。"""
        from app.tools import listing_audit as LA

        CONFIG.listinggood_api_key = ""
        CONFIG.listinggood_mode = "off"
        copy = {"title": "Stainless Steel Dog Bowl for Medium Dogs",
                "bullets": ["Rust resistant bowl " * 6] * 5,
                "description": "A bowl."}
        out = asyncio.run(LA.run({"copy": copy, "marketplace": "US"}))
        self.assertTrue(out.ok)
        pure = LA.audit(copy, marketplace="US")
        self.assertEqual(out.data["score"], pure["score"])
        self.assertEqual(out.data["external_review"]["status"], "off")
        self.assertEqual(out.data["cross_check"]["verdict"], "skipped")

    def test_summary_mentions_cross_check_only_when_it_ran(self):
        from app.tools import listing_audit as LA

        CONFIG.listinggood_api_key = ""
        CONFIG.listinggood_mode = "off"
        copy = {"title": "T", "bullets": ["b"], "description": ""}
        out = asyncio.run(LA.run({"copy": copy}))
        self.assertNotIn("外部复核", out.summary)


if __name__ == "__main__":
    unittest.main()
