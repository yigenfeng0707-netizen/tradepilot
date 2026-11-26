# -*- coding: utf-8 -*-
"""支付链路回归测试（alipay.trade.page.pay 接入）。

守的红线：
1. **验签零信任**：伪造/篡改签名一律 fail，没有"宽容解析"；
2. **幂等**：同一通知重发、已付订单再 mark_paid，状态与配额不重复变；
3. **跨租户隔离**：订单只能被所属租户查到/同步；
4. **金额一致**：total_amount 与订单金额不符一律 fail（防低价伪造回调）；
5. **自测单如实标记**：test=1 不冒充真实订阅。
"""
import dataclasses
import os
import tempfile
import unittest

from app.config import CONFIG  # noqa: E402
from app import billing, payment, quota  # noqa: E402

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-billing-test-")
billing.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
quota.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")


def _gen_keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PRIV, PUB = _gen_keys()


def _set_env():
    os.environ["ALIPAY_APP_ID"] = "2021000000000001"
    os.environ["ALIPAY_PRIVATE_KEY"] = PRIV
    os.environ["ALIPAY_ALIPAY_PUBLIC_KEY"] = PUB
    os.environ["ALIPAY_PID"] = "2088000000000001"
    os.environ.pop("ALIPAY_GATEWAY", None)
    os.environ.pop("ALIPAY_NOTIFY_URL", None)


def setUpModule():
    _set_env()
    quota.init_db()
    billing.init_db()


class TestSignVerify(unittest.TestCase):
    """RSA2 签名/验签：自己的签自己能验，被改一字就验不过。"""

    def test_roundtrip(self):
        params = {"app_id": "2021000000000001", "method": "x.y",
                  "charset": "utf-8", "sign_type": "RSA2",
                  "biz_content": '{"a":1}', "timestamp": "2026-11-26 12:00:00"}
        sign = payment._sign_content(params)
        self.assertTrue(payment._verify_content(params, sign))

    def test_tampered_value_fails(self):
        params = {"k1": "v1", "k2": "v2"}
        sign = payment._sign_content(params)
        self.assertFalse(payment._verify_content({**params, "k2": "evil"}, sign))

    def test_missing_sign_fails(self):
        self.assertFalse(payment.verify_notify({"out_trade_no": "1"}))


class TestPagePayUrl(unittest.TestCase):
    def test_url_contains_signed_params(self):
        url = payment.page_pay_url("TP1", "测试商品", 0.01)
        self.assertIn("https://openapi.alipay.com/gateway.do?", url)
        self.assertIn("alipay.trade.page.pay", url)
        # total_amount/subject 在 biz_content 内（page.pay 标准结构）
        self.assertIn("total_amount", url)
        self.assertIn("FAST_INSTANT_TRADE_PAY", url)
        self.assertIn("sign=", url)

    def test_amount_two_decimals(self):
        self.assertEqual(payment.format_amount(199.0), "199.00")
        self.assertEqual(payment.format_amount(0.01), "0.01")


class TestOrders(unittest.TestCase):
    def setUp(self):
        with billing._db() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM notify_seen")
        with quota._db() as conn:
            conn.execute("DELETE FROM tenant_limits")

    def test_create_and_mark_paid_upgrades_quota(self):
        order = billing.create_order("tenant-A", "pro")
        self.assertEqual(order["amount"], 199.00)
        self.assertTrue(order["pay_url"].startswith("https://openapi.alipay.com"))
        self.assertTrue(billing.mark_paid(order["out_trade_no"], "T1", via="notify"))
        # 已付再 mark：幂等，返回 False 不重复履约
        self.assertFalse(billing.mark_paid(order["out_trade_no"], "T1", via="notify"))
        self.assertEqual(quota.tenant_daily_limit("tenant-A"), 2000)

    def test_self_test_plan_marks_flag_no_quota_change(self):
        order = billing.create_order("tenant-A", "self_test")
        self.assertTrue(order["test"])
        billing.mark_paid(order["out_trade_no"], "T9", via="notify")
        self.assertEqual(quota.tenant_daily_limit("tenant-A"),
                         quota.default_daily_limit())
        got = billing.get_order("tenant-A", order["out_trade_no"])
        self.assertTrue(got["test"])
        self.assertEqual(got["status"], "PAID")

    def test_unknown_plan_rejected(self):
        with self.assertRaises(ValueError):
            billing.create_order("tenant-A", "pro_plus_extra")

    def test_cross_tenant_isolation(self):
        o1 = billing.create_order("tenant-A", "pro")
        self.assertIsNone(billing.get_order("tenant-B", o1["out_trade_no"]))
        self.assertEqual(billing.list_orders("tenant-B"), [])

    def test_unknown_order_mark_paid_ignored(self):
        self.assertFalse(billing.mark_paid("TP-NOPE", "T0", via="notify"))


