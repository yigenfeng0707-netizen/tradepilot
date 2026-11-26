# Copyright 2026 TradePilot project. Apache-2.0.
"""TradePilot 服务入口：FastAPI + WebSocket 遥测 + 静态大屏 + 任务存档/导出。"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, StreamingResponse)
from pydantic import BaseModel

from . import (auth, billing, llm, monitoring, payment, provenance, quota,
               shops, store, usage)
from .datasources import lazada, shopee, shopify
from .datasources import marketplace_auth as mauth
from .request_context import current_tenant_id
from .agents import (compliance, content_generation, customer_service,
                     market_insight, pipeline, smart_selection)
from .config import CONFIG
from .orchestrator import build_registry, run_mission
from .tools.fetch import shutdown_browser

WEB = Path(__file__).resolve().parents[1] / "web"
STATIC_WHITELIST = {"app.js", "style.css"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 建表幂等（IF NOT EXISTS）。auth 曾漏在这——开发机 data_dir 里总有
    # 旧库掩盖了缺口，干净容器（挂空卷）一注册就 no such table。
    # 经验：新加持久化模块，初始化必须在 lifespan 登记，靠容器冒烟兜底。
    auth.init_db()
    quota.init_db()
    usage.init_db()
    shops.init_db()
    billing.init_db()
    yield
    shutdown_browser()  # Cloud Run 优雅停机：释放复用中的 Chromium


app = FastAPI(title="TradePilot", version="0.2.0", lifespan=lifespan)
_registry = build_registry()


class MissionIn(BaseModel):
    goal: str
    keyword: str                     # 品类关键词（市场扫描用）
    keyword_cn: str = ""             # 中文源检索词（留空按内置映射，如 dog bed → 狗床）
    source: str = "dangdang"         # 数据源：dangdang（CNY）/ walmart（USD，受 robots 约束）
    category: str                    # 合规知识卡品类：pet/home/electronics
    marketplace: str = "US"          # 目标站点：US/UK/DE/JP（影响文案字符上限）
    unit_cost_cny: float | None = None   # 采购价（缺省按零售参考价 × cost_ratio 估算）
    cost_ratio: float = 0.35             # 成本率假设（零售价 → 采购成本的折算系数）
    freight_usd: float = 2.5
    duty_pct: float = 7.0
    last_mile_usd: float = 5.5
    commission_pct: float = 15.0
    target_margin_pct: float = 20.0
    search_terms: str = ""           # 后台 Search Terms（可选，审计会检查）
    brand_blocklist: list[str] = []  # 竞品品牌词（侵权审计用）
    keywords: str = ""               # 选品雷达候选词（逗号分隔）。留空=用内置假设词，
                                     # 内置词是待验证假设、不是热销榜，来源标记会随产物输出
    max_keywords: int = 5            # 雷达最多扫几个词：每个词都是一次真实抓取，直接决定耗时


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "tradepilot",
            "provenance": provenance.stats(),
            "data_dir": CONFIG.data_dir,
            "respect_robots": CONFIG.respect_robots,
            "llm": llm.available(),
            "llm_model": CONFIG.llm_model if llm.available() else None,
            # 支付在线与否同样要看得见：收银台坏掉不能靠用户下单失败才发现
            "billing": payment.configured()}


# 请求级权重（M4 配额口径）：一次全链路内部会调多次 LLM，但商业单位是
# "一次任务"——权重表让用户看到的余额与账单口径一致。新端点上线必须
# 在这里表态（不进表 = 不扣 = 运营看不见的免费午餐）。
_QUOTA_WEIGHTS: dict[str, float] = {
    "/api/agents/pipeline": 4.0,
    "/api/agents/pipeline/stream": 4.0,
    "/api/agents/content-generation": 2.0,
    "/api/agents/market-insight": 1.0,
    "/api/agents/smart-selection": 1.0,
    "/api/agents/customer-chat": 1.0,
    "/api/agents/compliance": 1.0,
}


# 匿名闸门（W1-5）：登录请求走配额表扣减，但匿名此前完全放行——公网 Demo
# 上任何人可以无限制触发真实 LLM 推理（单次 60–90s）与 Playwright 抓取，
# 成本与可用性双风险。做法是按 IP 滑动窗口限速（不是强制登录——
# Demo 无摩擦对本赛事更重要）。窗口与上限取"够评委演示、挡不住脚本滥用"：
# 5 分钟 10 次足够完整演示多轮，脚本级刷量会被卡住。
_ANON_AGENT_LIMIT_PATHS = ("/api/agents/",)
_ANON_WINDOW_S = 300.0
_ANON_MAX_CALLS = 10
_anon_hits: dict[str, list[float]] = {}


def _anon_agent_allowed(ip: str | None) -> tuple[bool, str]:
    """匿名访问 agent 类端点的滑动窗口判定。返回 (是否放行, 说明)。"""
    if not ip:
        return True, "无法解析客户端地址，放行（本地回环/ unix socket）"
    now = time.monotonic()
    hits = [t for t in _anon_hits.get(ip, []) if now - t < _ANON_WINDOW_S]
    # 内存护栏：IP 表无界增长本身是 DoS 面，超过上限整体重置（罕见，重置后
    # 最多让一个 IP 多用几次额度，不影响正确性）。
    if len(_anon_hits) > 10_000:
        _anon_hits.clear()
    if len(hits) >= _ANON_MAX_CALLS:
        _anon_hits[ip] = hits
        return False, f"匿名调用频率超限：{_ANON_MAX_CALLS} 次 / {_ANON_WINDOW_S:.0f}s（按 IP），请登录获取完整配额"
    hits.append(now)
    _anon_hits[ip] = hits
    return True, ""


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """请求计时与计数（M3-⑤）+ 租户 LLM 配额（M4）。

    /api/metrics 自身不记，否则看一次指标就把指标改了。
    配额：带有效令牌且命中权重表的端点才扣，匿名放行（无租户概念）。
    扣减在业务执行**之前**（防并发刷），但**根本没进业务的失败回滚**：
    404/405/422（路由不存在/方法不对/参数校验失败）不收费——接口都没调对，
    LLM 成本为零，扣了就是抢钱。进了 handler 的失败不回滚（成本已发生）。
    429 计入请求指标但不算 5xx 错误——超额是调用方的错，不是系统坏了。
    """
    path = request.url.path
    if path == "/api/metrics":
        return await call_next(request)
    t0 = time.perf_counter()
    tenant = _tenant_of(request)
    # 把租户带给深层调用链（llm.chat 记 token usage 用）——ContextVar
    # 请求间天然隔离，agents 签名一行不用改。
    ctx_token = current_tenant_id.set(tenant)
    try:
        response = await _dispatch_with_quota(request, call_next, path, t0, tenant)
    finally:
        current_tenant_id.reset(ctx_token)
    return response


async def _dispatch_with_quota(request: Request, call_next, path: str,
                               t0: float, tenant: str | None):
    """配额判定 + 匿名限速 + 业务分发 + 请求计时（metrics_middleware 的主体）。"""
    weight = _QUOTA_WEIGHTS.get(path) if tenant else None
    # 匿名访问 agent 类端点：按 IP 滑动窗口限速（登录用户走配额表，不限这里）
    if tenant is None and path.startswith(_ANON_AGENT_LIMIT_PATHS):
        ok, why = _anon_agent_allowed(request.client.host if request.client else None)
        if not ok:
            response = JSONResponse({"detail": why}, status_code=429)
            monitoring.record_request(path, 429, time.perf_counter() - t0)
            return response
    if weight is not None:
        q = quota.consume(tenant, weight)
        if not q["allowed"]:
            response: Any = JSONResponse(
                {"detail": "今日配额已用完（按请求权重计），UTC 0 点重置",
                 "quota": q}, status_code=429)
            monitoring.record_request(path, 429, time.perf_counter() - t0)
            return response
        response = await call_next(request)
        if response.status_code in (404, 405, 422):
            quota.refund(tenant, weight)    # 没进业务，退回
        else:
            response.headers["X-Quota-Remaining"] = str(q["remaining"])
    else:
        response = await call_next(request)
    monitoring.record_request(path, response.status_code,
                              time.perf_counter() - t0)
    return response


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    """监控指标与告警（单实例进程内口径）。

    /api/health 是"活着没有"；这里是"活着得怎么样"：请求量、5xx 率、
    延迟分位、进程 RSS、告警布尔。判定口径写死在 monitoring.snapshot()：
    high_error_rate = 总请求 ≥ 20 且 5xx 占比 > 5%（样本太小不判定，
    三条请求错一条就报警的系统只会教人忽略告警）。
    """
    snap = monitoring.snapshot()
    snap["llm_online"] = llm.available()
    snap["alarms"] = snap["alarms"] + ([] if llm.available() else ["llm_offline"])
    try:
        snap["storage"] = {"provenance": provenance.stats(),
                           "missions": len(store.list_missions(limit=1000)),
                           "auth": auth.stats(),
                           "llm_usage_today": usage.stats()["today"]}
        snap["storage"]["ok"] = True
    except Exception as exc:  # noqa: BLE001  存储层不可用必须可见，不能 500 掩盖
        snap["storage"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        snap["alarms"] = snap["alarms"] + ["storage_unavailable"]
    return snap


@app.get("/api/tools")
async def tools() -> dict[str, Any]:
    return {"tools": _registry.spec()}


class MarketInsightIn(BaseModel):
    """市场洞察请求：真实抓取优先；allow_sample=False 时严格拒绝样本。"""
    category: str
    marketplace: str = "US"
    keyword: str = ""
    keyword_cn: str = ""
    allow_sample: bool = True


class CustomerChatIn(BaseModel):
    """客服单轮对话：FAQ 为静态样本，LLM 仅组织语言。"""
    message: str
    category: str = "通用"
    language: str = "zh"
    session_id: str = "default"


class SmartSelectionIn(BaseModel):
    """智能选品：真实扫描 + 定价沙盘。无样本兜底，市场无源时直接失败。"""
    category: str
    marketplace: str = "US"
    budget: str = "中"
    keyword: str = ""
    keyword_cn: str = ""
    source: str = ""
    limit: int = 12
    unit_cost_cny: float | None = None


class ContentGenerationIn(BaseModel):
    """内容生成：多语言 × 多平台。产出是文案（generated），不是数据。"""
    product_name: str
    category: str = ""
    platform: str = "shopee"
    target_language: str = "en"
    marketplace: str = "US"
    keyword: str = ""
    use_market_data: bool = True
    # 默认不重试：单次 LLM 约 90 秒，重试会把接口拖到 3 分钟级
    retry_on_failure: bool = False


class ComplianceIn(BaseModel):
    """合规体检：美国走官方源实时验证，东南亚给静态分级参考。"""
    category: str
    target_country: str = "美国"


class AuthRegisterIn(BaseModel):
    email: str
    password: str
    company: str = ""


class AuthLoginIn(BaseModel):
    email: str
    password: str


class AuthRefreshIn(BaseModel):
    refresh_token: str


class PipelineIn(BaseModel):
    """全链路：市场洞察 → 智能选品 → 内容生成 → 合规查询。

    整链 2–3 分钟是常态（单次 LLM 文案就要 60–90 秒），因此提供 SSE 版本
    让前端显示真实进度；同步版本供脚本/API 调用。
    """
    category: str
    marketplace: str = "US"
    budget: str = "中"
    platform: str = "shopee"
    target_language: str = "en"
    target_country: str = "美国"
    keyword: str = ""
    keyword_cn: str = ""
    product_name: str = ""
    include_content: bool = True
    allow_sample: bool = True


@app.get("/api/agents")
async def agents_info() -> dict[str, Any]:
    """产品层 Agent 清单（M1：市场洞察/客服；M2：智能选品）。"""
    return {"agents": [
        {"name": "市场洞察Agent", "endpoint": "POST /api/agents/market-insight",
         "data_quality": "real（真实抓取带存证）/ sample（样本兜底，前端须明示）"},
        {"name": "智能客服Agent", "endpoint": "POST /api/agents/customer-chat",
         "data_quality": "sample_faq（FAQ 静态样本；回复 generated_by=llm|template）"},
        {"name": "智能选品Agent", "endpoint": "POST /api/agents/smart-selection",
         "data_quality": "real（真实扫描 + 定价沙盘）；无样本兜底路径，"
                         "市场无源时返回失败并说明原因"},
        {"name": "内容生成Agent", "endpoint": "POST /api/agents/content-generation",
         "data_quality": "generated（文案为生成内容，非可溯源数据）；"
                         "[ADD] 占位符需卖家补齐真实规格"},
        {"name": "合规Agent", "endpoint": "POST /api/agents/compliance",
         "data_quality": "real（美国：官方页面实时定位条款原文）/ "
                         "sample（东南亚：静态条款库，未经页面验证）"},
        {"name": "全链路工作流", "endpoint": "POST /api/agents/pipeline"
                                            "（流式：/api/agents/pipeline/stream）",
         "data_quality": "逐步标注，不合并成一个总标记；单步失败不影响其余步骤"},
    ]}


@app.post("/api/agents/market-insight")
async def agents_market_insight(body: MarketInsightIn) -> dict[str, Any]:
    return await market_insight.run(
        body.category, marketplace=body.marketplace, keyword=body.keyword,
        keyword_cn=body.keyword_cn, allow_sample=body.allow_sample)


@app.post("/api/agents/customer-chat")
async def agents_customer_chat(body: CustomerChatIn) -> dict[str, Any]:
    return await customer_service.chat(
        body.message, category=body.category, language=body.language,
        session_id=body.session_id)


@app.post("/api/agents/smart-selection")
async def agents_smart_selection(body: SmartSelectionIn) -> dict[str, Any]:
    return await smart_selection.run(
        body.category, marketplace=body.marketplace, budget=body.budget,
        keyword=body.keyword, keyword_cn=body.keyword_cn, source=body.source,
        limit=body.limit, unit_cost_cny=body.unit_cost_cny)


@app.get("/api/agents/content-options")
async def content_options() -> dict[str, Any]:
    """内容生成的可选项：语言（含是否有模板兜底）与平台（含配置上限）。

    为什么要走接口而不是前端硬编码：模板兜底语言表在后端，前端抄一份必然漂移；
    平台上限也一样。前端下拉框直接渲染这个返回值。
    """
    return {
        "languages": [
            {"code": code, "label": spec["label"], "english": spec["english"],
             "template_fallback": code in content_generation._TEMPLATE_LANGS}
            for code, spec in content_generation.LANGUAGES.items()
        ],
        "platforms": [
            {"code": code, "label": spec["label"],
             "title_max": spec["title_max"], "desc_max": spec["desc_max"],
             "highlight_n": spec["highlight_n"]}
            for code, spec in content_generation.PLATFORMS.items()
        ],
        "rules_source": content_generation._RULES_SOURCE,
        "rules_verified": False,
        "llm_available": llm.available(),
    }


@app.post("/api/agents/content-generation")
async def agents_content_generation(body: ContentGenerationIn) -> dict[str, Any]:
    return await content_generation.run(
        body.product_name, category=body.category, platform=body.platform,
        target_language=body.target_language, marketplace=body.marketplace,
        keyword=body.keyword, use_market_data=body.use_market_data,
        retry_on_failure=body.retry_on_failure)


@app.post("/api/agents/compliance")
async def agents_compliance(body: ComplianceIn) -> dict[str, Any]:
    return await compliance.run(body.category, target_country=body.target_country)


@app.get("/api/agents/compliance-options")
async def compliance_options() -> dict[str, Any]:
    """合规可选项：支持的国家 + 每国是否有官方源实时验证。

    前端下拉框渲染这个返回值，不在前端抄一份国家表——抄了必然漂移，
    且会让用户以为东南亚也有实时验证。
    """
    return {
        "countries": [
            {"name": label, "market": market,
             "official_source": market == "US",
             "note": "官方源实时验证" if market == "US"
                     else "静态条款库（无官方 API，未做页面验证）"}
            for market, label in compliance.MARKET_LABEL.items()
        ],
        "categories": ["宠物用品", "家居用品", "3C电子"],
        "tariff_api": False,
        "tariff_note": "HTS 与东盟各国税则无公开免费 API，不输出具体税率",
    }


@app.post("/api/auth/register")
async def auth_register(body: AuthRegisterIn) -> dict[str, Any]:
    """注册：建租户（公司）+ 建用户，直接返回会话令牌。

    密码策略在后端校验（8 位下限）——前端校验只是体验，不是边界。
    """
    result = auth.register(body.email, body.password, body.company)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "注册失败"))
    pair = auth.session_pair(result["user_id"], result["tenant_id"])
    return {"success": True, **pair,
            "user": {"user_id": result["user_id"], "email": result["email"],
                     "tenant_id": result["tenant_id"],
                     "company": (body.company or "").strip()}}


def _admin_token_configured() -> str:
    """运营令牌：env 配了才有后台。空串 = 后台关闭（fail closed）——
    未配置时管理端点 403 并明说原因，不冒充有运营后台。"""
    return os.environ.get("TRADEPILOT_ADMIN_TOKEN", "").strip()


def _admin_authorized(request: Request) -> None:
    """运营端点鉴权。未配置 → 403（功能关着）；配了不匹配 → 401（不许探测
    令牌是否存在——其实 403 已暴露"未配置"，但那是对运营者自己说的，
    外人只知道"进不去"。compare_digest 防时序。"""
    expected = _admin_token_configured()
    provided = request.headers.get("x-admin-token", "")
    if not expected:
        raise HTTPException(status_code=403,
                            detail="运营后台未启用（未配置 TRADEPILOT_ADMIN_TOKEN）")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="运营令牌无效")


@app.get("/api/admin/overview")
async def admin_overview(request: Request) -> dict[str, Any]:
    """运营总览（M4）：平台级聚合数据，不是租户数据面。

    认证：X-Admin-Token 与 env 比对（独立于租户 JWT——运营者是平台方，
    不是任何租户的成员）。返回内容不含密码散列与令牌哈希（auth.export_all
    的红线），邮箱/公司名可见——运营者本来就有权知道谁在用平台。
    """
    _admin_authorized(request)
    snap = monitoring.snapshot()
    return {
        "auth": auth.stats(),
        "tenants": auth.export_all(),
        "quota": quota.stats(),
        "usage": usage.stats(),
        "provenance": provenance.stats(),
        "missions_total": len(store.list_missions(limit=1000)),
        "llm_online": llm.available(),
        "llm_model": CONFIG.llm_model,
        "system": {"uptime_s": snap["uptime_s"],
                   "rss_mb": snap["process_rss_mb"],
                   "requests_total": snap["requests"]["total"],
                   "errors_5xx": snap["requests"]["errors_5xx"],
                   "alarms": snap["alarms"]},
    }


@app.post("/api/auth/login")
async def auth_login(body: AuthLoginIn, request: Request) -> dict[str, Any]:
    """登录。邮箱不存在与密码错误返回同一个 401——不帮人探测哪些邮箱注册过。"""
    ip = request.client.host if request.client else "unknown"
    locked = auth.login_locked(ip)
    if locked:
        raise HTTPException(status_code=429, detail=f"失败次数过多，请 {locked} 秒后再试")
    user = auth.authenticate(body.email, body.password)
    if user is None:
        auth.record_login_fail(ip)
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    auth.clear_login_fails(ip)
    return {**auth.session_pair(user["user_id"], user["tenant_id"]),
            "user": {"user_id": user["user_id"], "email": user["email"],
                     "tenant_id": user["tenant_id"]}}


@app.post("/api/auth/refresh")
async def auth_refresh(body: AuthRefreshIn) -> dict[str, Any]:
    """刷新令牌：用一次换一次（rotation）。旧令牌续期后立即作废，
    被盗令牌的重放会撞上 revoked 记录而失败。"""
    pair = auth.rotate_refresh_token(body.refresh_token)
    if pair is None:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期")
    return {"access_token": pair[0], "refresh_token": pair[1],
            "token_type": "bearer", "expires_in": auth.ACCESS_TOKEN_TTL_S}


@app.post("/api/auth/logout")
async def auth_logout(body: AuthRefreshIn) -> dict[str, Any]:
    revoked = auth.revoke_refresh_token(body.refresh_token)
    return {"success": True, "revoked": revoked}


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    user = auth.current_user(request.headers.get("authorization"),
                                 request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    return user


@app.post("/api/agents/pipeline")
async def agents_pipeline(body: PipelineIn) -> dict[str, Any]:
    """同步全链路：跑完再返回，供脚本/API 调用（浏览器请用 SSE 版）。"""
    return await pipeline.run(body.category, **body.model_dump(exclude={"category"}))


@app.post("/api/agents/pipeline/stream")
async def agents_pipeline_stream(body: PipelineIn) -> StreamingResponse:
    """SSE 全链路：每完成一步推一个事件，前端据此点亮节点。

    为什么不用前端定时器假装进度：整链 2–3 分钟，假进度会在真正卡住的
    时候继续往前跑，用户等到"全绿"才发现其实早就失败了。
    """
    params = body.model_dump(exclude={"category"})

    async def gen():
        async for ev in pipeline.run_steps(body.category, **params):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/me/usage")
async def me_usage(request: Request) -> dict[str, Any]:
    """当前租户的用量自查（计费闭环的用户侧）：配额余额 + 自己的 token
    明细。登录可见——匿名没有账可查。"""
    user = auth.current_user(request.headers.get("authorization"),
                                 request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    tid = str(user["tenant_id"])
    return {"tenant_id": tid,
            "quota": quota.status(tid),
            "llm_usage": usage.tenant_stats(tid)}


class BillingOrderIn(BaseModel):
    plan: str                        # pro | self_test（价格/权益服务端定，客户端传不了金额）
    return_path: str = "/app"        # 支付宝同步回跳目标（前端传，须以 / 开头）


@app.post("/api/billing/create-order")
async def billing_create_order(body: BillingOrderIn,
                               request: Request) -> dict[str, Any]:
    """下单：登录租户按套餐创建订单并签名支付链接。

    未配置支付凭证时 503 明说（fail closed，不假装能收钱）。
    价格、权益、自测标记全在服务端 PLANS，防低价下单。
    """
    user = auth.current_user(request.headers.get("authorization"),
                             request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    if body.plan not in billing.PLANS:
        raise HTTPException(status_code=400, detail="未知套餐")
    if not body.return_path.startswith("/") or ".." in body.return_path:
        raise HTTPException(status_code=400, detail="回跳路径非法")
    base = _public_base(request)
    try:
        order = billing.create_order(str(user["tenant_id"]), body.plan)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # 支付链接的 return_url 指向前端站点（非 API base），使同步回跳落到应用页
    return {**order,
            "return_url": f"{base}{body.return_path}?order={order['out_trade_no']}"}


def _public_base(request: Request) -> str:
    """代理后的公网基址：优先转发头，退化到 base_url。支付宝 return_url
    必须是公网可访问的绝对地址，内网 host 会导致回跳失败。"""
    host = request.headers.get("x-forwarded-host")
    proto = request.headers.get("x-forwarded-proto", "https")
    if host:
        return f"{proto}://{host.rstrip('/')}"
    return str(request.base_url).rstrip("/")


@app.get("/api/billing/orders")
async def billing_orders(request: Request) -> dict[str, Any]:
    """我的订单（只看自己的）。"""
    user = auth.current_user(request.headers.get("authorization"),
                             request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    return {"orders": billing.list_orders(str(user["tenant_id"]))}


@app.post("/api/billing/orders/{out_trade_no}/sync")
async def billing_order_sync(out_trade_no: str, request: Request) -> dict[str, Any]:
    """主动对账（查询兜底）：通知没到/用户回跳时也能确认支付结果。"""
    user = auth.current_user(request.headers.get("authorization"),
                             request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    order = await billing.sync_order(str(user["tenant_id"]), out_trade_no)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在或不属于当前租户")
    return order


@app.post("/alipay/notify")
async def alipay_notify(request: Request) -> PlainTextResponse:
    """支付宝异步通知：验签 + 业务校验 + 幂等履约，回 success/fail。

    公网可访问（不走租户鉴权——可信度全靠 RSA2 验签）；
    失败回 fail 让支付宝按策略重试，不编造成功。
    """
    form = dict(await request.form())
    return PlainTextResponse(billing.handle_notify(form))



@app.get("/api/admin/trends")
async def admin_trends(request: Request, days: int = 14) -> dict[str, Any]:
    """看板趋势数据（M4 数据看板）。只画真实可算的东西：

    - 任务量按报告 started_at 聚合（没任务的天补 0——平线是真的没任务）；
    - 配额消耗按天聚合（历史自配额功能上线起才有，之前的天如实为 0，
      不回填不估算）；
    - 存证按 kind 分布。
    """
    _admin_authorized(request)
    days = max(3, min(days, 60))
    return {"days": days,
            "missions_by_day": store.missions_by_day(days),
            "quota_by_day": quota.history(days),
            "provenance_by_kind": provenance.kind_stats()}


class IntegrationExchangeIn(BaseModel):
    """店铺授权回调：code 换店铺令牌。code 一次性且短时效（约 5 分钟）。"""
    code: str
    main_merchant_id: str = ""      # Shopee 多 merchant 场景
    redirect: str = ""              # Lazada uuid / 回调覆盖


class ShopCallIn(BaseModel):
    """店铺级业务调用请求体。path 是平台 API 纯路径（不带 query）。"""
    path: str
    params: dict[str, str] = {}
    method: str = "GET"


_INTEGRATION_PLATFORMS = {"shopee", "lazada", "shopify"}
# Shopify 不走"授权链接 + code 换 token"流程：自定义 App 模式下 token 由
# 店铺后台直接生成，用 /connect 贴 token 直连（见 shopify.py docstring）。
_TOKEN_CONNECT_PLATFORMS = {"shopify"}
_SHOP_CALL_METHODS = {"GET", "POST"}


def _require_user(request: Request) -> dict[str, Any]:
    """店铺授权是租户动作：授权后的店铺 API 调用消耗该租户的配额。
    未登录 401。"""
    user = auth.current_user(request.headers.get("authorization"),
                                 request.headers.get("x-auth-token"))
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或令牌已过期")
    return user


@app.get("/api/integrations/{platform}/auth-url")
async def integration_auth_url(platform: str, request: Request) -> dict[str, Any]:
    """店铺授权页链接。凭证未配置返回 409（不是 404——"功能存在但没条件"
    与"没有这个平台"是两回事）。"""
    _require_user(request)
    platform = platform.strip().lower()
    if platform not in _INTEGRATION_PLATFORMS:
        raise HTTPException(status_code=404, detail=f"不支持的平台：{platform}")
    if platform in _TOKEN_CONNECT_PLATFORMS:
        raise HTTPException(
            status_code=409,
            detail=f"{platform} 不使用授权链接——在店铺后台创建自定义 App 后，"
                   f"用 POST /api/integrations/{platform}/connect 贴 token 直连")
    url = shopee.auth_url() if platform == "shopee" else lazada.auth_url()
    if not url:
        raise HTTPException(
            status_code=409,
            detail=f"{platform} 凭证未配置（平台后台 Create App 后写入 .env.local）")
    return {"platform": platform, "auth_url": url}


@app.post("/api/integrations/{platform}/exchange")
async def integration_exchange(platform: str, body: IntegrationExchangeIn,
                               request: Request) -> dict[str, Any]:
    """授权 code 换店铺令牌。成功即入进程内 TOKENS（后续店铺级调用自动可用；
    重启即清，明文永不回传）。失败原样带回平台错误码——排查表见
    docs/ops-readiness.md。"""
    user = _require_user(request)
    platform = platform.strip().lower()
    if platform not in _INTEGRATION_PLATFORMS:
        raise HTTPException(status_code=404, detail=f"不支持的平台：{platform}")
    if platform in _TOKEN_CONNECT_PLATFORMS:
        raise HTTPException(
            status_code=409,
            detail=f"{platform} 不使用授权 code——"
                   f"用 POST /api/integrations/{platform}/connect 贴 token 直连")
    if not body.code.strip():
        raise HTTPException(status_code=400, detail="code 不能为空")
    if platform == "shopee":
        result = await shopee.exchange_auth_code(
            body.code.strip(), body.main_merchant_id.strip(), store=True)
    else:
        result = await lazada.exchange_auth_code(
            body.code.strip(), redirect=body.redirect.strip(), store=True)
    if not result.ok:
        raise HTTPException(status_code=502,
                            detail={"success": False,
                                    "error_code": result.error_code,
                                    "error_msg": result.error_msg})
    data = result.data or {}
    # 授权关系显式落库（多租户安全收口）：哪个店铺属于哪个租户，从此可考。
    # shop_id 为空（Lazada 响应形态待联调确认）记空串占位，不猜字段。
    shops.authorize(str(user["tenant_id"]), platform, str(data.get("shop_id") or ""))
    return {"success": True, "platform": platform, **data}


class ShopConnectIn(BaseModel):
    """Shopify 自定义 App token 直连：店铺域名 + Admin API access token。
    token 在店铺后台 Develop apps → Install 生成（shpat_ 前缀），明文只
    进请求体一次，之后与 shopee/lazada 同样只存进程内缓存、永不回传。"""
    shop: str
    token: str


@app.post("/api/integrations/shopify/connect")
async def integration_shopify_connect(body: ShopConnectIn,
                                      request: Request) -> dict[str, Any]:
    """Shopify 店铺直连：验证 token 可调真实 API（/shop.json），通过即
    落租户归属 + 进进程内缓存。验证失败不落任何状态——"连上"必须是
    真的能调 API，不是 token 格式对。"""
    user = _require_user(request)
    result = await shopify.verify_and_connect(body.token, body.shop)
    if not result.ok:
        raise HTTPException(status_code=502,
                            detail={"success": False,
                                    "error_code": result.error_code,
                                    "error_msg": result.error_msg})
    data = result.data or {}
    shop_id = str(data.get("shop_id") or "")
    shops.authorize(str(user["tenant_id"]), "shopify", shop_id)
    return {"success": True, "platform": "shopify", **data}


@app.get("/api/integrations/{platform}/shops")
async def integration_shops(platform: str, request: Request) -> dict[str, Any]:
    """当前租户已授权的店铺清单。只返回**自己的**——
    租户 A 授权的店铺对租户 B 不存在。"""
    user = _require_user(request)
    platform = platform.strip().lower()
    if platform not in _INTEGRATION_PLATFORMS:
        raise HTTPException(status_code=404, detail=f"不支持的平台：{platform}")
    rows = shops.shops_of(str(user["tenant_id"]), platform)
    # 附上 token 存活状态（进程内缓存口径，重启后显示需重新授权——如实）
    return {"platform": platform, "shops": [
        {**r, "token_alive": mauth.TOKENS.get(platform, r["shop_id"]) is not None}
        for r in rows]}


@app.post("/api/integrations/{platform}/shops/{shop_id}/call")
async def integration_shop_call(platform: str, shop_id: str, body: ShopCallIn,
                                request: Request) -> dict[str, Any]:
    """店铺级业务调用网关：只放行**本租户已授权店铺**的业务接口。

    闸门顺序（每一层失败语义都不同，混了会把运营者引去查错地方）：
    1. 未登录 401；
    2. 平台不支持 404；
    3. 无授权关系 404——跨租户与不存在同响应，与店铺清单口径一致；
    4. 授权关系在但进程内令牌不可用 409——"关系还在但令牌没了"（重启/
       过期）是重新授权的信号，不能拿 404 冒充，也不能假装能用；
    5. path 白名单 403——auth 类接口不得穿过店铺调用网关。

    平台业务错误（HTTP 200 + error 信封）如实透传为 502 + error_code。
    Lazada 的 shop_id 响应形态待真实联调确认（见 4.16），当前其归属
    记录可能为空串占位，届时以联调结果修正寻址方式。
    """
    user = _require_user(request)
    platform = platform.strip().lower()
    if platform not in _INTEGRATION_PLATFORMS:
        raise HTTPException(status_code=404, detail=f"不支持的平台：{platform}")
    shop_id = shop_id.strip()
    if not shop_id or not shops.is_authorized(str(user["tenant_id"]),
                                              platform, shop_id):
        raise HTTPException(status_code=404,
                            detail="店铺不存在或未授权到当前账号")
    token = mauth.TOKENS.get(platform, shop_id)
    if token is None:
        raise HTTPException(
            status_code=409,
            detail="店铺授权令牌不可用（已过期或服务重启后缓存丢失）——"
                   "请重新完成店铺授权")
    prefixes = {"shopee": shopee.SHOP_SCOPED_PREFIXES,
                "lazada": lazada.SHOP_SCOPED_PREFIXES,
                "shopify": shopify.SHOP_SCOPED_PREFIXES}[platform]
    path = body.path.strip()
    if not any(path.startswith(p) for p in prefixes):
        raise HTTPException(
            status_code=403,
            detail=f"只允许店铺级业务接口（前缀：{', '.join(prefixes)}）——"
                   "auth 类接口不走这个网关")
    method = body.method.strip().upper()
    if method not in _SHOP_CALL_METHODS:
        raise HTTPException(status_code=400,
                            detail=f"method 仅支持 GET/POST，收到：{method}")
    if platform == "shopify":
        # Shopify 走 token + 店铺域名寻址，签名层与 shopee/lazada 不同
        result = await shopify.call(shop_id, token.access_token, path,
                                    dict(body.params), method=method)
    else:
        call = shopee.call if platform == "shopee" else lazada.call
        result = await call(path, dict(body.params), method=method, token=token)
    if not result.ok:
        raise HTTPException(status_code=502,
                            detail={"success": False,
                                    "error_code": result.error_code,
                                    "error_msg": result.error_msg})
    return {"success": True, "platform": platform, "shop_id": shop_id,
            "data": result.data}


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    """能力声明（不是数据）：首页那些数字必须能在这里对上。

    之前首页写死「50+ 覆盖市场」「8 支持语言」「5 合规国家」——
    50+ 是编的（真实可抓市场只有 US），语言实际 9 种，合规国家实际 7 个。
    页面顶上还写着"每个数字都带来溯源存证"，这行字下面摆编造数字是自相矛盾。
    现在所有计数都从 Agent 的定义里算出来。
    """
    from .agents.sample_market import CATEGORY_KEYWORDS

    # CATEGORY_KEYWORDS 里同一品类有多个别名（3C数码/3C电子、服装/服装服饰），
    # 直接 len() 会把 4 个品类数成 6 个。这里按前端展示名去重。
    display = {"宠物用品": "宠物用品", "家居用品": "家居用品",
               "3C数码": "3C电子", "3C电子": "3C电子",
               "服装": "服装服饰", "服装服饰": "服装服饰"}
    with_keywords = sorted({display.get(k, k) for k in CATEGORY_KEYWORDS})
    official = sum(1 for m in compliance.MARKET_LABEL if m == "US")
    return {
        "categories": len(with_keywords),
        "categories_with_builtin_keywords": with_keywords,
        "languages": len(content_generation.LANGUAGES),
        "languages_with_template": len(content_generation._TEMPLATE_LANGS),
        "platforms": len(content_generation.PLATFORMS),
        "compliance_countries": len(compliance.MARKET_LABEL),
        "compliance_official_source_countries": official,
        "agents": 5,
        "note": "以上均为能力声明（能做什么），不是市场规模/增长率等业务数据；"
                "「可直接跑数据的品类」= 后端有内置检索词、无需用户自填关键词的品类。"
                "真实可抓市场当前只有 US，其余市场会如实报错而非返回样本。",
    }


@app.get("/api/provenance/{pid}")
async def provenance_detail(pid: int) -> JSONResponse:
    """溯源详情：前端「存证#N」点开的就是它。

    返回 URL + 抓取时间戳 + 内容 hash + 原始字段——作品的差异化承诺是
    "页面上每个数字都能溯源"，没有这个接口那句承诺就只能靠截图像素证明。
    """
    record = provenance.get(pid)
    if record is None:
        raise HTTPException(status_code=404, detail=f"存证 #{pid} 不存在")
    return JSONResponse(record)


@app.get("/api/provenance")
async def provenance_list(kind: str = "", limit: int = 50) -> dict[str, Any]:
    """存证清单（可按 kind 过滤）：抽检与答辩现场随机回访用。"""
    return {"stats": provenance.stats(), "items": provenance.list_records(
        kind=kind, limit=max(1, min(limit, 200)))}


def _tenant_of(request: Request) -> str | None:
    """从请求解析租户。未认证 → None（单机/演示模式保持开放，匿名可用）。

    但**一旦带有效令牌，就只看得见自己租户的任务存档**——
    隔离必须是"登录后收紧"，不是"登录后照旧全局可见"。
    """
    user = auth.current_user(request.headers.get("authorization"),
                                 request.headers.get("x-auth-token"))
    return str(user["tenant_id"]) if user else None


@app.get("/api/missions")
async def missions(request: Request, limit: int = 20) -> dict[str, Any]:
    return {"missions": store.list_missions(
        limit=max(1, min(limit, 100)), tenant_id=_tenant_of(request))}


@app.get("/api/missions/{mission_id}")
async def mission_detail(request: Request, mission_id: str) -> JSONResponse:
    report = store.get_mission(mission_id, tenant_id=_tenant_of(request))
    if report is None:
        raise HTTPException(status_code=404, detail="任务不存在或 id 非法")
    return JSONResponse(report)


@app.get("/api/missions/{mission_id}/export")
async def mission_export(request: Request, mission_id: str, format: str = "md") -> Any:
    """导出上架包：json（原始报告）/ md（上架包）/ docx（Word）/ opc（想法→生意简报）。

    ``opc`` 是面向 OPC 一人公司叙事的“想法→生意”价值链简报：
    内容全部来自同一次任务的真实报告，不额外造数（见 app/opc_brief.py 红线）。
    """
    report = store.get_mission(mission_id, tenant_id=_tenant_of(request))
    if report is None:
        raise HTTPException(status_code=404, detail="任务不存在或 id 非法")
    if format == "json":
        return JSONResponse(report, headers={
            "Content-Disposition": f'attachment; filename="{mission_id}.json"'})
    if format in ("md", "markdown"):
        text = store.export_markdown(report)
        return PlainTextResponse(text, headers={
            "Content-Disposition": f'attachment; filename="{mission_id}.md"'},
            media_type="text/markdown; charset=utf-8")
    if format == "opc":
        from .opc_brief import build_opc_brief, export_opc_markdown

        brief = build_opc_brief(report)
        text = export_opc_markdown(brief)
        return PlainTextResponse(text, headers={
            "Content-Disposition": f'attachment; filename="{mission_id}-opc.md"'},
            media_type="text/markdown; charset=utf-8")
    if format == "docx":
        # 局部导入：导出函数之外的行不属本模块改动范围，避免动共享的 import 区
        from fastapi.responses import Response

        from .export_docx import build_docx

        payload = build_docx(report)  # 零依赖：zipfile 拼 WordprocessingML
        return Response(content=payload, headers={
            "Content-Disposition": f'attachment; filename="{mission_id}.docx"'},
            media_type="application/vnd.openxmlformats-officedocument"
                       ".wordprocessingml.document")
    raise HTTPException(status_code=400, detail="format 仅支持 json / md / docx / opc")


@app.get("/api/opc/brief")
async def opc_brief_latest(request: Request) -> dict[str, Any]:
    """最新一次任务的 OPC「想法→生意」简报（JSON）。

    参赛现场可直接打开这个接口，把“一句话目标”到“可上架生意”的价值链讲给评委，
    无需依赖某个具体 mission_id。没有归档任务时返回 404，不编造示例。
    """
    from .opc_brief import build_opc_brief

    missions = store.list_missions(limit=1, tenant_id=_tenant_of(request))
    if not missions:
        raise HTTPException(status_code=404, detail="尚无归档任务，先下达一次任务")
    full = store.get_mission(missions[0]["mission_id"], tenant_id=_tenant_of(request))
    if full is None:
        raise HTTPException(status_code=404, detail="任务档案不可读")
    return build_opc_brief(full)


@app.get("/")
async def root() -> RedirectResponse:
    """根路径指向主前端。

    M2 起 React 是主前端（`/app`），Canvas 大屏收敛为独立路由 `/canvas`。
    之前大屏占着 `/`，导致访问根路径看到的是 M1 的运维大屏而不是产品界面——
    两套前端并存时，"谁是主入口"必须只有一个答案。
    """
    return RedirectResponse(url="/app", status_code=307)


@app.get("/canvas")
async def canvas_dashboard() -> FileResponse:
    """Canvas 运营大屏（M1 原生 Canvas 实现），独立路由保留。

    它和 React 主前端不是替代关系：大屏给运营看任务编排与存证的实时视图，
    React 给卖家走选品→文案→合规的工作流。两条入口各自独立，互不抢根路径。
    """
    return FileResponse(WEB / "index.html")


@app.get("/static/{name}")
async def static_file(name: str) -> FileResponse:
    if name not in STATIC_WHITELIST:  # 白名单：不做目录拼接，杜绝路径穿越
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(WEB / name)


# ---- React 前端（M2 起的主前端，构建产物由本服务托管）----
_WEB_REACT = Path(__file__).resolve().parent.parent / "web-react" / "dist"


@app.get("/app")
async def react_app_root() -> FileResponse:
    if not (_WEB_REACT / "index.html").exists():
        raise HTTPException(status_code=404,
                            detail="前端未构建：cd web-react && npm install && npm run build")
    return FileResponse(_WEB_REACT / "index.html")


@app.get("/app/{path:path}")
async def react_app(path: str) -> FileResponse:
    """单页应用托管：存在的静态文件直出，未知路径回 index.html（前端路由）。

    路径先 resolve 再做前缀校验，防止 ../ 穿越到仓库其他文件。
    """
    root = _WEB_REACT.resolve()
    candidate = (_WEB_REACT / path).resolve()
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=404, detail="not found")
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(_WEB_REACT / "index.html")


@app.websocket("/ws/mission")
async def mission_ws(ws: WebSocket) -> None:
    """WebSocket 任务通道：收一条任务 JSON，流式回推决策卡与最终报告。

    匿名闸门：本通道暂无认证（大屏为内部运维视图），但此前完全开放意味着
    公网上任何人可以无限制触发真实任务。复用 HTTP 层同款按 IP 滑动窗口。
    """
    ok, why = _anon_agent_allowed(ws.client.host if ws.client else None)
    if not ok:
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "summary": why},
                                      ensure_ascii=False))
        await ws.close()
        return
    await ws.accept()
    try:
        raw = await ws.receive_text()
        req = MissionIn(**json.loads(raw))
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        await ws.send_text(json.dumps({"type": "error", "summary": str(exc)}, ensure_ascii=False))
        await ws.close()
        return

    queue: asyncio.Queue = asyncio.Queue()

    async def runner() -> None:
        try:
            async for event in run_mission(req.goal, req.model_dump()):
                # 归档已在编排层（run_mission 内）完成：此前 WS 里再存一次是
                # 覆盖写，且把 provenance 改成 stats 后重复落盘，口径混乱。
                # WS 通道暂无认证（大屏为内部运维视图），编排层读到 tenant
                # 为 None = 匿名口径，与 usage 记账一致。
                await queue.put(event)
            await queue.put(None)
        except Exception as exc:  # noqa: BLE001
            await queue.put({"type": "error", "summary": f"{type(exc).__name__}: {exc}"})
            await queue.put(None)

    task = asyncio.create_task(runner())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await ws.send_text(json.dumps(event, ensure_ascii=False, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001,S110
            pass
