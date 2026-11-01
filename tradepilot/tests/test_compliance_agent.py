# -*- coding: utf-8 -*-
"""合规 Agent（产品层）的回归测试（M2）。

守的红线：
1. **不认识的国家不猜**：没有条款库就报错，不拿美国那套去套东南亚；
2. **静态库不许冒充已验证**：东南亚逐条 verification="static_reference"、
   verified=False、data_quality="sample"；
3. **不编税率**：HTS 与东盟税则无公开免费 API，输出里不许出现具体税率数字；
4. **风险等级不是法律结论**：risk_basis 必须写明这是资料完整度指标。
"""
import unittest
from unittest import mock

from app.agents import compliance as cp
from app.tools.executor import ToolOutcome


def _us_outcome(verified: int = 2, total: int = 4):
    checks = []
    for i in range(total):
        ok = i < verified
        checks.append({
            "item": f"要求{i}", "authority": "U.S. FDA", "kind": "authority",
            "verification": "clause_matched" if ok else "page_only",
            "verified": ok, "live": True, "http_reachable": True,
            "page_url": f"https://www.fda.gov/p{i}", "excerpt": "clause text",
            "provenance_id": 700 + i if ok else None,
        })
    checks.append({"item": "平台发布规则", "authority": "AliExpress 规则中心",
                   "kind": "platform", "verification": "page_only",
                   "verified": False, "live": True, "http_reachable": True,
                   "page_url": "https://helpcenter.aliexpress.com",
                   "excerpt": None, "provenance_id": 800})
    return ToolOutcome(True, "done", None, "合规体检", {
        "category": "pet", "checks": checks,
        "live_verified": verified, "content_ok_sources": total + 1,
        "http_reachable_sources": total + 1, "blocked_sources": [],
        "total": total + 1, "basis": "条款正文为编辑性知识",
    })


class TestInputValidation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_category(self):
        r = await cp.run("  ", target_country="美国")
        self.assertFalse(r["success"])

    async def test_unknown_country_is_rejected_not_guessed(self):
        """没有该国条款库就必须报错——猜法规比不答危险得多。"""
        r = await cp.run("宠物用品", target_country="巴西")
        self.assertFalse(r["success"])
        self.assertIn("未支持的目标国家", r["message"])
        self.assertIn("美国", r["detail"]["supported"])

    async def test_unknown_category_is_rejected(self):
        r = await cp.run("美妆个护", target_country="美国")
        self.assertFalse(r["success"])
        self.assertIn("暂无品类", r["message"])


class TestSeaStatic(unittest.IsolatedAsyncioTestCase):
    async def test_sea_is_sample_and_never_verified(self):
        r = await cp.run("宠物用品", target_country="印尼")
        self.assertTrue(r["success"])
        self.assertEqual(r["data_quality"], "sample")
        checks = r["data"]["checks"]
        self.assertTrue(checks)
        for c in checks:
            self.assertEqual(c["verification"], "static_reference")
            self.assertFalse(c["verified"])
            self.assertIsNone(c["source_url"])
        self.assertEqual(r["data"]["verified_count"], 0)

    async def test_sea_gaps_say_not_verified(self):
        r = await cp.run("宠物用品", target_country="泰国")
        blob = " ".join(r["data"]["gaps"])
        self.assertIn("静态分级参考", blob)
        self.assertIn("须经官方门户", blob)

    async def test_sea_does_not_borrow_us_clauses(self):
        """东南亚库里不许出现 FDA/FCC 这类美国机构——那是最典型的假合规。"""
        r = await cp.run("3C电子", target_country="越南")
        authorities = {c["authority"] for c in r["data"]["checks"]}
        self.assertFalse(any("FDA" in a or "FCC" in a or "CPSC" in a
                             for a in authorities), authorities)

    async def test_market_alias_is_case_insensitive(self):
        r = await cp.run("宠物用品", target_country="indonesia")
        self.assertTrue(r["success"])
        self.assertEqual(r["data"]["market"], "ID")


class TestUsReal(unittest.IsolatedAsyncioTestCase):
    async def test_us_uses_official_source(self):
        with mock.patch.object(cp.compliance_tool, "run",
                               mock.AsyncMock(return_value=_us_outcome())):
            r = await cp.run("宠物用品", target_country="美国")
        self.assertEqual(r["data_quality"], "real")
        self.assertEqual(r["data"]["verified_count"], 2)
        self.assertTrue(r["data"]["provenance_ids"])

    async def test_zero_verified_degrades_to_sample(self):
        """一条都没定位到原文时不能标 real——那就只是编辑性知识。"""
        with mock.patch.object(cp.compliance_tool, "run",
                               mock.AsyncMock(return_value=_us_outcome(verified=0))):
            r = await cp.run("宠物用品", target_country="美国")
        self.assertEqual(r["data_quality"], "sample")
        self.assertTrue(any("没有一条要求在官方页面定位到原文"
                            in g for g in r["data"]["gaps"]))

    async def test_tool_failure_is_surfaced(self):
        bad = ToolOutcome(False, "error", None, "抓取失败")
        with mock.patch.object(cp.compliance_tool, "run",
                               mock.AsyncMock(return_value=bad)):
            r = await cp.run("宠物用品", target_country="美国")
        self.assertFalse(r["success"])
        self.assertIn("抓取失败", r["message"])

    async def test_us_checks_get_level_from_kind(self):
        """美国官方源按发布主体归类，不硬造"认证"子类。"""
        with mock.patch.object(cp.compliance_tool, "run",
                               mock.AsyncMock(return_value=_us_outcome())):
            r = await cp.run("宠物用品", target_country="美国")
        by_item = {c["item"]: c for c in r["data"]["checks"]}
        self.assertEqual(by_item["要求0"]["level_key"], "certification")
        self.assertEqual(by_item["平台发布规则"]["level_key"], "platform")


class TestNoFabrication(unittest.IsolatedAsyncioTestCase):
    async def test_no_tariff_number_anywhere(self):
        """不许编税率：输出里不能有 tax_rate / duty_pct 这类具体数字字段。"""
        for country in ("美国", "印尼", "泰国"):
            with mock.patch.object(cp.compliance_tool, "run",
                                   mock.AsyncMock(return_value=_us_outcome())):
                r = await cp.run("宠物用品", target_country=country)
            blob = str(r["data"])
            for bad in ("tax_rate", "duty_pct", "total_tax"):
                self.assertNotIn(bad, blob, f"{country} 输出里出现了 {bad}")

    async def test_risk_basis_says_not_legal_conclusion(self):
        r = await cp.run("宠物用品", target_country="印尼")
        self.assertIn("不是该商品的法律合规结论", r["data"]["risk_basis"])

    async def test_recommendations_point_at_unverified_mandatory(self):
        r = await cp.run("3C电子", target_country="马来西亚")
        recs = r["data"]["recommendations"]
        self.assertTrue(recs)
        self.assertTrue(any("优先办理" in x for x in recs))


if __name__ == "__main__":
    unittest.main()
