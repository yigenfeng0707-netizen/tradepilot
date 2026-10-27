# Copyright 2026 TradePilot project. Apache-2.0.
"""统一工具执行器：所有工具的唯一入口（借鉴 commerce-agents 的单执行器蓝图）。

- 每个工具返回 ToolOutcome，永不抛出（异常降级为 error 结果，回合不中断）；
- 来源闸门：工具声明的 require_provenance 的 id 列表若不在存证库，返回 held——
  下游工具无法消费编造出来的数据点；
- 工具面白名单：orchestrator 只能点名 registry 里的工具，其余一律拒绝。
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .. import provenance

GATE_PROVENANCE = "provenance"


@dataclass
class ToolOutcome:
    ok: bool
    status: str                  # done / held / error
    gate: str | None
    summary: str                 # 给决策卡的一句话
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "gate": self.gate,
                "summary": self.summary, "data": self.data}


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[dict[str, Any]], Awaitable[ToolOutcome]]
    # 入参中哪些键是 provenance id 列表（闸门校验对象）
    provenance_keys: tuple[str, ...] = ()


def provenance_gate(name: str, tool: Tool, tool_input: dict[str, Any]) -> ToolOutcome | None:
    """校验入参里的数据点 id 是否真实在册；不在册即 held（不是 error——是闸门）。"""
    for key in tool.provenance_keys:
        raw = tool_input.get(key) or []
        ids = [int(i) for i in raw] if isinstance(raw, list) else [int(raw)]
        unknown = [i for i in ids if i not in provenance.exists(ids)]
        if unknown:
            return ToolOutcome(
                ok=False, status="held", gate=GATE_PROVENANCE,
                summary=(f"{name} 被来源闸门拦截：id {unknown[:5]} 不在本会话存证中，"
                         "必须先由数据采集工具真实抓取产生。"),
            )
    return None


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def spec(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    async def execute(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        """永不抛出：一切异常降级为 error 结果（对应 safety.md 的 failure ladder）。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(False, "error", None, f"未知工具 {name}（不在白名单）")
        if (held := provenance_gate(name, tool, tool_input)) is not None:
            return held
        try:
            return await tool.handler(tool_input)
        except Exception as exc:  # noqa: BLE001
            return ToolOutcome(
                False, "error", None,
                f"{name} 执行失败：{type(exc).__name__}: {exc}",
                {"trace": traceback.format_exc(limit=2)},
            )
