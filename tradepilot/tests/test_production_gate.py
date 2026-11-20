# Copyright 2026 TradePilot project. Apache-2.0.
"""生产门禁纯函数测试：验证"缺证据即失败、不估算、不误判"的红线。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import production_gate as gate  # noqa: E402


def green_evidence() -> dict:
    return {
        "backend_tests": {"passed": 574, "failed": 0},
        "frontend_build": {"tsc_errors": 0, "build_ok": True},
        "github_ci": {"status": "completed", "conclusion": "success",
                      "run_id": "44706586873", "head_sha": "abc123"},
        "branch_sync": {"ahead": 0, "behind": 0},
        "secrets": {"tracked_matches": 0, "plaintext_files": [], "gitignore_ok": True},
        "shopify_live": {"verified": True, "products": 3, "orders": 1},
        "human_eval": {"baseline_null": False, "spotcheck_checked": 30, "spotcheck_total": 30},
        "pricing": {"decided": True, "file": "config/pricing.json"},
        "docs": {"drift": False, "note": "已同步"},
    }


class TestProductionGate(unittest.TestCase):
    def test_all_green(self):
        results = gate.evaluate(green_evidence(), head_commit="abc123")
        self.assertEqual(len(results), 8)
        self.assertTrue(all(item.ok for item in results),
                        [item.detail for item in results if not item.ok])

    def test_missing_evidence_fails_instead_of_assuming(self):
        results = gate.evaluate({}, head_commit="abc123")
        self.assertEqual([item.gate for item in results if not item.ok],
                         ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"])

    def test_ci_sha_must_match_current_commit(self):
        evidence = green_evidence()
        evidence["github_ci"]["head_sha"] = "old-commit"
        results = {item.gate: item for item in gate.evaluate(evidence, head_commit="abc123")}
        self.assertFalse(results["G2"].ok)
        self.assertIn("sha_matches=False", results["G2"].detail)

    def test_human_nulls_block_gate(self):
        evidence = green_evidence()
        evidence["human_eval"] = {"baseline_null": True, "spotcheck_checked": 0,
                                  "spotcheck_total": 30}
        results = {item.gate: item for item in gate.evaluate(evidence, head_commit="abc123")}
        self.assertFalse(results["G6"].ok)
        self.assertFalse(results["G6"].detail.startswith("baseline_null=False"))

    def test_plaintext_key_blocks_gate(self):
        evidence = green_evidence()
        evidence["secrets"]["plaintext_files"] = ["tradepilot/.llm_key"]
        results = {item.gate: item for item in gate.evaluate(evidence, head_commit="abc123")}
        self.assertFalse(results["G4"].ok)

    def test_secret_detector_ignores_documented_examples(self):
        self.assertFalse(gate._looks_like_secret("SHOPIFY_TOKEN=shpat_xxxxxxxxxxxxxxxx"))
        self.assertFalse(gate._looks_like_secret("token=shpat_ABCDEFGH12345678"))
        self.assertTrue(gate._looks_like_secret("token=shpat_ABCDEFGH12345678XYZW"))


if __name__ == "__main__":
    unittest.main()
