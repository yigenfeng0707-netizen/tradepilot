#!/usr/bin/env python3
# Copyright 2026 TradePilot project. Apache-2.0.
"""对线上实例（默认 Cloud Run）跑全端点功能核查。

用法：
    python scripts/probe_cloudrun.py [BASE_URL] [--agent-slow]

--agent-slow 加上才会跑 content-generation / pipeline 等耗时接口（1-3 分钟级）。
输出每项：PASS/FAIL/WARN + 关键指标；结尾汇总。
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

DEFAULT_BASE = "https://tradepilot-590160899175.asia-east1.run.app"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
       "Accept": "application/json", "Accept-Language": "zh-CN,zh;q=0.9"}

results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str, warn: bool = False) -> None:
    tag = "PASS" if ok else ("WARN" if warn else "FAIL")
    results.append((tag, name, detail))
    print(f"[{tag:<4}] {name}｜{detail}")


def call(client: httpx.Client, name: str, method: str, path: str,
         body: dict | None = None, timeout: float = 60.0,
         expect: int = 200, slow_ok: bool = False) -> httpx.Response | None:
    t0 = time.time()
    try:
        r = client.request(method, path, json=body, headers=HDR, timeout=timeout)
    except httpx.HTTPError as e:
        record(name, False, f"{type(e).__name__}: {e}"[:200])
        return None
    dt = time.time() - t0
    ok = r.status_code == expect
    # 5xx 认定为失败；慢接口只记录耗时
    record(name, ok, f"HTTP {r.status_code} ({dt:.1f}s)" + ("" if ok else " " + r.text[:120]))
    return r if ok else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=DEFAULT_BASE)
    ap.add_argument("--agent-slow", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    with httpx.Client(base_url=base, follow_redirects=True) as c:
        print(f"=== 目标：{base} ===\n一、基础设施")
        r = call(c, "health", "GET", "/api/health")
        if r:
            d = r.json()
            record("health.llm", d.get("llm") is True, f"llm={d.get('llm')} model={d.get('llm_model')}")
        call(c, "metrics", "GET", "/api/metrics")
        r = call(c, "tools", "GET", "/api/tools")
        if r:
            tools = r.json().get("tools", [])
            record("tools.count", len(tools) >= 8, f"工具数={len(tools)}")
        r = call(c, "capabilities", "GET", "/api/capabilities")
        if r:
            d = r.json()
            record("capabilities.fields",
                   all(k in d for k in ("agents", "languages", "platforms", "categories")),
                   f"agents={d.get('agents')} languages={d.get('languages')} "
                   f"platforms={d.get('platforms')}")
        r = call(c, "agents.列表", "GET", "/api/agents")
        if r:
            agents = r.json().get("agents", [])
            record("agents.count", len(agents) >= 6, f"agent 数={len(agents)}")
        r = call(c, "missions", "GET", "/api/missions")
        if r:
            ms = r.json().get("missions", [])
            record("missions", isinstance(ms, list), f"任务数={len(ms)}（新实例为空属正常）")
        r404 = c.get("/api/opc/brief", headers=HDR, timeout=30)
        # 有归档任务时返回简报（200），无任务时 404（设计行为）——两者皆正常
        record("opc/brief", r404.status_code in (200, 404),
               f"HTTP {r404.status_code}"
               + ("（有归档任务，返回简报）" if r404.status_code == 200
                  else f"（无任务，detail={r404.json().get('detail', '')[:30]}）"))

        print("\n二、前端页面")
        for path, needle in (("/", "html"), ("/app", "html"), ("/canvas", "canvas")):
            r = call(c, f"page {path}", "GET", path)
            if r:
                body = r.text
                ok = "<html" in body.lower() and "error" not in body[:100].lower()
                record(f"page {path} 内容", ok, f"长度={len(body)}B 含html={'<html' in body.lower()}")
        r = call(c, "static app.js", "GET", "/static/app.js")
        if r:
            record("static app.js", len(r.content) > 10000, f"大小={len(r.content)}B")

        print("\n三、业务 Agent")
        r = call(c, "market-insight", "POST", "/api/agents/market-insight",
                 {"category": "pet", "keyword": "dog bed", "marketplace": "US"}, timeout=180)
        if r:
            d = r.json()
            ok = d.get("success") is True
            gen = str(d.get("generated_by"))
            record("market-insight 内容", ok, f"success={ok} generated_by={gen}")
            if ok and gen != "llm":
                record("market-insight LLM", False, "摘要未走 LLM", warn=True)

        r = call(c, "smart-selection", "POST", "/api/agents/smart-selection",
                 {"category": "pet", "keyword": "dog bed", "limit": 6}, timeout=180)
        if r:
            d = r.json()
            data = d.get("data") or {}
            items = data.get("product_recommendations") or data.get("items") or []
            record("smart-selection 选品", d.get("success") is True and len(items) > 0,
                   f"success={d.get('success')} 商品数={len(items)}"
                   + ("" if d.get("success") else "｜" + str(d.get("message"))[:80]))

        r = call(c, "content-options", "GET", "/api/agents/content-options")
        if r:
            d = r.json()
            record("content-options", len(d.get("languages", [])) > 0 and len(d.get("platforms", [])) > 0,
                   f"语言={len(d.get('languages', []))} 平台={len(d.get('platforms', []))}")
        call(c, "compliance-options", "GET", "/api/agents/compliance-options")

        r = call(c, "compliance", "POST", "/api/agents/compliance",
                 {"category": "pet", "target_country": "美国"}, timeout=180)
        if r:
            d = r.json()
            record("compliance", d.get("success") is True, f"success={d.get('success')}")

        r = call(c, "customer-chat", "POST", "/api/agents/customer-chat",
                 {"message": "这个狗床能机洗吗"}, timeout=120)
        if r:
            d = r.json()
            inner = d.get("data") or {}
            record("customer-chat", d.get("success") is True,
                   f"generated_by={inner.get('generated_by')} reply={str(inner.get('reply'))[:40]}")

        if args.agent_slow:
            print("\n三 b、慢接口（1-3 分钟）")
            r = call(c, "content-generation", "POST", "/api/agents/content-generation",
                     {"product_name": "可拆洗狗床", "category": "pet", "platform": "shopee",
                      "target_language": "en", "keyword": "dog bed"}, timeout=300)
            if r:
                d = r.json()
                gen = str(d.get("generated_by"))
                record("content-generation", d.get("success") is True,
                       f"success={d.get('success')} generated_by={gen}")
                if d.get("success") and "template" in gen:
                    record("content-generation LLM", False, "模板兜底未走 LLM", warn=True)

            r = call(c, "pipeline", "POST", "/api/agents/pipeline",
                     {"keyword": "dog bed", "category": "pet", "marketplace": "US"}, timeout=600)
            if r:
                d = r.json()
                record("pipeline", d.get("success") is True,
                       f"success={d.get('success')} steps={len(d.get('steps', []))}")

        print("\n四、鉴权与计量")
        stamp = str(int(time.time()))
        email = f"probe_{stamp}@example.com"
        r = call(c, "register", "POST", "/api/auth/register",
                 {"email": email, "password": "Probe12345!", "company": "probe"})
        if r:
            reg = r.json()
            token = reg.get("access_token") or (reg.get("data") or {}).get("access_token")
            refresh = reg.get("refresh_token") or (reg.get("data") or {}).get("refresh_token")
            auth_ok = bool(token)
            record("register 发 token", auth_ok, f"token={'有' if token else '无'}")
            if token:
                auth = {"X-Auth-Token": token, **HDR}
                r2 = c.get("/api/auth/me", headers=auth, timeout=30)
                record("auth/me", r2.status_code == 200, f"HTTP {r2.status_code}")
                r3 = c.get("/api/me/usage", headers=auth, timeout=30)
                record("me/usage", r3.status_code == 200, f"HTTP {r3.status_code}")
                if r3.status_code == 200:
                    usage = r3.json()
                    record("me/usage 内容", "quota" in usage and "llm_usage" in usage,
                           f"keys={list(usage.keys())}")
                r4 = c.get("/api/admin/overview", headers=auth, timeout=30)
                record("admin/overview", r4.status_code in (200, 403),
                       f"HTTP {r4.status_code}" + ("（非管理员拒绝，符合预期）" if r4.status_code == 403 else ""))
                if r4.status_code == 200:
                    call(c, "admin/trends", "GET", "/api/admin/trends", timeout=30)
                if refresh:
                    r5 = c.post("/api/auth/logout", headers=auth,
                                json={"refresh_token": refresh}, timeout=30)
                    record("logout", r5.status_code == 200, f"HTTP {r5.status_code}")
                    r6 = c.get("/api/auth/me", headers=auth, timeout=30)
                    # access token 仍有效属于设计（JWT 无状态），吊销的是 refresh
                    record("logout 后 access token 状态", r6.status_code in (200, 401),
                           f"HTTP {r6.status_code}（JWT 无状态，吊销的是 refresh_token）")
                else:
                    record("logout", False, "register 未返回 refresh_token")
        call(c, "login 错误密码", "POST", "/api/auth/login",
             {"email": email, "password": "wrong"}, expect=401, timeout=30)

        print("\n五、集成与存证")
        # 前面的 r 变量已被复用，这里直接用带鉴权的请求
        ra = c.get("/api/integrations/shopify/auth-url",
                   headers={"X-Auth-Token": token, **HDR}, timeout=30)
        # 200=有授权链接；409=该平台设计上不走授权链接或凭证未配置（detail 有清晰指引）
        record("integrations auth-url", ra.status_code in (200, 409),
               f"HTTP {ra.status_code}｜{ra.json().get('detail', '返回授权链接')[:60]}")
        rs = c.get("/api/integrations/shopify/shops",
                   headers={"X-Auth-Token": token, **HDR}, timeout=30)
        record("integrations shops", rs.status_code == 200,
               f"HTTP {rs.status_code} shops={len(rs.json().get('shops', []))}")
        r = c.post("/api/integrations/shopify/connect", json={},
                   headers={"X-Auth-Token": token, **HDR}, timeout=30)
        record("shopify connect 空体校验", r.status_code == 422,
               f"HTTP {r.status_code} 字段校验拦截（符合预期）")

        r = call(c, "provenance 列表", "GET", "/api/provenance")
        if r:
            d = r.json()
            total = d.get("total", len(d)) if isinstance(d, dict) else len(d)
            record("provenance 列表", total is not None, f"total={total}")
            items = d.get("items") if isinstance(d, dict) else d
            if items:
                pid = items[0].get("provenance_id") or items[0].get("id")
                r2 = call(c, "provenance 单条", "GET", f"/api/provenance/{pid}")
                if r2:
                    d2 = r2.json()
                    record("provenance 单条内容", bool(d2.get("url") or d2.get("snapshot")),
                           f"pid={pid} 有url/快照")

    print("\n=== 汇总 ===")
    n_pass = sum(1 for t, *_ in results if t == "PASS")
    n_warn = sum(1 for t, *_ in results if t == "WARN")
    n_fail = sum(1 for t, *_ in results if t == "FAIL")
    for t, name, detail in results:
        if t != "PASS":
            print(f"  [{t}] {name}｜{detail}")
    print(f"共 {len(results)} 项：PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
