"""选品雷达（模块①）的离线单测。

这里要锁住两件事：
1. 算得对——分位、权重、排序、样本门槛都可机器复算；
2. 说得诚——没接入的数据一律留空，样本不足就不出分，内置候选词必须标成假设。
`scan` 与源侧过滤函数全部打桩，单测不联网。
"""
from __future__ import annotations  # noqa: F401

import asyncio
import unittest
from typing import Any
from unittest import mock

from app.tools import opportunity as O


def items(n: int, price: float, tag: str = "usb c cable") -> list[dict[str, Any]]:
    return [{"title": f"{tag} braided fast charger {i} {tag}", "price": price + i,
             "source_url": f"https://src.example.com/p/{i}"} for i in range(n)]


def fake_scan(table: dict[str, dict], error: dict[str, str] | None = None):
    async def _scan(source: str, keyword: str, limit: int = 8, keyword_cn: str = "") -> dict:
        if error and keyword in error:
            raise ValueError(error[keyword])
        row = table.get(keyword)
        if row is None:
            raise ValueError(f"搜索「{keyword}」未解析到商品")
        return {**row, "source_url": f"https://src.example.com/search?q={keyword}",
                "channel": "browser", "robots": {"allowed": True, "note": "允许"},
                "source_label": "Fake · 测试源"}
    return _scan


def patch_all(scan, category: str = "electronics"):
    """把源侧过滤打桩掉：本文件只考机会分逻辑，不重复考 sources 的过滤规则。"""
    return (mock.patch.object(O, "scan", scan),
            mock.patch.object(O, "is_book", lambda t: False),
            mock.patch.object(O, "relevant", lambda *a, **k: True))


def run_tool(tool_input: dict, scan) -> Any:
    s, b, r = patch_all(scan)
    with s, b, r:
        return asyncio.run(O.run(tool_input))


class TestPureHelpers(unittest.TestCase):
    def test_price_stats_empty_is_zeros_not_cheap(self) -> None:
        self.assertEqual(O.price_stats([]),
                         {"min": 0.0, "median": 0.0, "max": 0.0, "spread_pct": 0.0})

    def test_price_stats_median_odd_and_even(self) -> None:
        self.assertEqual(O.price_stats([3.0, 1.0, 2.0])["median"], 2.0)
        self.assertEqual(O.price_stats([1.0, 2.0, 3.0, 4.0])["median"], 2.5)
        # 口径是 (max−min)/median：[10,20] 的中位数 15 → 66.7，不是相对均值
        self.assertEqual(O.price_stats([10.0, 20.0])["spread_pct"], 66.7)

    def test_percentile_refuses_fake_gap_on_equal_values(self) -> None:
        """单值/全等一律给 50：不能凭零差距造出排名差距。"""
        self.assertEqual(O._percentile([7.0]), [50.0])
        self.assertEqual(O._percentile([7.0, 7.0, 7.0]), [50.0, 50.0, 50.0])
        self.assertEqual(O._percentile([]), [])
        self.assertEqual(O._percentile([1.0, 2.0, 3.0]), [33.3, 66.7, 100.0])

    def test_dedupe_drops_same_title_same_price_only(self) -> None:
        rows = [{"title": "a" * 30, "price": 9.9}, {"title": "a" * 30, "price": 9.9},
                {"title": "a" * 30, "price": 12.0}]
        self.assertEqual(len(O._dedupe(rows)), 2)

    def test_distinct_terms_counts_word_forms_not_semantics(self) -> None:
        self.assertGreaterEqual(O._distinct_terms(["usb c cable braided charger"]), 4)
        self.assertEqual(O._distinct_terms([""]), 0)

    def test_resolve_keywords_user_wins_and_dedupes(self) -> None:
        words, origin = O.resolve_keywords(
            {"keywords": "dog bed, Dog Bed；cat bed\ncat litter"}, "pet")
        self.assertEqual(origin, "user")
        self.assertEqual(words, ["dog bed", "cat bed", "cat litter"])

    def test_resolve_keywords_builtin_is_labelled_as_hypothesis(self) -> None:
        words, origin = O.resolve_keywords({}, "pet")
        self.assertEqual(origin, "builtin_hypothesis")
        self.assertEqual(words, O.BUILTIN_HYPOTHESES["pet"][:O.MAX_KEYWORDS])

    def test_unknown_category_has_no_builtin_words(self) -> None:
        self.assertEqual(O.resolve_keywords({}, "jewelry")[0], [])

    def test_weights_are_a_closed_contract(self) -> None:
        self.assertAlmostEqual(sum(O.WEIGHTS.values()), 1.0)
        self.assertEqual(set(O.WEIGHTS), {"yield", "noise", "depth", "spread", "terms"})


