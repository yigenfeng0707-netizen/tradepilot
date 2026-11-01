# Copyright 2026 TradePilot project. Apache-2.0.
"""外部合规复核（第三方服务 ListingGood）——可选的第二意见，不是审计权威。

## 为什么不把它接成审计主引擎

实测过三个端点后才敢这么定：

1. **它的浅检规则引擎有可见误报。** 同一段合格英文文案
   （"Non slip silicone base stops sliding on tile"），它把 `stops` 判成
   「医疗健康宣称 high」，把 `BPA free` 在同一次报告里重复计了两次
   （3 条违规实际只有 2 类）。拿它当闸门，会把合格文案拦下来。
2. **深度体检是计费接口。** 每次调用扣月度额度，评测批量跑（3 品类 × 3 轮）
   会烧掉额度，且结论依赖外部服务可用性。参赛作品的核心能力不能建立在
   第三方按次计费的接口上——现场断网、额度用尽，作品就没了。
3. **我们自己的规则审计是确定性的**：同样的文案必然得到同样的分数，
   可离线复算、可单测、可答辩现场复现。这才是能交付的那一层。

所以定位是**交叉校验（cross-check）**：本地规则给分（权威），外部给第二意见。
两者不一致时**并排展示分歧，不取平均**——分歧本身是信息，取平均会把
"两边都没把握"伪装成"两边都同意"。

## 三种模式（默认 off）

- `off`   不调用任何外部接口，审计结果与接入前完全一致（评测/离线演示用这个）；
- `check` 免费浅检 `/api/compliance-check`，用于演示交叉校验；
- `deep`  计费深度体检 `/api/scan/deep`（异步任务 + 轮询），人工演示时按需开。

深度体检的价值确实高于浅检：它是 LLM 语义分析，会引用亚马逊具体政策条款
（如 Product Authenticity & Anti-Counterfeiting Policy、California Prop 65），
而浅检只是关键词表。所以 `deep` 是给人看的，`check` 是给流程看的。

## 密钥

`LISTINGGOOD_API_KEY` 环境变量优先，其次仓库根目录 `.listinggood_key`
（已在 .gitignore，与 `.llm_key` 同惯例）。**绝不写进代码或提交入库。**
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import CONFIG

SERVICE = "ListingGood"
BASE_URL = "https://listinggood.cn"

# 浅检快（实测 0.8s），深度体检是异步任务（实测约 10-20s 出结果）
SHALLOW_TIMEOUT_S = 12.0
DEEP_SUBMIT_TIMEOUT_S = 20.0
DEEP_TOTAL_TIMEOUT_S = 90.0
DEEP_POLL_INTERVAL_S = 3.0

# 外部严重度 → 本地口径（本地只有 critical/warning/info 三档）
_SEVERITY_MAP = {"high": "critical", "critical": "critical",
                 "medium": "warning", "warning": "warning",
                 "low": "info", "info": "info"}


@dataclass
class Review:
    """一次外部复核的结果。status 决定下游怎么解读，不要看 score 猜。"""
    status: str                      # off / ok / unavailable / error / timeout
    mode: str = "off"
    score: int | None = None
    level: str | None = None
    summary: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    task_id: str | None = None
    latency_ms: int = 0
    fetched_at: str = ""
    note: str = ""
    endpoint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def critical_n(self) -> int:
        return sum(1 for i in self.issues if i.get("severity") == "critical")

    def to_json(self) -> dict[str, Any]:
        return {
            "service": SERVICE,
            "status": self.status,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "score": self.score,
            "level": self.level,
            "summary": self.summary[:600],
            "issues": self.issues[:12],
            "issue_n": len(self.issues),
            "critical_n": self.critical_n,
            "task_id": self.task_id,
            "latency_ms": self.latency_ms,
            "fetched_at": self.fetched_at,
            "note": self.note,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mode() -> str:
    """当前模式；未配置 Key 时无论设成什么都降级为 off。"""
    m = (CONFIG.listinggood_mode or "off").strip().lower()
    if m not in ("off", "check", "deep"):
        m = "off"
    if m != "off" and not CONFIG.listinggood_api_key:
        return "off"
    return m


def available() -> bool:
    return mode() != "off"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {CONFIG.listinggood_api_key}",
            "Content-Type": "application/json"}


def _dedupe(issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """去掉完全重复的条目。

    实测浅检会把同一个命中重复计入（`BPA free` 在一次报告里出现两次），
    不去重会让"3 项高危"看起来比实际严重。重复条数如实记在 duplicates_removed。
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    dup = 0
    for it in issues:
        key = (str(it.get("category") or it.get("area") or ""),
               str(it.get("matched") or it.get("evidence") or it.get("finding") or ""))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        out.append(it)
    return out, dup


