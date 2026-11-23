# -*- coding: utf-8 -*-
"""租户隔离接入业务数据的回归测试（M3-2）。

守的红线：
1. **登录后收紧**：带有效令牌的请求只能看到自己租户的任务存档；
2. **跨租户 = 不存在**：get 返回同样的 None，404 不泄露"存在但不属于你"；
3. **legacy 存档不冒充归属**：无 tenant_id 的旧存档对登录租户不可见，
   只在单机/演示模式（未认证）可见；
4. **存证库不隔离**：provenance 是数据信任层的全局事实库 + 防编造闸门，
   刻意不按租户切分——这是设计决策不是遗漏。

测试手法与 test_auth 一致：给模块一份独立 Config（只换 data_dir），
不污染全局单例。store 额外在 setUpClass 里用全新目录——store.CONFIG 是
模块级全局，其他测试模块的 save_mission 也写进"当前"目录，不隔离会互相污染。
"""
import dataclasses
import tempfile
import unittest

from app.config import CONFIG  # noqa: E402
from app import auth, store  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-tenant-test-")
_orig_store_config = store.CONFIG   # 模块结束必须还原：此前只替换不还原，
_orig_auth_config = auth.CONFIG     # 后续测试模块的 save_mission 会写进本目录
store.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir)
auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir)


def setUpModule():
    auth.init_db()


def tearDownModule():
    store.CONFIG = _orig_store_config
    auth.CONFIG = _orig_auth_config


def _mission(mid: str, goal: str) -> dict:
    """构造一份能通过 _SAFE_ID 校验的最小报告。"""
    return {"mission_id": mid, "goal": goal, "started_at": "2026-11-01T00:00:00",
            "elapsed_s": 1.0, "plan_engine": "rules", "decisions": [],
            "autonomy": {}, "results": {}}


class TestTenantIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 每类一个全新目录：store.CONFIG 是模块级全局，其他测试模块的
        # save_mission 也会写进当前 data_dir——不隔离会互相污染全集断言。
        cls._orig = store.CONFIG
        cls._dir = tempfile.mkdtemp(prefix="tradepilot-tenant-case-")
        store.CONFIG = dataclasses.replace(CONFIG, data_dir=cls._dir)

    @classmethod
    def tearDownClass(cls):
        store.CONFIG = cls._orig

    def setUp(self):
        # 三个租户视角：A 有任务，B 有任务，legacy 无归属
        self.a1 = store.save_mission(_mission("mission-20261101-010101", "A 的任务"),
                                     tenant_id="tenant-A")
        self.b1 = store.save_mission(_mission("mission-20261101-010202", "B 的任务"),
                                     tenant_id="tenant-B")
        self.legacy = store.save_mission(_mission("mission-20261101-010303", "旧演示任务"))

    def test_save_writes_tenant_field(self):
        """租户 id 必须真实落盘，不是只在返回值里说说。"""
        import json
        import os
        base = os.path.join(type(self)._dir, "missions")
        with open(os.path.join(base, "mission-20261101-010101.json"),
                  encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw.get("tenant_id"), "tenant-A")
        with open(os.path.join(base, "mission-20261101-010303.json"),
                  encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertIsNone(raw.get("tenant_id"))

    def test_list_scoped_to_tenant(self):
        ids = {m["mission_id"] for m in store.list_missions(tenant_id="tenant-A")}
        self.assertEqual(ids, {"mission-20261101-010101"})

    def test_list_single_mode_sees_everything(self):
        """未认证（单机/演示模式）保持既有行为：全部可见。"""
        ids = {m["mission_id"] for m in store.list_missions(tenant_id=None)}
        self.assertEqual(ids, {"mission-20261101-010101", "mission-20261101-010202",
                               "mission-20261101-010303"})

    def test_legacy_not_shown_to_tenant(self):
        """无归属的 legacy 存档不能冒充成任何租户的数据。"""
        ids = {m["mission_id"] for m in store.list_missions(tenant_id="tenant-A")}
        self.assertNotIn("mission-20261101-010303", ids)

    def test_cross_tenant_get_is_none(self):
        """跨租户读取与不存在返回同一个 None——404 不泄露存在性。"""
        self.assertIsNone(store.get_mission("mission-20261101-010101",
                                            tenant_id="tenant-B"))
        self.assertIsNone(store.get_mission("mission-does-not-exist-000000",
                                            tenant_id="tenant-B"))

    def test_same_tenant_get_ok(self):
        report = store.get_mission("mission-20261101-010101", tenant_id="tenant-A")
        self.assertIsNotNone(report)
        self.assertEqual(report["goal"], "A 的任务")

    def test_legacy_invisible_to_tenant_but_visible_single_mode(self):
        self.assertIsNone(store.get_mission("mission-20261101-010303",
                                            tenant_id="tenant-A"))
        self.assertIsNotNone(store.get_mission("mission-20261101-010303",
                                               tenant_id=None))


class TestTenantFromRequest(unittest.TestCase):
    """_tenant_of 的核心逻辑：有效令牌 → 租户；无令牌/坏令牌 → None（匿名降级）。"""

    def test_valid_token_yields_tenant(self):
        # 用独立 tmpdir 的 auth 密钥签发（不依赖生产 jwt_secret）
        user = auth.register("t1@example.com", "password123", "公司一")
        self.assertTrue(user.get("ok"), user)
        token = auth.issue_access_token(user["user_id"], user["tenant_id"])
        info = auth.current_user(f"Bearer {token}")
        self.assertIsNotNone(info)
        self.assertEqual(info["tenant_id"], user["tenant_id"])

    def test_bad_or_missing_token_yields_none(self):
        self.assertIsNone(auth.current_user(None))
        self.assertIsNone(auth.current_user("Bearer not-a-jwt"))
        self.assertIsNone(auth.current_user("Basic abc"))


if __name__ == "__main__":
    unittest.main()