class TestScoreRows(unittest.TestCase):
    def _rows(self) -> list[dict[str, Any]]:
        def row(kw, kept, raw, lo, hi, terms):
            mid = (lo + hi) / 2
            return {"keyword": kw, "keyword_used": kw, "currency": "USD",
                    "raw_count": raw, "kept": kept, "filtered_noise": raw - kept,
                    "noise_rate": round(1 - kept / raw, 3),
                    "price": O.price_stats([lo, mid, hi]),
                    "distinct_terms": terms, "source_url": "u", "top_items": [],
                    "provenance_ids": []}
        return [row("a", 8, 8, 10, 20, 30), row("b", 4, 8, 10, 80, 10),
                row("c", 3, 12, 5, 9, 20)]

    def test_components_are_in_range_and_higher_is_better(self) -> None:
        scored = O.score_rows(self._rows())
        for r in scored:
            for name, v in r["components"].items():
                self.assertGreaterEqual(v, 0.0, name)
                self.assertLessEqual(v, 100.0, name)

    def test_rank_order_follows_score(self) -> None:
        scored = O.score_rows(self._rows())
        self.assertEqual([r["rank"] for r in scored], [1, 2, 3])
        self.assertEqual(scored, sorted(scored, key=lambda r: r["score"], reverse=True))
        self.assertEqual(scored[0]["keyword"], "a")

    def test_score_is_the_stated_weighted_sum(self) -> None:
        """分数必须能被外部按同一公式复算，否则"可复算"只是说法。"""
        for r in O.score_rows(self._rows()):
            expect = round(sum(r["components"][k] * O.WEIGHTS[k] for k in O.WEIGHTS), 1)
            self.assertAlmostEqual(r["score"], expect, places=1)

    def test_empty_rows_score_to_empty(self) -> None:
        self.assertEqual(O.score_rows([]), [])