def _norm_shallow(report: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """浅检 violations → 统一 issue 形状。"""
    raw = report.get("violations") or []
    issues = [{
        "severity": _SEVERITY_MAP.get(str(v.get("severity") or "").lower(), "info"),
        "area": str(v.get("field") or ""),
        "finding": str(v.get("category") or ""),
        "evidence": str(v.get("matched") or ""),
        "remediation": str(v.get("suggestion") or v.get("note") or ""),
        "source": "ListingGood rules engine",
    } for v in raw]
    return _dedupe(issues)


def _norm_deep(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """深度体检 issues → 统一 issue 形状（它自带 source，指向具体政策条款）。"""
    raw = result.get("issues") or []
    issues = [{
        "severity": _SEVERITY_MAP.get(str(v.get("severity") or "").lower(), "info"),
        "area": str(v.get("area") or ""),
        "finding": str(v.get("finding") or ""),
        "evidence": str(v.get("evidence") or ""),
        "remediation": str(v.get("remediation") or ""),
        "source": str(v.get("source") or "ListingGood knowledge base"),
    } for v in raw]
    return _dedupe(issues)


def _copy_text(copy: dict[str, Any]) -> str:
    """深度体检只收一段纯文本（标题 + 五点 + 描述），自己拼。"""
    parts = [str(copy.get("title") or "")]
    bullets = copy.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    for i, b in enumerate(bullets, 1):
        if str(b).strip():
            parts.append(f"{i}. {b}")
    desc = str(copy.get("description") or "").strip()
    if desc:
        parts.append(desc)
    return "\n\n".join(p for p in parts if p.strip())


async def _shallow(client: httpx.AsyncClient, copy: dict[str, Any],
                   marketplace: str) -> Review:
    """免费浅检。请求体是 `listing` 对象——文档写的是扁平 title/bullets，
    实测会 400 报「请提供 listing 或 text」，以实测为准。"""
    payload = {"listing": {"title": str(copy.get("title") or ""),
                           "bullets": [str(b) for b in (copy.get("bullets") or [])],
                           "description": str(copy.get("description") or "")},
               "marketplace": marketplace}
    r = await client.post(f"{BASE_URL}/api/compliance-check", json=payload,
                          headers=_headers(), timeout=SHALLOW_TIMEOUT_S)
    if r.status_code != 200:
        return Review(status="error", mode="check",
                      summary=f"HTTP {r.status_code}: {r.text[:200]}",
                      endpoint="/api/compliance-check", fetched_at=_now())
    data = r.json()
    report = data.get("report") or {}
    issues, dup = _norm_shallow(report)
    note = f"浅检为关键词表规则，已知误报：把 'stops sliding' 的 stops 判医疗宣称。"
    if dup:
        note += f" 原始报告有 {dup} 条重复命中，已去重。"
    return Review(
        status="ok", mode="check",
        score=report.get("score"), level=str(report.get("level") or "") or None,
        summary=str(report.get("summary") or ""), issues=issues,
        endpoint="/api/compliance-check", fetched_at=_now(), note=note,
    )


async def _deep(client: httpx.AsyncClient, copy: dict[str, Any],
                marketplace: str) -> Review:
    """计费深度体检：提交拿 task_id，再轮询 /api/task/<id>。"""
    payload = {"text": _copy_text(copy), "marketplace": marketplace, "images": []}
    r = await client.post(f"{BASE_URL}/api/scan/deep", json=payload,
                          headers=_headers(), timeout=DEEP_SUBMIT_TIMEOUT_S)
    if r.status_code != 200:
        return Review(status="error", mode="deep",
                      summary=f"提交失败 HTTP {r.status_code}: {r.text[:200]}",
                      endpoint="/api/scan/deep", fetched_at=_now())
    task_id = str((r.json() or {}).get("task_id") or "")
    if not task_id:
        return Review(status="error", mode="deep", summary="提交成功但未返回 task_id",
                      endpoint="/api/scan/deep", fetched_at=_now())

    deadline = time.monotonic() + DEEP_TOTAL_TIMEOUT_S
    last_msg = ""
    while time.monotonic() < deadline:
        await _sleep(DEEP_POLL_INTERVAL_S)
        try:
            q = await client.get(f"{BASE_URL}/api/task/{task_id}",
                                 headers=_headers(), timeout=DEEP_SUBMIT_TIMEOUT_S)
        except httpx.HTTPError as exc:
            last_msg = f"轮询异常：{type(exc).__name__}"
            continue
        if q.status_code != 200:
            last_msg = f"轮询 HTTP {q.status_code}"
            continue
        task = q.json() or {}
        status = str(task.get("status") or "")
        last_msg = str(task.get("message") or status)
        if status == "done":
            result = task.get("result") or {}
            issues, dup = _norm_deep(result)
            note = "深度体检为 LLM 语义分析，引用具体政策条款；含月度计费额度。"
            if dup:
                note += f" 已去重 {dup} 条。"
            return Review(
                status="ok", mode="deep",
                score=result.get("score"), level=str(result.get("level") or "") or None,
                summary=str(result.get("summary") or ""), issues=issues,
                task_id=task_id, endpoint="/api/scan/deep",
                fetched_at=_now(), note=note,
            )
        if status in ("error", "failed"):
            return Review(status="error", mode="deep",
                          summary=str(task.get("error") or last_msg or "任务失败"),
                          task_id=task_id, endpoint="/api/scan/deep", fetched_at=_now())
    # 超时：把 task_id 留下，人还能自己去查，而不是"结果丢了"
    return Review(status="timeout", mode="deep",
                  summary=f"轮询 {int(DEEP_TOTAL_TIMEOUT_S)}s 未出结果（{last_msg}）",
                  task_id=task_id, endpoint="/api/scan/deep", fetched_at=_now())


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


async def review(copy: dict[str, Any], marketplace: str = "US") -> Review:
    """外部复核入口。**永不抛出**——外部服务挂了不能影响本地审计出结果。"""
    m = mode()
    if m == "off":
        reason = ("未配置 LISTINGGOOD_API_KEY" if not CONFIG.listinggood_api_key
                  else f"模式为 {CONFIG.listinggood_mode}")
        return Review(status="off", mode="off",
                      note=f"外部复核未启用（{reason}）；本结果仅来自本地规则审计。")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            out = (await _deep(client, copy, marketplace) if m == "deep"
                   else await _shallow(client, copy, marketplace))
    except httpx.HTTPError as exc:
        out = Review(status="error", mode=m,
                     summary=f"{type(exc).__name__}: {exc}"[:200], fetched_at=_now())
    except Exception as exc:  # noqa: BLE001 — 外部服务任何异常都不得中断审计
        out = Review(status="error", mode=m,
                     summary=f"{type(exc).__name__}: {exc}"[:200], fetched_at=_now())
    out.latency_ms = int((time.monotonic() - started) * 1000)
    if not out.fetched_at:
        out.fetched_at = _now()
    return out


_STOP = frozenset("the and for with that this from your you are was were has have "
                  "not but all any can will its our their it's".split())


def _overlaps(needle: str, haystack: str) -> bool:
    """粗匹配：needle 里**任一**实词（≥4 字符、非停用词）出现在 haystack 就算同一处问题。

    为什么不用整串子串匹配：外部证据常是长句（
    `标题: 'Stainless Steel Dog Bowl'; 五点1: 'BPA free materials'`），
    而本地证据是命中的原文片段（`BPA free`），整串互查几乎永不命中，
    会把所有问题都算成"对方独有"，分歧数虚高。

    为什么是"任一"而不是"最长两个"：最长两个会挑中 `stainless`/`materials`
    这类只在外部句子里出现、本地片段里没有的词，照样判不中。取"任一"的偏差
    方向是**宁可多判重叠**，也就是宁可少报"我们漏了"——这个方向是保守的，
    不会凭空给本地审计加一条它其实没漏的指控。
    """
    if not needle or not haystack:
        return False
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'\-]{3,}", needle.lower())
             if w not in _STOP]
    if not words:
        return False
    hay = haystack.lower()
    return any(w in hay for w in set(words))


def cross_check(local: dict[str, Any], ext: Review) -> dict[str, Any]:
    """本地规则审计 vs 外部复核：给出 agree / divergent / unavailable。

    刻意不做"加权平均分"：两个引擎的方法不同（一个是确定性关键词表 +
    字符上限，一个是 LLM 语义 + 知识库），平均出来的数没有物理意义。
    并排展示分歧，让人判断。
    """
    if not ext.ok:
        # off 是"按设计没问"，unavailable 是"问了但没拿到"——两者不能混成一个
        verdict = "skipped" if ext.status == "off" else "unavailable"
        return {"verdict": verdict, "local_passed": bool(local.get("passed")),
                "external_passed": None,
                "reason": (ext.note if ext.status == "off"
                           else f"外部复核不可用（{ext.status}）：{ext.summary or ext.note}")}

    local_crit = [i for i in (local.get("issues") or []) if i.get("level") == "critical"]
    local_pass = bool(local.get("passed"))
    ext_pass = ext.critical_n == 0

    # 外部独有：本地规则没命中的高危项——这是交叉校验真正的价值所在
    local_blob = " ".join(str(i.get("evidence") or "") for i in
                          (local.get("issues") or []))
    external_only = [i for i in ext.issues
                     if i.get("severity") == "critical"
                     and not _overlaps(str(i.get("evidence") or i.get("finding") or ""),
                                       local_blob)]
    # 本地独有：外部没提的必改项（多数是字符上限/结构类，外部不查这些）
    ext_blob = " ".join(str(i.get("evidence") or "") + " " + str(i.get("finding") or "")
                        for i in ext.issues)
    local_only = [i for i in local_crit
                  if not _overlaps(str(i.get("evidence") or ""), ext_blob)]

    verdict = "agree" if local_pass == ext_pass else "divergent"
    return {
        "verdict": verdict,
        "local_passed": local_pass,
        "external_passed": ext_pass,
        "local_score": local.get("score"),
        "external_score": ext.score,
        "local_critical_n": len(local_crit),
        "external_critical_n": ext.critical_n,
        "external_only_n": len(external_only),
        "local_only_n": len(local_only),
        "external_only": external_only[:5],
        "local_only": local_only[:5],
        "basis": ("两个引擎方法不同：本地为确定性规则（字符上限/结构/宣称词表，"
                  "可离线复算），外部为第三方语义分析（引用政策条款，有计费与可用性依赖）。"
                  "结论不一致时不取平均，以本地为准并列出分歧供人工判断。"),
    }
