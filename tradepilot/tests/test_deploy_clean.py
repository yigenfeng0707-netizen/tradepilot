# Copyright 2026 TradePilot project. Apache-2.0.
"""部署前清理脚本的测试：stash → restore 必须是一次可逆的搬家。

背景事故（2026-10-31）：sites 部署会把整个目录打包上传，只排除
node_modules/.git/构建产物，于是本机的 `assets/provenance.db`、`assets/missions/`、
`.llm_key` 全被传到了沙箱——线上任务列表里躺着本地测试任务，且线上跑的是
本机 LLM Key。这个脚本就是那次事故的修复，用例要保证它真能搬干净、也能搬回来。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_clean.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, TRADEPILOT_DEPLOY_ROOT=str(cwd))
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))


class TestDeployClean(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "assets" / "missions").mkdir(parents=True)
        (self.dir / "assets" / "provenance.db").write_text("db", encoding="utf-8")
        (self.dir / "assets" / "missions" / "m1.json").write_text("{}", encoding="utf-8")
        (self.dir / ".llm_key").write_text("sk-x", encoding="utf-8")
        (self.dir / ".listinggood_key").write_text("lg_live_x", encoding="utf-8")
        (self.dir / "app").mkdir()
        (self.dir / "app" / "main.py").write_text("# code", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stash_moves_runtime_data_and_leaves_code(self):
        r = _run(["stash"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.dir / "assets" / "provenance.db").exists())
        self.assertFalse((self.dir / "assets" / "missions").exists())
        # 代码必须留在原地，否则上传上去就是个空壳
        self.assertTrue((self.dir / "app" / "main.py").exists())
        self.assertTrue((self.dir / ".deploy_stash" / "assets" / "provenance.db").exists())

    def test_keys_untouched_unless_asked(self):
        """默认不动凭证——保留 LLM 模式是用户的选择，脚本不能擅自替他决定。"""
        _run(["stash"], self.dir)
        self.assertTrue((self.dir / ".llm_key").exists())
        self.assertTrue((self.dir / ".listinggood_key").exists())

    def test_keys_stashed_when_named(self):
        _run(["stash", "--keys", ".listinggood_key"], self.dir)
        self.assertFalse((self.dir / ".listinggood_key").exists())
        self.assertTrue((self.dir / ".llm_key").exists())
        self.assertTrue((self.dir / ".deploy_stash" / ".listinggood_key").exists())

    def test_restore_puts_everything_back(self):
        _run(["stash", "--keys", ".llm_key,.listinggood_key"], self.dir)
        r = _run(["restore"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.dir / "assets" / "provenance.db").read_text(encoding="utf-8"), "db")
        self.assertTrue((self.dir / "assets" / "missions" / "m1.json").exists())
        self.assertEqual((self.dir / ".llm_key").read_text(encoding="utf-8"), "sk-x")
        self.assertEqual((self.dir / ".listinggood_key").read_text(encoding="utf-8"), "lg_live_x")
        # 清单清空，避免下次 restore 找不存在的文件
        self.assertEqual(json.loads((self.dir / ".deploy_stash" / "manifest.json")
                                    .read_text(encoding="utf-8")), [])

    def test_second_stash_merges_manifest(self):
        """分两次 stash（先数据、再凭证）不能互相覆盖清单，否则 restore 会漏东西。"""
        _run(["stash"], self.dir)
        _run(["stash", "--keys", ".listinggood_key"], self.dir)
        manifest = json.loads((self.dir / ".deploy_stash" / "manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertIn("assets/provenance.db", manifest)
        self.assertIn(".listinggood_key", manifest)
        _run(["restore"], self.dir)
        self.assertTrue((self.dir / "assets" / "provenance.db").exists())
        self.assertTrue((self.dir / ".listinggood_key").exists())

    def test_stash_twice_does_not_crash(self):
        _run(["stash"], self.dir)
        r = _run(["stash"], self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("没有需要移出", r.stdout)

    def test_unknown_key_rejected(self):
        r = _run(["stash", "--keys", ".secret_unknown"], self.dir)
        self.assertEqual(r.returncode, 2)
        self.assertIn("未知凭证", r.stderr)

    def test_status_flags_dirty_directory(self):
        r = _run(["status"], self.dir)
        self.assertIn("含运行态数据", r.stdout)
        self.assertIn("含凭证文件", r.stdout)

    def test_status_clean_after_stash(self):
        _run(["stash"], self.dir)
        r = _run(["status"], self.dir)
        self.assertIn("仍在暂存区", r.stdout)
        self.assertNotIn("含运行态数据", r.stdout)


class TestDataDirOutsideUpload(unittest.TestCase):
    def test_default_data_dir_is_not_inside_repo(self):
        """回归守卫：数据目录若落在仓库内，部署会把本机存证库和历史任务一起传上沙箱。

        这正是 2026-10-31 的事故根因——线上任务列表挂着本地测试任务、
        存证条数不从 0 开始。所以默认数据目录必须在仓库之外。
        """
        from app import config

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRADEPILOT_DATA_DIR", None)
            resolved = config._resolve_data_dir()
        self.assertNotIn(str(config.REPO_ROOT).lower(), resolved.lower(),
                         f"默认数据目录 {resolved} 落在仓库内，部署时会随源码一起上传")
        self.assertTrue(Path(resolved).exists())

    def test_env_override_still_wins(self):
        from app import config

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"TRADEPILOT_DATA_DIR": d}, clear=False):
                self.assertEqual(config._resolve_data_dir(), d)


if __name__ == "__main__":
    unittest.main()