class TestOpportunityTool(unittest.TestCase):
    def test_happy_path_is_fully_traceable(self) -> None:
        from app import provenance
        table = {"a": {"keyword_used": "a", "currency": "USD", "items": items(8, 10.0, "usb c cable")},
                 "b": {"keyword_used": "b", "currency": "USD", "items": items(6, 40.0, "usb c cable")},
                 "c": {"keyword_used": "c", "currency": "USD", "items": items(4, 5.0, "usb c cable")}}
        out = run_tool({"category": "electronics", "source": "newegg", "keywords": "a,b,c"},
                       fake_scan(table))
        self.assertTrue(out.ok)
        data = out.data
        self.assertEqual(data["scans_run"], 3)
        self.assertEqual(data["scans_usable"], 3)
        self.assertEqual(data["keyword_origin"], "user")
        self.assertEqual([c["rank"] for c in data["candidates"]], [1, 2, 3])
        self.assertEqual(data["currency"], "USD")
        for cand in data["candidates"]:
            self.assertTrue(cand["provenance_ids"], "每个候选分都必须可点回真实抓取")
            rec = provenance.get(cand["provenance_ids"][0])
            self.assertIsNotNone(rec)
            self.assertEqual(rec["kind"], "observation")
            self.assertIn("src.example.com", rec["url"])
            self.assertTrue(rec["fetched_at"])
            self.assertEqual(rec["payload"]["keyword"], cand["keyword"])

    def test_failed_words_are_listed_not_dropped(self) -> None:
        """抓失败的词必须留在产物里并带原因，不能被静默丢掉后假装只扫了成功的。"""
        table = {"a": {"keyword_used": "a", "currency": "USD",
                       "items": items(8, 10.0, "usb c cable")}}
        out = run_tool({"category": "electronics", "keywords": "a,bad"},
                       fake_scan(table, error={"bad": "robots.txt 禁止抓取该页面"}))
        self.assertTrue(out.ok)
        self.assertEqual(out.data["scans_run"], 2)
        self.assertEqual(out.data["scans_ok"], 1)
        self.assertEqual(out.data["scans_usable"], 1)
        self.assertEqual(out.data["scans_failed"][0]["keyword"], "bad")
        self.assertIn("robots.txt", out.data["scans_failed"][0]["error"])
        self.assertIn("未取得数据", out.summary)

    def test_too_few_samples_refuses_to_rank(self) -> None:
        """样本不足就不出分：宁可不给排名，也不给一个站不住的排名。"""
        table = {"a": {"keyword_used": "a", "currency": "USD", "items": items(2, 10.0)},
                 "b": {"keyword_used": "b", "currency": "USD", "items": items(1, 3.0)}}
        out = run_tool({"category": "electronics", "keywords": "a,b"}, fake_scan(table))
        self.assertFalse(out.ok)
        self.assertEqual(out.status, "error")
        self.assertIn("不编造机会排名", out.summary)

    def test_unknown_category_asks_for_words_instead_of_inventing(self) -> None:
        out = run_tool({"category": "jewelry"}, fake_scan({}))
        self.assertFalse(out.ok)
        self.assertIn("keywords", out.summary)
        self.assertIn("不替你臆造候选品类", out.summary)

    def test_noise_accounting_counts_filtered_items(self) -> None:
        table = {"a": {"keyword_used": "a", "currency": "USD", "items": items(8, 10.0)}}
        keep = lambda *a, **k: any(f"charger {i}" in a[0] for i in range(4))  # 8 留 4
        with mock.patch.object(O, "scan", fake_scan(table)), \
                mock.patch.object(O, "is_book", lambda t: False), \
                mock.patch.object(O, "relevant", keep):
            out = asyncio.run(O.run({"category": "electronics", "keywords": "a"}))
        cand = out.data["candidates"][0]
        self.assertEqual(cand["raw_count"], 8)
        self.assertEqual(cand["sample_size"], 4)
        self.assertEqual(cand["filtered_noise"], 4)
        self.assertEqual(cand["noise_rate"], 0.5)
        self.assertEqual(cand["components"]["noise"], 50.0)   # 干净度 = 保留/原始
        self.assertEqual(cand["components"]["yield"], 50.0)   # 4 / 目标 8 条

    def test_honesty_disclaimers_ship_with_the_score(self) -> None:
        """口径说明必须跟分数一起交付——只写在注释里等于没写。"""
        table = {"a": {"keyword_used": "a", "currency": "USD", "items": items(8, 10.0)}}
        out = run_tool({"category": "electronics", "keywords": "a"}, fake_scan(table))
        data = out.data
        self.assertIn("不是需求量", data["basis"])
        self.assertIn("平台热销品类", data["basis"])
        self.assertIn("权重是设计选择", data["method"])
        self.assertIsNone(data["candidates"][0]["core_term_coverage"])  # 没可靠口径就留空
        self.assertEqual(set(data["candidates"][0]["components"]),
                         {"yield", "noise", "depth", "spread", "terms"})
        self.assertEqual(data["weights"], O.WEIGHTS)

    def test_builtin_words_are_labelled_as_hypothesis_in_output(self) -> None:
        table = {kw: {"keyword_used": kw, "currency": "USD", "items": items(8, 10.0)}
                 for kw in O.BUILTIN_HYPOTHESES["pet"]}
        out = run_tool({"category": "pet"}, fake_scan(table))
        self.assertEqual(out.data["keyword_origin"], "builtin_hypothesis")
        self.assertEqual(out.data["scans_run"], len(O.BUILTIN_HYPOTHESES["pet"]))

    def test_keyword_count_is_capped_twice(self) -> None:
        """每个词都是一次真实抓取：默认 5 个（MAX_KEYWORDS），显式再多也只到 8（KEYWORD_CAP）。"""
        many = ",".join(f"k{i}" for i in range(12))
        table = {f"k{i}": {"keyword_used": f"k{i}", "currency": "USD", "items": items(8, 1.0)}
                 for i in range(12)}
        default_run = run_tool({"category": "electronics", "keywords": many}, fake_scan(table))
        self.assertEqual(default_run.data["scans_run"], O.MAX_KEYWORDS)
        greedy = run_tool({"category": "electronics", "keywords": many, "max_keywords": 99},
                          fake_scan(table))
        self.assertEqual(greedy.data["scans_run"], O.KEYWORD_CAP)


    def test_thin_samples_are_accounted_for(self) -> None:
        """抓成功但样本不足的词必须单独记账：否则摘要"4/5"而失败清单为空，差值凭空消失。"""
        table = {"good": {"keyword_used": "good", "currency": "USD", "items": items(8, 10.0)},
                 "thin": {"keyword_used": "thin", "currency": "USD", "items": items(1, 3.0)}}
        out = run_tool({"category": "electronics", "keywords": "good,thin"}, fake_scan(table))
        self.assertTrue(out.ok)
        data = out.data
        self.assertEqual(data["scans_run"], 2)
        self.assertEqual(data["scans_usable"], 1)
        self.assertEqual(data["scans_failed"], [])
        self.assertEqual(data["scans_thin"][0]["keyword"], "thin")
        self.assertEqual(data["scans_thin"][0]["kept"], 1)
        self.assertIn("样本过少", out.summary)
        self.assertIn("thin", out.summary)

    def test_accounting_covers_every_requested_keyword(self) -> None:
        """硬不变式：评分 + 样本过少 + 抓取失败 = 请求词数，一个都不能少算。"""
        table = {"good": {"keyword_used": "good", "currency": "USD", "items": items(8, 10.0)},
                 "thin": {"keyword_used": "thin", "currency": "USD", "items": items(2, 3.0)}}
        out = run_tool({"category": "electronics", "keywords": "good,thin,broken"},
                       fake_scan(table, error={"broken": "未解析到商品"}))
        data = out.data
        self.assertEqual(
            data["scans_usable"] + len(data["scans_thin"]) + len(data["scans_failed"]),
            data["scans_run"])
        self.assertEqual(data["scans_run"], 3)


