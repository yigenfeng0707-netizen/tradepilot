# -*- coding: utf-8 -*-
"""运营后台鉴权的回归测试（M4）。

守的红线：
1. **fail closed**：TRADEPILOT_ADMIN_TOKEN 未配置时管理端点整体关闭，
   403 明说"未启用"——不冒充有后台，也没有默认令牌；
2. **令牌比对防时序**：secrets.compare_digest，不是 ==；
3. **独立于租户 JWT**：运营者是平台方，不是任何租户的成员——
   带租户令牌访问管理端点照样 401；
4. **导出无敏感材料**：overview 里的租户/用户清单不含密码散列与令牌哈希
   （auth.export_all 的红线，这里验证的是聚合响应也不引入）。
"""
import dataclasses
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app.config import CONFIG  # noqa: E402
from app import auth  # noqa: E402
from app.main import _admin_authorized  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-admin-test-")
auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir)


def setUpModule():
    auth.init_db()


def _req(token: str = ""):
    return SimpleNamespace(headers={"x-admin-token": token})


class TestAdminAuth(unittest.TestCase):
    def test_not_configured_fails_closed(self):
        """未配置令牌 = 后台关闭，403 而不是放行。"""
        with mock.patch.dict(os.environ, {"TRADEPILOT_ADMIN_TOKEN": ""}, clear=False):
            os.environ.pop("TRADEPILOT_ADMIN_TOKEN", None)
            with self.assertRaises(HTTPException) as ctx:
                _admin_authorized(_req("anything"))
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIn("未启用", ctx.exception.detail)

    def test_wrong_token_401(self):
        with mock.patch.dict(os.environ, {"TRADEPILOT_ADMIN_TOKEN": "s3cret"}):
            with self.assertRaises(HTTPException) as ctx:
                _admin_authorized(_req("wrong"))
            self.assertEqual(ctx.exception.status_code, 401)

    def test_empty_token_401_even_when_configured(self):
        with mock.patch.dict(os.environ, {"TRADEPILOT_ADMIN_TOKEN": "s3cret"}):
            with self.assertRaises(HTTPException) as ctx:
                _admin_authorized(_req(""))
            self.assertEqual(ctx.exception.status_code, 401)

    def test_correct_token_passes(self):
        with mock.patch.dict(os.environ, {"TRADEPILOT_ADMIN_TOKEN": "s3cret"}):
            _admin_authorized(_req("s3cret"))       # 不抛即通过

    def test_tenant_jwt_is_not_admin(self):
        """租户访问令牌不是运营凭证——两种身份体系互不通用。"""
        with mock.patch.dict(os.environ, {"TRADEPILOT_ADMIN_TOKEN": "s3cret"}):
            with self.assertRaises(HTTPException) as ctx:
                _admin_authorized(_req("Bearer eyJhbGciOiJIUzI1NiJ9.fake"))
            self.assertEqual(ctx.exception.status_code, 401)


class TestExportHygiene(unittest.TestCase):
    def test_export_all_has_no_secrets(self):
        """聚合响应的红线：密码散列与令牌哈希永不出现。"""
        auth.register("hygiene@example.com", "password123", "卫生检查公司")
        data = auth.export_all()
        blob = repr(data)
        self.assertNotIn("argon2id", blob)          # 密码散列
        self.assertNotIn("password_hash", blob)
        self.assertNotIn("token_hash", blob)
        self.assertTrue(all("email" in u for u in data["users"]))
