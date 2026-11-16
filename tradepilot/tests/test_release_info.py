# Copyright 2026 TradePilot project. Apache-2.0.
"""发布地址单一来源的守卫测试。

## 为什么值得单独立一个测试文件

2026-10-31 真出过事故：重新部署后域名变了，地址却硬编码在四个脚本里，
只改了其中三处——BP 里印着旧链接，而旧链接返回 403（网关报"不支持通过 SDK Token
直接访问"）。评委点开是死链。这类错误代码本身不报错、测试全绿、部署成功，
只有"人真的去点一下"才会发现，所以必须用机器守住。
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import release_info  # noqa: E402

# 发布域名的特征片段：出现在 release_info.py 之外就算硬编码
_DOMAIN_MARKS = ("ms.show", "apps.workbuddy.host")


class TestReleaseInfoParsing(unittest.TestCase):
    # 分享码不是恒定的：旧托管网关强制分享码（空码=线上 403），当前魔搭创空间部署
    # 无分享码闸门（SHARE_URL 不带 query）。守卫因此改为条件化——
    # 「带码就必须全程带、不带码就不许半路冒出来」，具体带码行为由
    # test_env_override_takes_effect 以固定输入锁定。
    def test_share_url_splits_into_origin_and_query(self):
        self.assertTrue(release_info.ORIGIN.startswith("https://"))
        self.assertNotIn("?", release_info.ORIGIN)
        if release_info.QUERY:
            self.assertIn("sharecode=", release_info.QUERY)

    def test_ws_url_points_at_mission_channel(self):
        self.assertTrue(release_info.WS_URL.startswith("wss://"))
        self.assertIn("/ws/mission", release_info.WS_URL)
        # 有分享码时必须带上（旧网关不带会 403）；无分享码时不得凭空造出 query
        if release_info.SHARECODE:
            self.assertIn("sharecode=", release_info.WS_URL)
        else:
            self.assertNotIn("sharecode=", release_info.WS_URL)

    def test_url_helper_consistent_with_query(self):
        u = release_info.url("/api/tools")
        self.assertTrue(u.startswith(release_info.ORIGIN + "/api/tools"))
        if release_info.QUERY:
            self.assertTrue(u.endswith("?" + release_info.QUERY))
        else:
            self.assertNotIn("sharecode=", u)

    def test_sharecode_matches_query(self):
        """SHARECODE 必须与 SHARE_URL 的 query 一致，不能各自为政。"""
        if "sharecode=" in release_info.QUERY:
            expected = release_info.QUERY.split("sharecode=", 1)[1].split("&", 1)[0]
            self.assertEqual(release_info.SHARECODE, expected)
        else:
            self.assertEqual(release_info.SHARECODE, "")

    def test_env_override_takes_effect(self):
        """换环境靠环境变量，不改代码。"""
        with mock.patch.dict(os.environ, {
                "TRADEPILOT_SHARE_URL": "https://example.test/?sharecode=ABC123"}, clear=False):
            mod = importlib.reload(release_info)
            try:
                self.assertEqual(mod.ORIGIN, "https://example.test")
                self.assertEqual(mod.SHARECODE, "ABC123")
                self.assertEqual(mod.WS_URL, "wss://example.test/ws/mission?sharecode=ABC123")
            finally:
                os.environ.pop("TRADEPILOT_SHARE_URL", None)
                importlib.reload(release_info)


class TestNoHardcodedDeployUrl(unittest.TestCase):
    def test_only_release_info_may_contain_the_domain(self):
        """`scripts/` 下除 release_info.py 外，任何文件都不得再写死发布域名。

        这条用例就是那次死链事故的回归测试：只要有人图省事又复制一遍地址，它立刻红。
        """
        offenders: list[str] = []
        for path in sorted(SCRIPTS.glob("*.py")):
            if path.name == "release_info.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for mark in _DOMAIN_MARKS:
                if mark in text:
                    offenders.append(f"{path.name}: 含 {mark}")
        self.assertEqual(offenders, [],
                         "发布地址必须从 release_info 取，不要硬编码：\n" + "\n".join(offenders))

    def test_consumers_import_release_info(self):
        """BP / 视频 / 云端验证 / 漂移检查这四个消费方都要真的引用它。"""
        for name in ("build_bp.py", "build_video.py", "verify_cloud.py",
                     "check_release_drift.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8", errors="ignore")
            self.assertIn("release_info", text, f"{name} 未引用 release_info")


class TestBpEmbedsCurrentLink(unittest.TestCase):
    def test_bp_source_uses_share_url_not_a_literal(self):
        text = (SCRIPTS / "build_bp.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("release_info.SHARE_URL", text)
        # 不能出现"附：作品链接 http..."这种写死形式
        self.assertIsNone(re.search(r"作品链接\s+https?://", text),
                          "BP 里的作品链接必须来自 release_info.SHARE_URL")


if __name__ == "__main__":
    unittest.main()
