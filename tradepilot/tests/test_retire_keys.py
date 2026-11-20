# Copyright 2026 TradePilot project. Apache-2.0.
"""密钥实体清理脚本的红线测试。

红线：没有 --revoked 确认时，哪怕调用 --apply 也绝不删除本地明文密钥。
防止出现"先把文件删了、以为已经吊销"的人为事故；日志也不得写入密钥内容。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import retire_keys  # noqa: E402


class TestRetireKeys(unittest.TestCase):
    SECRET = "this-value-must-never-appear-in-logs"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.key = self.root / ".llm_key"
        self.key.write_text(self.SECRET, encoding="utf-8")
        self.log = self.root / "docs" / "security" / "key-retirement.log"

    def tearDown(self):
        self._tmp.cleanup()

    def test_apply_without_revoked_is_refused(self):
        code = retire_keys.retire_keys(
            self.root,
            [Path(".llm_key")],
            revoked=False,
            apply=True,
            log_path=self.log,
        )
        self.assertEqual(code, 2)
        self.assertTrue(self.key.exists())
        self.assertEqual(self.key.read_text(encoding="utf-8"), self.SECRET)
        self.assertFalse(self.log.exists())

    def test_check_mode_reports_without_deleting(self):
        code = retire_keys.retire_keys(
            self.root,
            [Path(".llm_key")],
            revoked=False,
            apply=False,
            log_path=self.log,
        )
        self.assertEqual(code, 0)
        self.assertTrue(self.key.exists())
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("PRESENT .llm_key", text)
        self.assertIn("(not deleted)", text)
        self.assertNotIn(self.SECRET, text)

    def test_revoked_apply_deletes_and_logs_hash_prefix_only(self):
        code = retire_keys.retire_keys(
            self.root,
            [Path(".llm_key")],
            revoked=True,
            apply=True,
            log_path=self.log,
        )
        self.assertEqual(code, 0)
        self.assertFalse(self.key.exists())
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("DELETED .llm_key", text)
        self.assertIn("sha256=", text)
        self.assertNotIn(self.SECRET, text)
        self.assertNotIn(len(self.SECRET) * "x", text)

    def test_missing_file_is_reported_not_fatal(self):
        code = retire_keys.retire_keys(
            self.root,
            [Path(".listinggood_key")],
            revoked=True,
            apply=True,
            log_path=self.log,
        )
        self.assertEqual(code, 0)
        self.assertIn("MISSING .listinggood_key", self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
