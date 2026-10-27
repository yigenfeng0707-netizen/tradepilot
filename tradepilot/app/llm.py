# Copyright 2026 TradePilot project. Apache-2.0.
"""LLM 网关（可选增强层）：智谱 GLM OpenAI 兼容接口。

设计原则：
- 无 API Key 时全部优雅降级——编排器走规则规划、Listing 走模板，作品依然完整可跑；
- LLM 输出**只做组织与措辞**，一切事实字段（价格/条款）来自存证回填，prompt 里
  不给模型编造数字的机会（对应 commerce-agents 的 grounding 思想）。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .config import CONFIG


def available() -> bool:
    return bool(CONFIG.llm_api_key)


async def chat(system: str, user: str, max_tokens: int = 800) -> str | None:
    """返回模型文本；不可用/失败一律返回 None（调用方自行降级）。"""
    if not available():
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{CONFIG.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {CONFIG.llm_api_key}"},
                json={
                    "model": CONFIG.llm_model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
            )
            resp.raise_for_status()
            return json.loads(resp.text)["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 — 网关故障一律降级，不让作品失败
        return None


async def plan_next(goal: str, done_steps: list[str], tools: list[dict[str, str]]) -> dict[str, Any] | None:
    """编排规划：给定目标与已完成步骤，产出 {next_tool, reason}。失败返回 None 走规则。"""
    raw = await chat(
        "你是智能体编排规划器。只能从给出的工具白名单里选择下一步，输出严格 JSON："
        '{"next_tool":"工具名","reason":"一句话理由"}，不要输出任何其他文字。',
        f"目标：{goal}\n已完成：{done_steps or '（无，这是第一步）'}\n"
        f"工具白名单：{json.dumps(tools, ensure_ascii=False)}",
        max_tokens=200,
    )
    if raw is None:
        return None
    try:
        plan = json.loads(raw.strip().strip("`").lstrip("json").strip())
        if plan.get("next_tool") in {t["name"] for t in tools}:
            return plan
    except Exception:  # noqa: BLE001
        pass
    return None


async def write_listing(goal: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    """Listing 文案：只允许基于 facts 组织英文文案；失败返回 None 走模板。"""
    raw = await chat(
        "你是跨境电商 Listing 文案师。只允许使用用户给出的真实数据（价格带、竞品卖点、"
        "合规要点），不得编造任何数字与认证。输出严格 JSON："
        '{"title":"<=200 chars","bullets":["..."],"description":"..."}。',
        f"目标：{goal}\n真实数据：{json.dumps(facts, ensure_ascii=False)}",
        max_tokens=700,
    )
    if raw is None:
        return None
    try:
        parsed = json.loads(raw.strip().strip("`").lstrip("json").strip())
        if parsed.get("title") and parsed.get("bullets"):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    return None
