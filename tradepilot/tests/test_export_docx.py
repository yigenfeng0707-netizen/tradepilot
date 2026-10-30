# Copyright 2026 TradePilot project. Apache-2.0.
"""DOCX 导出的离线单测：只用标准库拆包断言，不依赖 Word / python-docx。

为什么这样验证：.docx 只是「zip + XML」，所以合法性完全可以机器复核——
    zipfile.is_zipfile → 必需部件齐全 → xml.etree 能解析 document.xml
    → 把 <w:t> 文本拼起来断言内容 → 数 <w:tbl>/<w:tc> 证明是真表格
    → 合规四态逐个断言，blocked/offline 不得出现「已定位官方条款原文」
这样连「Word 能不能打开」之前的一层结构错误都能拦住，CI 也不需要装 Office。
"""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from unittest import mock

from app.export_docx import COMPLIANCE_MARKS, build_docx

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

REQUIRED_PARTS = ("[Content_Types].xml", "_rels/.rels", "word/document.xml")


def _report() -> dict:
    """一份带齐七个章节的样例报告（字段与 store.get_mission 的真实结构一致）。"""
    return {
        "mission_id": "mission-20260101-101010",
        "goal": "把这款狗床卖到美国站 & 控制毛利 < 25%",
        "started_at": "2026-01-01T10:10:10",
        "finished_at": "2026-01-01T10:11:40",
        "elapsed_s": 90.0,
        "plan_engine": "llm",
        "llm_model": "internlm",
        "autonomy": {"llm_decisions": 7, "llm_overrides": 2, "retries": 1,
                     "steps_degraded": ["compliance_check"]},
        "decisions": [
            {"n": 1, "tool": "market_scan", "attempt": 1, "status": "done",
             "summary": "真实采集 12 条", "reason": "目标是选品与定价"},
            {"n": 2, "tool": "pricing_sandbox", "attempt": 2, "status": "done",
             "summary": "报价下限 27.40", "reason": "首次缺汇率，重试补齐"},
        ],
        "results": {
            "market_scan": {"ok": True, "status": "done", "gate": None,
                            "summary": "真实采集 12 条", "data": {
                                "keyword": "dog bed", "keyword_used": "狗床",
                                "source": "dangdang", "currency": "CNY",
                                "source_url": "https://search.dangdang.com/dogbed",
                                "scan_provenance_id": 7,
                                "items": [
                                    {"title": "可拆洗狗床 大型犬 <加厚>",
                                     "price": 89.0, "currency": "CNY",
                                     "source_url": "https://item.dangdang.com/1.html",
                                     "provenance_id": 11},
                                    {"title": "四季通用宠物垫",
                                     "price": 129.5, "currency": "CNY",
                                     "source_url": "https://item.dangdang.com/2.html",
                                     "provenance_id": 12},
                                ],
                                "price_band": {"min": 89.0, "median": 129.5,
                                               "max": 129.5},
                            }},
            "fx_rate": {"ok": True, "status": "done", "gate": None,
                        "summary": "USD/CNY 7.1234", "data": {}},
            "pricing_sandbox": {"ok": True, "status": "done", "gate": None,
                                "summary": "报价下限 27.40", "data": {
                                    "fx_rate": 7.1234, "fx_provenance_id": 21,
                                    "cost_basis_cny": 89.0, "unit_cost_usd": 12.49,
                                    "cost_basis_note": "零售价 ×0.35",
                                    "landed_cost": 27.4,
                                    "cost_breakdown": {"unit_cost_usd": 12.49,
                                                       "freight_usd": 2.5,
                                                       "duty_usd": 0.87,
                                                       "last_mile_usd": 5.5},
                                    "floor_price": 27.4,
                                    "band_source": "真实抓取价格带中位数",
                                    "suggestion": {"positioning": "中高价切入"}}},
            "compliance_check": {"ok": True, "status": "done", "gate": None,
                                 "summary": "0/4 条已定位原文", "data": {
                                     "category": "pet",
                                     "basis": "条款正文为编辑性知识，不构成法律意见。",
                                     "checks": [
                                         {"kind": "authority", "item": "犬粮标签",
                                          "authority": "FDA",
                                          "verification": "clause_matched",
                                          "page_url": "https://www.fda.gov/pet-food",
                                          "excerpt": "Labeling must include net quantity.",
                                          "matched_terms": ["labeling", "net quantity"],
                                          "provenance_id": 31},
                                         {"kind": "authority", "item": "进口申报",
                                          "authority": "CBP",
                                          "verification": "page_only",
                                          "page_url": "https://www.cbp.gov/import",
                                          "excerpt": None, "matched_terms": [],
                                          "provenance_id": 32},
                                         {"kind": "authority", "item": "加州 Prop 65",
                                          "authority": "OEHHA",
                                          "verification": "blocked",
                                          "page_url": "https://oehha.ca.gov/prop65",
                                          "block_reason": "WAF 拦截页",
                                          "excerpt": None, "matched_terms": [],
                                          "provenance_id": None},
                                         {"kind": "authority", "item": "FTC 广告规则",
                                          "authority": "FTC",
                                          "verification": "offline",
                                          "page_url": "https://www.ftc.gov/business",
                                          "excerpt": None, "matched_terms": [],
                                          "provenance_id": None},
                                     ]}},
            "keyword_plan": {"ok": True, "status": "done", "gate": None,
                             "summary": "词表已生成", "data": {
                                 "buckets": {"core": [{"english": ["dog bed"],
                                                       "observed": "狗床", "count": 18}],
                                             "attribute": [{"english": ["washable"],
                                                            "observed": "可拆洗",
                                                            "count": 9}]},
                                 "competition_proxy": {"sample_size": 12,
                                                       "core_term_coverage": 0.83,
                                                       "avg_title_len": 46.2},
                                 "search_terms": "dog bed washable pet mat",
                                 "search_terms_bytes": 28}},
            "listing_draft": {"ok": True, "status": "done", "gate": None,
                              "summary": "文案已生成", "data": {
                                  "generated_by": "llm",
                                  "copy": {"title": "Washable Dog Bed & Mat",
                                           "bullets": ["Soft & washable",
                                                       "Non-slip base"],
                                           "description": "A cozy bed."}}},
            "listing_audit": {"ok": True, "status": "done", "gate": None,
                              "summary": "92 分", "data": {
                                  "score": 92, "grade": "A", "verdict": "可上架",
                                  "counts": {"critical": 0, "warning": 1, "info": 2},
                                  "issues": [{"level": "warning", "rule": "PROMO_WORD",
                                              "field": "bullets",
                                              "evidence": "best seller",
                                              "fix": "删除绝对化用语"}],
                                  "basis": "规则表为编辑性知识，非平台官方判定。"}},
        },
        "provenance": {"count": 12},
    }


