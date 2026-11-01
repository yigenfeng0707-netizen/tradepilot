# -*- coding: utf-8 -*-
"""LLM token 用量计量的回归测试（M4 计费地基）。

守的红线：
1. **失败也是计费事件**：调用发出但 usage 字段缺失 → tokens 记 0、calls +1
   ——成本事件不因响应缺字段而消失；
2. **归属不静默失真**：ContextVar 有租户记租户，没有记 anonymous——
   anonymous 是显式口径，不是"忘了归属"；
3. **记账失败不拖垮业务**：usage 读写炸了，chat 的返回不受影响；
4. **负数与坏类型钳 0**：网关给什么都要能记，但负 token 不入库。
"""
import asyncio
import dataclasses
import json
import tempfile
import unittest
from unittest import mock

from app.config import CONFIG  # noqa: E402
from app import llm, usage  # noqa: E402
from app.request_context import current_tenant_id  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-usage-test-")
usage.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
usage.init_db()
# llm 模块持有自己的 CONFIG 引用；把 data_dir 换到独立目录避免写真实库
llm.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="",
                                 llm_api_key="test-key", llm_disabled=False)


def _mock_response(body: dict) -> mock.AsyncMock:
    resp = mock.Mock()
    resp.text = json.dumps(body)
    resp.raise_for_status = mock.Mock()
    return resp


def _chat_body(content: str = "ok", usage_field: dict | None = {
        "prompt_tokens": 100, "completion_tokens": 50}) -> dict:
    body = {"choices": [{"message": {"content": content}}]}
    if usage_field is not None:
        body["usage"] = usage_field
    return body


class TestExtractUsage(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(usage.extract_usage(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}), (10, 5))

    def test_missing_field_is_zero(self):
        self.assertEqual(usage.extract_usage({"choices": []}), (0, 0))
        self.assertEqual(usage.extract_usage(
            {"usage": {"prompt_tokens": "abc"}}), (0, 0))

    def test_negative_clamped(self):
        self.assertEqual(usage.extract_usage(
            {"usage": {"prompt_tokens": -5, "completion_tokens": 3}}), (0, 3))


class TestRecord(unittest.TestCase):
    def setUp(self):
        with usage._db() as conn:
            conn.execute("DELETE FROM llm_usage")

    def test_accumulates_and_anonymous_key(self):
        usage.record("tenant-A", 100, 50)
        usage.record("tenant-A", 30, 20)
        usage.record(None, 10, 5)               # 匿名显式归 anonymous
        s = usage.stats()
        self.assertEqual(s["today"]["calls"], 3)
        self.assertEqual(s["today"]["prompt_tokens"], 140)
        self.assertEqual(s["today"]["completion_tokens"], 75)
        # 明细里 anonymous 是独立账目，不混进租户
        conn = usage._connect()
        rows = {r["tenant_id"]: r["calls"] for r in
                conn.execute("SELECT tenant_id, calls FROM llm_usage")}
        conn.close()
        self.assertEqual(rows["tenant-A"], 2)
        self.assertEqual(rows["anonymous"], 1)

    def test_negative_record_clamped(self):
        usage.record("tenant-A", -100, -50)
        s = usage.stats()
        self.assertEqual(s["today"]["prompt_tokens"], 0)
        self.assertEqual(s["today"]["calls"], 1)    # 调用计数不吞


class TestChatAccounting(unittest.TestCase):
    def setUp(self):
        with usage._db() as conn:
            conn.execute("DELETE FROM llm_usage")

    def _patch_client(self, body: dict) -> mock.Mock:
        client = mock.AsyncMock()
        client.post = mock.AsyncMock(return_value=_mock_response(body))
        cm = mock.AsyncMock()
        cm.__aenter__ = mock.AsyncMock(return_value=client)
        cm.__aexit__ = mock.AsyncMock(return_value=False)
        return mock.patch.object(llm.httpx, "AsyncClient", mock.Mock(return_value=cm))

    def test_chat_records_usage_with_tenant(self):
        """ContextVar 有租户 → usage 记到该租户；agent 签名一行没改。"""
        async def _main():
            tok = current_tenant_id.set("tenant-B")
            try:
                return await llm.chat("sys", "user")
            finally:
                current_tenant_id.reset(tok)
        with self._patch_client(_chat_body()):
            out = asyncio.run(_main())
        self.assertEqual(out, "ok")
        conn = usage._connect()
        rows = conn.execute("SELECT tenant_id, prompt_tokens, completion_tokens"
                            " FROM llm_usage").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_id"], "tenant-B")
        self.assertEqual(rows[0]["prompt_tokens"], 100)

    def test_chat_without_context_goes_anonymous(self):
        with self._patch_client(_chat_body()):
            asyncio.run(llm.chat("sys", "user"))
        conn = usage._connect()
        rows = conn.execute("SELECT tenant_id FROM llm_usage").fetchall()
        conn.close()
        self.assertEqual(rows[0]["tenant_id"], "anonymous")

    def test_chat_without_usage_field_still_counts_call(self):
        """网关响应缺 usage：tokens 0 但 calls +1——成本事件不消失。"""
        with self._patch_client(_chat_body(usage_field=None)):
            asyncio.run(llm.chat("sys", "user"))
        s = usage.stats()
        self.assertEqual(s["today"]["calls"], 1)
        self.assertEqual(s["today"]["prompt_tokens"], 0)

    def test_accounting_failure_does_not_break_chat(self):
        """记账炸了不影响业务返回——计量层永远不能拖垮产品。"""
        broken = _chat_body()
        with self._patch_client(broken), \
             mock.patch.object(usage, "record", side_effect=RuntimeError("db gone")):
            out = asyncio.run(llm.chat("sys", "user"))
        self.assertEqual(out, "ok")

    def test_unavailable_records_nothing(self):
        """没配 key 时连请求都不发，更不该有账。"""
        with mock.patch.object(llm, "CONFIG",
                               dataclasses.replace(llm.CONFIG, llm_api_key="")):
            out = asyncio.run(llm.chat("sys", "user"))
        self.assertIsNone(out)
        self.assertEqual(usage.stats()["today"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
