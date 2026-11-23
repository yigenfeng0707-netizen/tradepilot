# -*- coding: utf-8 -*-
"""Wave 2：robots.txt 合规闸门 + HTTP 层冒烟（TestClient 首次引入）。

为什么必须有：
- **robots 闸门测试**：作品公开声明"尊重 robots"（README 三条红线之一），
  此前只有代码实现（fetch.robots_allowed）、没有任何回归测试——声明必须
  可执行、可复验，否则就是话术。Walmart robots 禁止通用 UA 抓 /search 的
  拦截行为必须有机器证据。
- **HTTP 层冒烟**：全仓没有一个 TestClient——"集成端点"测试用
  SimpleNamespace 伪 Request 直接调 handler，路由注册、Pydantic 422、
  中间件链从未被执行。路由拼错只有运行时才知道。本测试建立 TestClient
  基建，覆盖匿名 401 边界与认证流的 HTTP 层语义。
"""
import unittest
from unittest import mock

import httpx

from app.tools.fetch import _robots_cache, robots_allowed


def _robots_handler(body: str | None, status: int = 200):
    """构造 robots.txt 的 MockTransport 响应；body=None 表示不可达。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/robots.txt":
            return httpx.Response(404, text="not robots")
        if body is None:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(status, text=body)
    return handler


class TestRobotsGate(unittest.IsolatedAsyncioTestCase):
    """robots.txt 闸门：声明"尊重"就必须有测试拦着。"""

    def setUp(self):
        # 套件全局设 TRADEPILOT_RESPECT_ROBOTS=0 是给评测复现环境的；
        # 本测试验闸门本身，显式打开（与生产默认一致）。
        from app.config import CONFIG
        self._cfg = mock.patch.object(CONFIG, "respect_robots", True)
        self._cfg.start()
        _robots_cache.clear()

    def tearDown(self):
        self._cfg.stop()
        _robots_cache.clear()

    async def _allowed_with_handler(self, handler, url):
        """注入 MockTransport：robots_allowed 内部自建 client，
        通过 patch 构造器替换——side_effect 里必须用 patch 前捕获的
        原始 AsyncClient（直接调用 httpx.AsyncClient 会回到 mock 自身）。"""
        orig_ctor = httpx.AsyncClient
        with mock.patch("app.tools.fetch.httpx.AsyncClient",
                        side_effect=lambda **kw: orig_ctor(
                            transport=httpx.MockTransport(handler))):
            return await robots_allowed(url)

    async def test_disallow_is_blocked(self):
        robots = """User-agent: *