def _unzip(payload: bytes) -> dict[str, bytes]:
    assert isinstance(payload, (bytes, bytearray)), "build_docx 必须返回 bytes"
    assert zipfile.is_zipfile(BytesIO(payload)), "产物不是合法 zip"
    with zipfile.ZipFile(BytesIO(payload)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _document_xml(payload: bytes) -> bytes:
    return _unzip(payload)["word/document.xml"]


def _plain_text(payload: bytes) -> str:
    """把每个 <w:p> 内的 <w:t> 拼成一行、段落之间换行——等价于「全文搜索」。"""
    return "\n".join(_paragraphs(payload))


def _paragraphs(payload: bytes) -> list[str]:
    root = ET.fromstring(_document_xml(payload))
    out = []
    for p in root.iter(f"{W}p"):
        text = "".join(node.text or "" for node in p.iter(f"{W}t"))
        if text:
            out.append(text)
    return out


class TestDocxPackage(unittest.TestCase):
    """包结构：三个必需部件 + 合法 XML + zip 完整性。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = build_docx(_report())

    def test_returns_bytes_and_valid_zip(self):
        self.assertIsInstance(self.payload, bytes)
        self.assertTrue(self.payload.startswith(b"PK"))
        self.assertTrue(zipfile.is_zipfile(BytesIO(self.payload)))

    def test_required_parts_present(self):
        parts = _unzip(self.payload)
        for name in REQUIRED_PARTS:
            self.assertIn(name, parts, f"缺少必需部件 {name}")
            self.assertTrue(parts[name].strip(), f"部件 {name} 为空")

    def test_content_types_declares_main_document(self):
        types = _unzip(self.payload)["[Content_Types].xml"].decode("utf-8")
        self.assertIn("wordprocessingml.document.main+xml", types)
        self.assertIn("word/document.xml", types)

    def test_document_xml_is_parseable(self):
        root = ET.fromstring(_document_xml(self.payload))
        self.assertEqual(root.tag, f"{W}document")
        self.assertIsNotNone(root.find(f"{W}body"))
        # 页面设置必须在 sectPr 里，否则 Word 打开会用奇怪默认值
        self.assertIsNotNone(root.find(f".//{W}sectPr"))

    def test_zip_crc_ok(self):
        """testzip() == None 表示所有条目的 CRC 都对（半截写坏会在这里暴露）。"""
        with zipfile.ZipFile(BytesIO(self.payload)) as zf:
            self.assertIsNone(zf.testzip())


class TestDocxContent(unittest.TestCase):
    """内容口径：七章齐全、关键数字来自报告、表格是真表格。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = build_docx(_report())
        cls.text = _plain_text(cls.payload)
        cls.paragraphs = _paragraphs(cls.payload)

    def test_title_and_sections(self):
        self.assertIn("上架包 · mission-20260101-101010", self.text)
        for heading in ("一、真实市场数据", "二、定价沙盘", "三、合规体检",
                        "四、关键词层", "五、Listing 文案", "六、文案合规审计",
                        "七、决策实录"):
            self.assertIn(heading, self.text, f"缺少章节：{heading}")
        # 章节顺序必须与 Markdown 导出一致
        positions = [self.text.index(h) for h in ("一、真实市场数据", "二、定价沙盘",
                                                 "三、合规体检", "四、关键词层",
                                                 "五、Listing 文案", "六、文案合规审计",
                                                 "七、决策实录")]
        self.assertEqual(positions, sorted(positions))

    def test_meta_and_numbers(self):
        for needle in ("目标：把这款狗床卖到美国站 & 控制毛利 < 25%",
                       "规划引擎：llm", "LLM 决策 7 次", "覆盖规则序 2 次", "重试 1 次",
                       "降级步骤：compliance_check",
                       "27.4", "7.1234", "89", "12.49",
                       "92 分 / A 级 — 可上架", "样本 12 条真实竞品标题",
                       "核心词覆盖率 83%", "标题均长 46.2 字",
                       "Washable Dog Bed & Mat", "可拆洗狗床",
                       "1. /market_scan（第 1 次）→ done"):
            self.assertIn(needle, self.text, f"正文缺少关键内容：{needle}")

    def test_market_table_is_real_table(self):
        root = ET.fromstring(_document_xml(self.payload))
        tables = list(root.iter(f"{W}tbl"))
        # 商品表至少存在，且结构与列数（#/商品/价格/单品页/存证）匹配
        self.assertGreaterEqual(len(tables), 1, "没有 <w:tbl>，说明表格被写成竖线段落")
        first = tables[0]
        self.assertIsNotNone(first.find(f"{W}tblGrid"))
        rows = list(first.findall(f"{W}tr"))
        self.assertEqual(len(rows), 1 + 2)  # 表头 + 2 条商品
        self.assertEqual(len(rows[0].findall(f"{W}tc")), 5)
        # 报告里没有「|」式伪表格：竖线不应出现在任何段落文本里
        self.assertNotIn("|", self.text)

    def test_traceability_ids_and_urls(self):
        self.assertIn("存证#11", self.text)
        self.assertIn("存证#12", self.text)
        self.assertIn("https://item.dangdang.com/1.html", self.text)
        self.assertIn("价格带：89 / 129.5 / 129.5 CNY", self.text)
        self.assertIn("https://search.dangdang.com/dogbed", self.text)
        self.assertIn("存证#21", self.text)          # 汇率存证
        self.assertIn("数据来源与抓取时间戳见来源存证库", self.text)
        self.assertIn("数字均来自真实抓取与确定性计算", self.text)

    def test_compliance_four_states(self):
        """四态各出现一次，且 blocked/offline 不得被写成「已验证」。"""
        for code, mark in COMPLIANCE_MARKS.items():
            flagged = [p for p in self.paragraphs if f"（{code}）" in p]
            self.assertEqual(len(flagged), 1, f"{code} 状态未渲染或重复")
            self.assertIn(mark, flagged[0])

        verified = [p for p in self.paragraphs
                    if "已定位官方条款原文" in p and "（clause_matched）" in p]
        self.assertEqual(len(verified), 1)
        self.assertIn("https://www.fda.gov/pet-food", verified[0])
        self.assertIn("Labeling must include net quantity.", self.text)
        self.assertIn("存证#31", self.text)

        # 诚实红线：只有 clause_matched 允许出现「已定位官方条款原文」
        for code in ("blocked", "offline", "page_only"):
            for p in (p for p in self.paragraphs if f"（{code}）" in p):
                self.assertNotIn("已定位官方条款原文", p, f"{code} 被渲染成已验证")
                self.assertNotIn("✅", p, f"{code} 用了已验证的勾号")
        blocked = [p for p in self.paragraphs if "（blocked）" in p][0]
        self.assertIn("拦截页/SPA 空壳/软 404", blocked)
        self.assertIn("WAF 拦截页", blocked)     # block_reason 必须带出来
        offline = [p for p in self.paragraphs if "（offline）" in p][0]
        self.assertIn("网络不可达", offline)
        # blocked/offline 没有可核验正文时，必须明说「不作为合规依据」
        self.assertIn("未取得可核验正文，不作为合规依据", self.text)


class TestDocxEscaping(unittest.TestCase):
    """XML 转义：中文、&、<、引号都不能把 document.xml 弄坏。"""

    def test_special_characters_are_escaped(self):
        report = _report()
        report["goal"] = 'A & B <script>alert("x")</script> \'quoted\' 中文'
        report["results"]["listing_draft"]["data"]["copy"]["title"] = "<b>粗</b> & 斜"
        payload = build_docx(report)
        raw = _document_xml(payload).decode("utf-8")

        self.assertIn("&amp;", raw)
        self.assertIn("&lt;script&gt;", raw)
        self.assertNotIn("<script>", raw, "原始尖括号未转义，XML 会被破坏")
        self.assertIn("&quot;", raw)
        self.assertIn("&apos;", raw)
        ET.fromstring(raw.encode("utf-8"))       # 转义失败这里会抛 ParseError
        text = _plain_text(payload)
        self.assertIn("A & B <script>alert(\"x\")</script> 'quoted' 中文", text)
        self.assertIn("<b>粗</b> & 斜", text)

    def test_control_characters_removed(self):
        report = _report()
        report["goal"] = "标题含控制字符\x00\x07\x1f，必须剔除"
        payload = build_docx(report)
        raw = _document_xml(payload).decode("utf-8")
        self.assertNotIn("\x00", raw)
        self.assertNotIn("\x07", raw)
        ET.fromstring(raw.encode("utf-8"))
        self.assertIn("标题含控制字符，必须剔除", _plain_text(payload))


class TestDocxDegenerateReports(unittest.TestCase):
    """非法/空报告：宁可写「未生成」，也不能 500。"""

    def _assert_ok(self, report):
        payload = build_docx(report)  # type: ignore[arg-type]
        parts = _unzip(payload)
        for name in REQUIRED_PARTS:
            self.assertIn(name, parts)
        root = ET.fromstring(parts["word/document.xml"])
        self.assertEqual(root.tag, f"{W}document")
        self.assertIn("上架包", _plain_text(payload))
        return _plain_text(payload)

    def test_empty_dict(self):
        text = self._assert_ok({})
        self.assertIn("（无任务编号）", text)
        self.assertIn("未取得真实商品数据", text)
        self.assertIn("（未生成定价结论）", text)

    def test_none_and_non_dict(self):
        for bad in (None, "junk", 0, [], ["a"]):
            self._assert_ok(bad)  # type: ignore[arg-type]

    def test_wrong_types_inside_report(self):
        report = {
            "mission_id": 12345,             # 非字符串
            "results": {"market_scan": {"data": {"items": "not-a-list"}},
                        "compliance_check": {"data": {"checks": [None, 3]}},
                        "pricing_sandbox": {"data": []},
                        "keyword_plan": {"data": {"buckets": {"core": "x"}}},
                        "listing_draft": {"data": {"copy": {"title": 7}}},
                        "listing_audit": {"data": {"issues": [{"level": None}]}}},
            "decisions": "not-a-list",
            "autonomy": {"steps_degraded": None},
        }
        text = self._assert_ok(report)
        self.assertIn("12345", text)                 # 非字符串 id 也能渲染
        self.assertIn("未取得真实商品数据", text)      # items 不是列表 → 如实留空
        self.assertIn("（未生成定价结论）", text)      # data 是 [] → 视作缺失
        self.assertIn("Title（1 字符）", text)         # 非字符串 title 仍能安全转文本

    def test_missing_autonomy_and_decisions(self):
        text = self._assert_ok({"mission_id": "mission-20260101-000000"})
        self.assertIn("降级步骤：无", text)
        self.assertIn("（无决策记录）", text)


class TestExportRoute(unittest.TestCase):
    """路由分支：docx 返回二进制附件，md/json 行为保持不变。"""

    def setUp(self):
        from app import main as M          # 延迟导入：测试文件被 discover 时不必装 fastapi
        self.M = M
        self.report = _report()

    def _call(self, fmt: str):
        import asyncio

        with mock.patch.object(self.M.store, "get_mission", return_value=self.report):
            return asyncio.run(self.M.mission_export("mission-20260101-101010", format=fmt))

    def test_docx_branch(self):
        resp = self._call("docx")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.media_type,
                         "application/vnd.openxmlformats-officedocument"
                         ".wordprocessingml.document")
        self.assertIn('filename="mission-20260101-101010.docx"',
                      resp.headers["content-disposition"])
        self.assertTrue(zipfile.is_zipfile(BytesIO(resp.body)))
        self.assertIn("一、真实市场数据", _plain_text(resp.body))

    def test_md_and_json_unchanged(self):
        md = self._call("md")
        self.assertEqual(md.media_type, "text/markdown; charset=utf-8")
        self.assertIn('filename="mission-20260101-101010.md"',
                      md.headers["content-disposition"])
        self.assertTrue(md.body.decode("utf-8").startswith("# 上架包"))
        js = self._call("json")
        self.assertEqual(js.media_type, "application/json")
        self.assertIn('filename="mission-20260101-101010.json"',
                      js.headers["content-disposition"])

    def test_unknown_format_rejected(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self._call("pdf")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("docx", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
