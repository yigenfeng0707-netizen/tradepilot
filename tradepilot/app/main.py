# Copyright 2026 TradePilot project. Apache-2.0.
"""TradePilot 服务入口：FastAPI + WebSocket 遥测 + 静态大屏。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import provenance
from .orchestrator import build_registry, run_mission

WEB = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="TradePilot", version="0.1.0")
_registry = build_registry()


class MissionIn(BaseModel):
    goal: str
    keyword: str                     # 品类关键词（市场扫描用）
    source: str = "dangdang"         # 数据源：dangdang（CNY）/ walmart（USD，部署态）
    category: str                    # 合规知识卡品类：pet/home/electronics
    unit_cost_cny: float | None = None   # 采购价（缺省取扫描中位数）
    freight_usd: float = 2.5
    duty_pct: float = 7.0
    last_mile_usd: float = 5.5
    commission_pct: float = 15.0
    target_margin_pct: float = 20.0


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "tradepilot", "provenance": provenance.stats()}


@app.get("/api/tools")
async def tools() -> dict[str, Any]:
    return {"tools": _registry.spec()}


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/static/{name}")
async def static_file(name: str) -> FileResponse:
    return FileResponse(WEB / name)


@app.websocket("/ws/mission")
async def mission_ws(ws: WebSocket) -> None:
    """WebSocket 任务通道：收一条任务 JSON，流式回推决策卡与最终报告。"""
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

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    report_holder: dict[str, Any] = {}

    async def runner() -> None:
        try:
            async for event in run_mission(req.goal, req.model_dump()):
                if event["type"] == "mission_done":
                    event["report"]["provenance"] = provenance.stats()
                    report_holder["report"] = event["report"]
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