class TestMissionContract(unittest.TestCase):
    """前端提交的选品参数必须真能被后端接住——字段没声明就会被 pydantic 静默丢掉。"""

    def test_mission_in_carries_radar_params(self) -> None:
        from app.main import MissionIn
        req = MissionIn(goal="选品", keyword="dog bed", category="pet",
                        keywords="dog bed,cat bed", max_keywords=2)
        dumped = req.model_dump()
        self.assertEqual(dumped["keywords"], "dog bed,cat bed")
        self.assertEqual(dumped["max_keywords"], 2)
        self.assertEqual(dumped["category"], "pet")

    def test_radar_params_are_optional_with_honest_defaults(self) -> None:
        from app.main import MissionIn
        dumped = MissionIn(goal="选品", keyword="dog bed", category="pet").model_dump()
        self.assertEqual(dumped["keywords"], "")          # 空=用内置假设词，不是随机词
        self.assertEqual(dumped["max_keywords"], O.MAX_KEYWORDS)

    def test_empty_keywords_falls_back_to_builtin_hypothesis(self) -> None:
        """接口默认值必须与模块口径一致：空串走内置假设词并如实标记来源。"""
        words, origin = O.resolve_keywords({"keywords": ""}, "pet")
        self.assertEqual(origin, "builtin_hypothesis")
        self.assertEqual(words, O.BUILTIN_HYPOTHESES["pet"][:O.MAX_KEYWORDS])
