# Copyright 2026 TradePilot project. Apache-2.0.
"""支付宝电脑网站支付（alipay.trade.page.pay）：零第三方支付 SDK 接入。

设计原则（与 llm.py 一脉相承）：
- **签名/验签用 cryptography 做 RSA2**（应用私钥签名、支付宝公钥验签），
  不引支付 SDK——链路只有「组装参数→签名→POST→解析」，SDK 反而把
  签名细节藏成黑盒，出问题无从排查。
- **私钥永不落代码库**：只从环境变量读（线上走平台 Secret 注入，
  与 LLM_API_KEY 同一套运维口径）。
- **验签失败一律失败**：异步通知没有"宽容解析"——伪造的通知直接 fail，
  不降级、不猜测。
- 对账兜底：notify 只是"快"，真相以 alipay.trade.query 为准——两者都走，
  任意一个判 paid 即履约，但履约幂等（见 billing.py）。

密钥格式兼容 PEM 与原始 base64 DER（PKCS#8 私钥 / SPKI 公钥）——
魔搭/云函数里整文件注入常带换行，统一在此规整。
"""
from __future__ import annotations

import base64
import os
import time
import urllib.parse
from typing import Any

import httpx

GATEWAY_DEFAULT = "https://openapi.alipay.com/gateway.do"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or default).strip()


def app_id() -> str:
    return _env("ALIPAY_APP_ID")


def configured() -> bool:
    """三项齐备才算能收钱：app、私钥、支付宝公钥。缺一即整体关闭，
    下单端点会 503 明说，不假装能支付。"""
    return bool(app_id() and _env("ALIPAY_PRIVATE_KEY") and _env("ALIPAY_ALIPAY_PUBLIC_KEY"))


def gateway() -> str:
    return _env("ALIPAY_GATEWAY", GATEWAY_DEFAULT)


def notify_url() -> str:
    """异步通知地址：公网 HTTPS，由部署环境注入。空=不带 notify_url，
    只靠主动查询（query）兜底——慢但不丢。"""
    return _env("ALIPAY_NOTIFY_URL")


def pid() -> str:
    """收款 PID（卖家身份校验用）。"""
    return _env("ALIPAY_PID")


def _load_private_key():
    from cryptography.hazmat.primitives.serialization import (
        load_der_private_key, load_pem_private_key)

    raw = _env("ALIPAY_PRIVATE_KEY")
    if "-----BEGIN" in raw:
        return load_pem_private_key(raw.encode(), password=None)
    # 原始 base64（PKCS#8 DER）：个别平台注入会去掉 PEM 头
    return load_der_private_key(base64.b64decode(raw), password=None)


def _load_public_key():
    from cryptography.hazmat.primitives.serialization import (
        load_der_public_key, load_pem_public_key)

    raw = _env("ALIPAY_ALIPAY_PUBLIC_KEY")
    if "-----BEGIN" in raw:
        return load_pem_public_key(raw.encode())
    return load_der_public_key(base64.b64decode(raw))


def _sign_content(params: dict[str, str]) -> str:
    """RSA2（SHA256withRSA + PKCS#1 v1.5）：按 key 升序拼 k=v，剔除 sign。

    **sign_type 参与签名内容**（实测：生产网关对不含 sign_type 的内容返回
    isv.invalid-signature——这是与本仓早期假设相反的一个坑，以 2026-11-26
    生产冒烟为准）。空值参数不参与签名。
    """
    items = sorted((k, v) for k, v in params.items()
                   if k != "sign" and v != "")
    content = "&".join(f"{k}={v}" for k, v in items)
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    sig = _load_private_key().sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def _verify_content(params: dict[str, str], sign: str) -> bool:
    """验签：sign_type 是否纳入签名内容在支付宝两侧口径曾不一致（请求要
    纳入、部分异步通知不含），两种内容都尝试——验签强度不降（同一把
    支付宝公钥，内容变体确定）。"""
    items = sorted((k, v) for k, v in params.items()
                   if k not in ("sign", "sign_type") and v != "")
    content_a = "&".join(f"{k}={v}" for k, v in items)
    items_b = sorted((k, v) for k, v in params.items()
                     if k != "sign" and v != "")
    content_b = "&".join(f"{k}={v}" for k, v in items_b)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    for content in (content_a, content_b):
        try:
            _load_public_key().verify(
                base64.b64decode(sign),
                content.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256())
            return True
        except Exception:  # noqa: BLE001 — 两种内容都不通过才算验签失败
            continue
    return False


def _base_params(method: str, biz_content: dict[str, Any],
                 extra: dict[str, str] | None = None) -> dict[str, str]:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    p: dict[str, str] = {
        "app_id": app_id(),
        "method": method,
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": ts,
        "version": "1.0",
        "biz_content": __import__("json").dumps(biz_content, ensure_ascii=False),
    }
    if notify_url():
        p["notify_url"] = notify_url()
    if extra:
        p.update({k: v for k, v in extra.items() if v})
    p["sign"] = _sign_content(p)
    return p


def format_amount(amount: float) -> str:
    """支付宝要两位小数字符串。浮点 199.0 必规整为 "199.00"，
    否则 total_amount 校验怎么比都假。"""
    return f"{amount:.2f}"


def page_pay_url(out_trade_no: str, subject: str, amount: float,
                 return_url: str | None = None) -> str:
    """电脑网站支付：返回签名后的跳转 URL（浏览器直接打开即收银台）。

    page.pay 的标准用法是网关返回自动提交表单；直接拼 GET 同样成立且
    前端处理更简单（window.location 跳转即可）。
    """
    biz: dict[str, Any] = {
        "out_trade_no": out_trade_no,
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "total_amount": format_amount(amount),
        "subject": subject,
    }
    extra = {"return_url": return_url} if return_url else None
    params = _base_params("alipay.trade.page.pay", biz, extra)
    return gateway() + "?" + urllib.parse.urlencode(params)


async def query_trade(out_trade_no: str, timeout_s: float = 20.0) -> dict[str, Any] | None:
    """alipay.trade.query 主动对账：返回 trade_status（WAIT_BUYER_PAY /
    TRADE_CLOSED / TRADE_SUCCESS / TRADE_FINISHED）或 None（不可达/解析失败）。

    通知是"快"，查询是"准"：演示与履约都应能接受慢半秒的主动查询兜底。
    """
    params = _base_params("alipay.trade.query",
                          {"out_trade_no": out_trade_no})
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(gateway(),
                                     data={k: v.encode("utf-8") for k, v in params.items()})
            resp.raise_for_status()
            body = __import__("json").loads(resp.text)
        node = body.get("alipay_trade_query_response", {})
        if node.get("code") == "10000":
            return {"trade_status": node.get("trade_status", ""),
                    "trade_no": node.get("trade_no", ""),
                    "total_amount": node.get("total_amount", "")}
        # 40004 订单不存在（还没创建/已超时关单）等业务码如实返回
        return {"trade_status": "", "trade_no": "",
                "sub_code": node.get("sub_code", node.get("code", ""))}
    except Exception:  # noqa: BLE001 — 对账失败不阻断用户主流程
        return None


def verify_notify(form: dict[str, str]) -> bool:
    """异步通知验签（公钥验签，不信任任何中间代理）。"""
    sign = form.get("sign", "")
    if not sign:
        return False
    return _verify_content(form, sign)


PAID_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")
