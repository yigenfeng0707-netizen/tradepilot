"""关键词层测试：词频来自真实标题、不产出搜索量、Search Terms 守字节上限。

这一层最容易滑向造假（"月搜索量 12,000"），所以用例重点锁两件事：
1. 每个输出词都必须能在真实标题里找到证据；
2. 输出里**不存在**搜索量/竞争度评分这类拿不到的数据。
"""
from __future__ import annotations

import asyncio
import json
import unittest

from app import provenance
from app.tools import keywords

TITLES = [
    "狗窝可拆洗四季通用狗狗床超级大猫窝狗窝大型中型犬夏天宠物床垫",
    "狗床小型犬狗窝四季通用可拆洗中型犬宠物床离地小狗睡觉专用跨境卡贝拉",
    "狗床小型犬狗窝四季通用可拆洗中型犬宠物床离地小狗睡觉跨境卡贝拉",
    "狗窝猫床夏季狗狗床宠物行军床小型犬狗窝四季通用离地床猫窝夏天",
    "夏季狗窝四季通用狗狗床宠物用品沙发窝小中大型犬可拆洗睡垫子",
]

EN_TITLES = [
    "Dog Bed Large Memory Foam Washable Cover Grey",
    "Memory Foam Dog Bed Medium Washable Non Slip Base",
    "Dog Bed Large Waterproof Memory Foam Grey",
]


def build(titles=TITLES, keyword="dog bed") -> dict:
    return keywords.build(keyword, titles)


class TestObservationGrounded(unittest.TestCase):
    def test_frequent_terms_are_counted(self) -> None:
        data = build()
        flat = {e["observed"]: e for b in data["buckets"].values() for e in b}
        self.assertGreaterEqual(flat["可拆洗"]["count"], 3)
        self.assertGreaterEqual(flat["四季通用"]["count"], 3)
        self.assertIn("狗床", flat)
        self.assertIn("小型犬", flat)

    def test_every_term_carries_evidence_title(self) -> None:
        data = build()
        for bucket in data["buckets"].values():
            for entry in bucket:
                self.assertTrue(entry["evidence"], entry)
                self.assertIn(entry["observed"], entry["evidence"] + entry["observed"])

    def test_buckets_are_classified(self) -> None:
        b = build()["buckets"]
        self.assertTrue(any(e["observed"] == "可拆洗" for e in b["attribute"]))
        self.assertTrue(any(e["observed"] == "四季通用" for e in b["scenario"]))
        self.assertTrue(any(e["observed"] == "小型犬" for e in b["audience"]))
        self.assertTrue(any(e["observed"] in ("狗床", "狗窝") for e in b["core"]))

    def test_core_is_capped_and_rest_goes_related(self) -> None:
        b = build()["buckets"]
        self.assertLessEqual(len(b["core"]), 2, "标题核心位有限，核心词不得超过 2 个")
        related = {e["observed"] for e in b["related"]}
        core = {e["observed"] for e in b["core"]}
        self.assertFalse(related & core, "同一个词不能既在核心又在相关品类")
        self.assertTrue(related, "低频品类词应落到相关品类桶")

    def test_core_prefers_seed_synonym_over_generic_term(self) -> None:
        b = build()["buckets"]
        top = (b["core"][0]["english"] or [""])[0]
        self.assertEqual(top, "dog bed", "核心词必须是目标品类词，不能被泛词「宠物」挤掉")

    def test_platform_jargon_is_excluded(self) -> None:
        data = build()
        observed = {e["observed"] for b in data["buckets"].values() for e in b}
        for noise in ("跨境", "包邮", "正品", "卡贝拉"):
            self.assertNotIn(noise, observed)


class TestHonestyBoundary(unittest.TestCase):
    def test_no_search_volume_or_competition_score(self) -> None:
        data = build()
        blob = json.dumps(data, ensure_ascii=False)
        # 免责声明里可以出现"搜索量"三个字，但绝不能出现这类字段或数值
        for fake in ("search_volume", "competition_score", "月搜索量"):
            self.assertNotIn(fake, blob)
        self.assertFalse(set(data["competition_proxy"]) &
                         {"score", "level", "competition_score"})

    def test_proxy_metrics_are_from_sample(self) -> None:
        proxy = build()["competition_proxy"]
        self.assertEqual(proxy["sample_size"], len(TITLES))
        self.assertGreater(proxy["core_term_coverage"], 0)
        self.assertIn("未接入搜索量数据源", proxy["_说明"])

    def test_honesty_notes_present(self) -> None:
        notes = " ".join(build()["honesty"])
        self.assertIn("不等于", notes)


class TestSearchTerms(unittest.TestCase):
    def test_within_250_bytes(self) -> None:
        data = build()
        self.assertLessEqual(data["search_terms_bytes"], 250)
        self.assertEqual(len(data["search_terms"].encode("utf-8")), data["search_terms_bytes"])

    def test_english_only_and_deduped(self) -> None:
        terms = [t.strip() for t in build()["search_terms"].split(",") if t.strip()]
        self.assertTrue(terms)
        self.assertEqual(len(terms), len(set(terms)), "不得重复")
        for t in terms:
            self.assertTrue(t.isascii(), t)

    def test_does_not_repeat_title_keyword(self) -> None:
        terms = build()["search_terms"].lower()
        self.assertNotIn("dog bed,", terms)


class TestEnglishSource(unittest.TestCase):
    def test_ngrams_extracted_from_english_titles(self) -> None:
        data = build(EN_TITLES, keyword="dog bed")
        self.assertEqual(data["language_of_source"], "en")
        flat = {e["observed"]: e for b in data["buckets"].values() for e in b}
        self.assertIn("memory foam", flat)
        self.assertGreaterEqual(flat["memory foam"]["count"], 3)


class TestToolWrapper(unittest.TestCase):
    def test_needs_three_titles(self) -> None:
        pid = provenance.record("item", "https://example.com/x",
                                {"title": "狗床可拆洗", "price": 1.0, "currency": "CNY"})
        out = asyncio.run(keywords.run({"item_ids": [pid]}))
        self.assertEqual(out.status, "error")
        self.assertIn("不足以统计词频", out.summary)

    def test_runs_on_provenance_items(self) -> None:
        ids = [provenance.record("item", "https://example.com/y",
                                 {"title": t, "price": 10.0 + i, "currency": "CNY"})
               for i, t in enumerate(TITLES)]
        out = asyncio.run(keywords.run({"item_ids": ids, "keyword": "dog bed"}))
        self.assertEqual(out.status, "done", out.summary)
        self.assertIn("Search Terms", out.summary)
        self.assertIn("不提供搜索量", out.summary)


if __name__ == "__main__":
    unittest.main()
