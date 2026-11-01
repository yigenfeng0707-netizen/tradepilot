# Copyright 2026 TradePilot project. Apache-2.0.
"""外部复核在导出物里的呈现：md 与 docx 两条路径都要如实交代。

要守住的三条：
1. 外部复核拿到了结果 → 导出物里要有，且明确写"不计入上方评分"；
2. 未启用（off）→ 也要留一行说明，让读者知道这层被考虑过、按设计没开；
3. 取结果失败 → 说清失败原因，并明示本地审计结论不受影响。
"""
from __future__ import annotations

import unittest

from app.export_docx import build_docx
from app.store import export_markdown


def _report(audit_extra: dict | None = None) -> dict:
    data = {
        "score": 86, "grade": "A", "verdict": "可上架",
        "counts": {"critical": 0, "warning": 2, "info": 1},
        "issues": [{"level": "warning", "rule": "关键词堆砌", "field": "全文",
                    "evidence": "bowl×4", "fix": "用同义词覆盖"}],
        "basis": "规则为编辑性知识，不构成法律意见。",
    }
    data.update(audit_extra or {})
    return {
        "mission_id": "mission-20260101-101010",
        "goal": "把狗碗卖到美国站",
        "results": {
            "listing_draft": {"data": {"copy": {"title": "Dog Bowl", "bullets": ["b"]},
                                       "generated_by": "llm"}},
            "listing_audit": {"data": data},
        },
        "decisions": [],
        "autonomy": {},
    }


def _ext_ok() -> dict:
    return {
        "external_review": {
            "service": "ListingGood", "status": "ok", "mode": "deep",
            "endpoint": "/api/scan/deep", "score": 85, "level": "需关注",
            "issue_n": 2, "critical_n": 0, "latency_ms": 21764,
            "issues": [{"severity": "warning", "finding": "未明确排除儿童使用",
                        "evidence": "medium sized dog", "source": "CPC 认证要求"}],
            "note": "深度体检为 LLM 语义分析，含月度计费额度。",
        },
        "cross_check": {"verdict": "agree", "local_passed": True, "external_passed": True,
                        "local_score": 86, "external_score": 85, "external_only_n": 0},
    }


def _docx_text(report: dict) -> str:
    import re
    import zipfile
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(build_docx(report))) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.S))


class TestMarkdownExternalReview(unittest.TestCase):
    def test_ok_block_present_and_labeled_not_scored(self):
        md = export_markdown(_report(_ext_ok()))
        self.assertIn("外部复核", md)
        self.assertIn("不计入上方评分", md)
        self.assertIn("agree", md)
        self.assertIn("CPC 认证要求", md)

    def test_off_states_it_was_not_enabled(self):
        md = export_markdown(_report({
            "external_review": {"status": "off", "note": "未配置 LISTINGGOOD_API_KEY"},
            "cross_check": {"verdict": "skipped", "local_passed": True},
        }))
        self.assertIn("外部复核未启用", md)
        self.assertIn("未配置 LISTINGGOOD_API_KEY", md)

    def test_off_note_is_not_double_wrapped(self):
        """note 本身已是完整句子，模板不能再包一层——否则导出物里出现
        「外部复核未启用（外部复核未启用（…））」这种病句。"""
        note = "外部复核未启用（模式为 off）；本次结论仅来自本地规则审计。"
        md = export_markdown(_report({
            "external_review": {"status": "off", "note": note},
            "cross_check": {"verdict": "skipped"},
        }))
        self.assertNotIn("未启用（外部复核未启用", md)
        self.assertEqual(md.count("外部复核未启用"), 1)
        self.assertIn("本次结论仅来自本地规则审计", md)

    def test_off_without_note_still_readable(self):
        md = export_markdown(_report({
            "external_review": {"status": "off"},
            "cross_check": {"verdict": "skipped"},
        }))
        self.assertIn("外部复核未启用", md)

    def test_error_says_local_result_unaffected(self):
        md = export_markdown(_report({
            "external_review": {"status": "error", "summary": "ConnectError: boom"},
            "cross_check": {"verdict": "unavailable", "local_passed": True},
        }))
        self.assertIn("ConnectError", md)
        self.assertIn("本地审计结论不受影响", md)

    def test_no_external_keys_renders_nothing_extra(self):
        """老任务存档（没有这两个键）导出时不能被凭空加出一段。"""
        md = export_markdown(_report())
        self.assertNotIn("外部复核", md)


class TestDocxExternalReview(unittest.TestCase):
    def test_ok_block_present(self):
        text = _docx_text(_report(_ext_ok()))
        self.assertIn("外部复核", text)
        self.assertIn("不计入上方评分", text)
        self.assertIn("agree", text)

    def test_off_block_present(self):
        text = _docx_text(_report({
            "external_review": {"status": "off", "note": "模式为 off"},
            "cross_check": {"verdict": "skipped"},
        }))
        self.assertIn("外部复核未启用", text)

    def test_missing_keys_render_nothing(self):
        text = _docx_text(_report())
        self.assertNotIn("外部复核", text)

    def test_degenerate_external_payload_does_not_crash(self):
        text = _docx_text(_report({
            "external_review": "not-a-dict", "cross_check": None,
        }))
        self.assertIn("文案合规审计", text)


if __name__ == "__main__":
    unittest.main()
