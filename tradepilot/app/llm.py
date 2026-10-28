# Copyright 2026 TradePilot project. Apache-2.0.
"""LLM 网关（可选增强层）：书生 InternAI 聚合网关（OpenAI 兼容，多模型）。

设计原则：
- 无 API Key 时全部优雅降级——编排器走规则规划、Listing 走模板，作品依然完整可跑；
- LLM 输出**只做组织与措辞**，一切事实字段（价格/条款）来自存证回填，prompt 里
  不给模型编造数字的机会（对应 commerce-agents 的 grounding 思想）；
- 推理模型（Atria-Dawn 等）会把 token 预算先花在 reasoning 上：content 为空而
  reasoning_content 有货时，从 reasoning 尾段提取 JSON 兜底。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .config import CONFIG


def available() -> bool:
    return bool(CONFIG.llm_api_key)


def _extract_json(text: str) -> dict[str, Any] | None:
    """宽容 JSON 提取：剥围栏、取首个平衡对象。"""
    text = (text or "").strip().strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:i + 1])
                        return parsed if isinstance(parsed, dict) else None
                    except Exception:  # noqa: BLE001
                        return None
        return None


async def chat(system: str, user: str, max_tokens: int = 800) -> str | None:
    """返回模型正文；不可用/失败一律返回 None（调用方自行降级）。"""
    if not available():
        return None
    try:
        async with httpx.AsyncClient(timeout=150) as client:
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
            msg = json.loads(resp.text)["choices"][0]["message"]
            content = str(msg.get("content") or "").strip()
            if content:
                return content
            reasoning = str(msg.get("reasoning_content") or "")
            if reasoning and "{" in reasoning:
                return reasoning[reasoning.rfind("{"):]  # 尾段起点，交由调用方 _extract_json 兜底
            return None
    except Exception:  # noqa: BLE001 — 网关故障一律降级，不让作品失败
        return None


async def plan_next(goal: str, done_steps: list[str], tools: list[dict[str, str]]) -> dict[str, Any] | None:
    """编排规划：给定目标与已完成步骤，产出 {next_tool, reason}。失败返回 None 走规则。"""
    raw = await chat(
        "你是智能体编排规划器。只能从给出的工具白名单里选择下一步，输出严格 JSON："
        '{"next_tool":"工具名","reason":"一句话理由"}，不要输出任何其他文字。',
        f"目标：{goal}\n已完成：{done_steps or '（无，这是第一步）'}\n"
        f"工具白名单：{json.dumps(tools, ensure_ascii=False)}",
        max_tokens=800,
    )
    if raw is None:
        return None
    plan = _extract_json(raw)
    if plan and plan.get("next_tool") in {t["name"] for t in tools}:
        return plan
    return None


async def write_listing(goal: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    """Listing 文案：品类级文案（非具体 SKU），数字只准来自 facts；失败返回 None 走模板。"""
    raw = await chat(
        "你是跨境电商 Listing 文案师，任务是写品类级文案（不是某个具体 SKU 的规格页）。"
        "facts 里是经过验证的真实市场数据（价格带、定位结论、合规要点）。规则："
        "1) 一切数字与认证名称只能来自 facts，一个都不许编造；"
        "2) 品类通用卖点（如材质工艺的一般性描述）可以写，但不得出现具体未提供的参数"
        "（尺寸、承重、面料成分等一律不写）；"
        "3) 输出严格 JSON：{\"title\":\"<=200 chars\",\"bullets\":[\"...\"],\"description\":\"...\"}，"
        "不要输出任何其他文字。",
        f"目标：{goal}\n事实数据：{json.dumps(facts, ensure_ascii=False)}",
        max_tokens=2000,
    )
    if raw is None:
        return None
    parsed = _extract_json(raw)
    if parsed and parsed.get("title") and parsed.get("bullets"):
        return parsed
    return None