class TestHandleNotify(unittest.TestCase):
    """异步通知：验签 + 业务校验 + 幂等。"""

    def setUp(self):
        with billing._db() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM notify_seen")
        with quota._db() as conn:
            conn.execute("DELETE FROM tenant_limits")
        self.order = billing.create_order("tenant-A", "pro")

    def _form(self, **over):
        base = {
            "app_id": "2021000000000001",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "trade_status": "TRADE_SUCCESS",
            "out_trade_no": self.order["out_trade_no"],
            "trade_no": "2026112622001000000001",
            "total_amount": payment.format_amount(self.order["amount"]),
            "seller_id": "2088000000000001",
            "notify_id": "NID-" + str(_counter()),
            "notify_time": "2026-11-26 12:00:00",
            "notify_type": "trade_status_sync",
        }
        base.update(over)
        base["sign"] = payment._sign_content(base)
        return base

    def test_valid_notify_pays_and_upgrades(self):
        self.assertEqual(billing.handle_notify(self._form()), "success")
        self.assertEqual(billing.get_order("tenant-A", self.order["out_trade_no"])["status"], "PAID")
        self.assertEqual(quota.tenant_daily_limit("tenant-A"), 2000)

    def test_replayed_notify_idempotent_success(self):
        form = self._form()
        self.assertEqual(billing.handle_notify(form), "success")
        # 同一 notify_id 重发：不再处理，但仍回 success
        self.assertEqual(billing.handle_notify(form), "success")
        self.assertEqual(quota.tenant_daily_limit("tenant-A"), 2000)

    def test_tampered_sign_fail(self):
        form = self._form()
        form["sign"] = form["sign"][:-4] + "AAAA"
        self.assertEqual(billing.handle_notify(form), "fail")
        self.assertEqual(billing.get_order("tenant-A", self.order["out_trade_no"])["status"], "PENDING")

    def test_wrong_app_id_fail(self):
        form = self._form(app_id="2021999999999999")
        self.assertEqual(billing.handle_notify(form), "fail")

    def test_amount_mismatch_fail(self):
        form = self._form(total_amount="0.01")
        self.assertEqual(billing.handle_notify(form), "fail")

    def test_wrong_seller_fail(self):
        form = self._form(seller_id="2088999999999999")
        self.assertEqual(billing.handle_notify(form), "fail")

    def test_non_paid_status_no_fulfillment(self):
        form = self._form(trade_status="WAIT_BUYER_PAY")
        self.assertEqual(billing.handle_notify(form), "success")
        self.assertEqual(billing.get_order("tenant-A", self.order["out_trade_no"])["status"], "PENDING")

    def test_unknown_order_fail(self):
        form = self._form(out_trade_no="TP-NOPE")
        self.assertEqual(billing.handle_notify(form), "fail")


class TestConfigGate(unittest.TestCase):
    def test_unconfigured_blocks_order(self):
        saved = os.environ.pop("ALIPAY_PRIVATE_KEY")
        try:
            with self.assertRaises(RuntimeError):
                billing.create_order("tenant-A", "pro")
            self.assertFalse(payment.configured())
        finally:
            os.environ["ALIPAY_PRIVATE_KEY"] = saved


_counter_seq = [0]


def _counter() -> int:
    _counter_seq[0] += 1
    return _counter_seq[0]


if __name__ == "__main__":
    unittest.main()
