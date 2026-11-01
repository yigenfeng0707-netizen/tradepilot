# -*- coding: utf-8 -*-
"""lifespan 初始化的回归测试（容器冒烟抓到的真实 bug）。

事故：lifespan 漏了 auth.init_db()——开发机 data_dir 里总有旧库掩盖缺口，
干净容器（挂空卷）启动后一注册就 `no such table: tenants`。
此测试在全新临时目录上跑 lifespan 启动段，守护"每个持久化模块都被初始化"。
"""
import asyncio
import dataclasses
import tempfile
import unittest
from contextlib import AsyncExitStack

from app import auth, quota, shops, usage
from app.config import CONFIG
from app.main import lifespan


class TestLifespanInit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="tradepilot-lifespan-test-")
        self._saved = (auth.CONFIG, quota.CONFIG, usage.CONFIG, shops.CONFIG)
        auth.CONFIG = dataclasses.replace(CONFIG, data_dir=tmp, db_path="")
        quota.CONFIG = dataclasses.replace(CONFIG, data_dir=tmp, db_path="")
        usage.CONFIG = dataclasses.replace(CONFIG, data_dir=tmp, db_path="")
        shops.CONFIG = dataclasses.replace(CONFIG, data_dir=tmp, db_path="")

    def tearDown(self):
        auth.CONFIG, quota.CONFIG, usage.CONFIG, shops.CONFIG = self._saved

    def test_lifespan_initializes_all_stores(self):
        async def run():
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(lifespan(None))
                reg = auth.register("lifespan@example.com", "password123", "寿命测试")
                self.assertTrue(reg.get("ok"), reg)
                # 其余库也应已初始化（能写即已建表）
                quota.consume(reg["tenant_id"], 1)
                shops.authorize(reg["tenant_id"], "shopee", "s1")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