Disallow: /search
Allow: /
"""
        ok, why = await self._allowed_with_handler(
            _robots_handler(robots), "https://example.com/search?q=dog")
        self.assertFalse(ok)
        self.assertIn("robots.txt 禁止", why)

    async def test_allowed_path_passes(self):
        robots = "User-agent: *\nAllow: /\n"
        ok, why = await self._allowed_with_handler(
            _robots_handler(robots), "https://example.com/product/123")
        self.assertTrue(ok)

    async def test_missing_robots_allows_by_convention(self):
        """404/不可达按 RFC 9309 惯例放行（不是乱开绿灯：惯例允许）。"""
        ok, why = await self._allowed_with_handler(
            _robots_handler(None), "https://example.com/anything")
        self.assertTrue(ok)
        self.assertIn("RFC 9309", why)

    async def test_respect_robots_off_allows_everything(self):
        """关闭开关时直接放行（评测复现环境用，开关本身就是显式声明）。"""
        from app.config import CONFIG
        with mock.patch.object(CONFIG, "respect_robots", False):
            ok, why = await robots_allowed("https://example.com/search")
        self.assertTrue(ok)


class TestHttpSmoke(unittest.TestCase):
    """HTTP 层冒烟：第一次用 TestClient 跑通真实路由链。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        # 必须进上下文：lifespan 里 init_db，裸 TestClient 第一次注册就
        # no such table（与干净容器同一个坑）
        cls._cm = TestClient(app)
        cls._cm.__enter__()
        cls.client = cls._cm

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)

    def test_health_is_public(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_tools_and_capabilities_public(self):
        for path in ("/api/tools", "/api/capabilities"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)

    def test_protected_endpoints_reject_anonymous(self):
        """带租户身份语义的端点匿名 401/403，不能裸奔。
        注意 /api/missions 是登录后收紧而非强制认证：匿名 = 单机/演示模式
        （可见全部），这是有意设计——此处只断言强制认证的端点。"""
        r = self.client.get("/api/me/usage")
        self.assertIn(r.status_code, (401, 403))
        r = self.client.get("/api/auth/me")
        self.assertIn(r.status_code, (401, 403))

    def test_admin_requires_token(self):
        r = self.client.get("/api/admin/overview")
        self.assertIn(r.status_code, (401, 403))

    def test_mission_detail_nonexistent_404(self):
        # 未认证模式下 missions 可见但非法 id 应 404 而非 500
        r = self.client.get("/api/missions/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_register_login_refresh_full_http_flow(self):
        """注册→登录→刷新→旧刷新重放被拒（此前只在模块层测过轮换）。"""
        import secrets
        user = f"smoke{secrets.token_hex(4)}"
        email = f"{user}@test.com"
        payload = {"username": user, "email": email, "password": "Test12345!"}

        r = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("access_token", r.json())

        r = self.client.post("/api/auth/login",
                             json={"email": email, "password": "Test12345!"})
        self.assertEqual(r.status_code, 200, r.text)
        tokens = r.json()
        self.assertIn("refresh_token", tokens)

        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], email)

        # 刷新轮换：新令牌生效
        r = self.client.post("/api/auth/refresh",
                             json={"refresh_token": tokens["refresh_token"]})
        self.assertEqual(r.status_code, 200, r.text)
        new_refresh = r.json().get("refresh_token")

        # 旧刷新重放必须被拒（轮换的核心安全语义）
        if new_refresh:
            r = self.client.post("/api/auth/refresh",
                                 json={"refresh_token": tokens["refresh_token"]})
            self.assertIn(r.status_code, (401, 403), "旧刷新令牌重放必须被拒")

    def test_login_wrong_password_unified_response(self):
        """登录失败同响应防用户枚举（此前只有模块层测试）。"""
        r = self.client.post("/api/auth/login",
                             json={"email": "nobody@test.com",
                                   "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_sse_pipeline_stream_consumes_to_done(self):
        """SSE 流式端点能跑到结束（此前中间件+流式生成器从未被执行）。"""
        import secrets
        user = f"sse{secrets.token_hex(4)}"
        email = f"{user}@test.com"
        self.client.post("/api/auth/register",
                         json={"username": user, "email": email,
                               "password": "Test12345!"})
        tokens = self.client.post("/api/auth/login",
                                  json={"email": email,
                                        "password": "Test12345!"}).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        # mock 四个 agent，避免真实抓取（SSE 链路本身才是被测对象）
        from app.agents import (compliance as cp, content_generation as cg,
                                market_insight as mi, smart_selection as ss)

        def _ok(agent):
            return {"success": True, "agent": agent, "data": {"hi": 1},
                    "data_quality": "sample", "message": f"{agent}完成"}

        with mock.patch.object(mi, "run", mock.AsyncMock(return_value=_ok("mi"))), \
             mock.patch.object(ss, "run", mock.AsyncMock(
                 return_value=_ok("ss"))), \
             mock.patch.object(cg, "run", mock.AsyncMock(return_value=_ok("cg"))), \
             mock.patch.object(cp, "run", mock.AsyncMock(return_value=_ok("cp"))):
            with self.client.stream(
                    "POST", "/api/agents/pipeline/stream",
                    json={"category": "pet", "keyword": "dog bed",
                          "product_name": "dog bed"},
                    headers=headers) as resp:
                self.assertEqual(resp.status_code, 200)
                body = b"".join(resp.iter_bytes())
        text = body.decode("utf-8")
        self.assertIn("event", text)
        self.assertIn("DONE", text)
        self.assertIn("mission_id", text, "SSE 完成事件应带归档回执")


if __name__ == "__main__":
    unittest.main()
