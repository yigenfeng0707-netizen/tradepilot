# Copyright 2026 TradePilot project. Apache-2.0.
"""TradePilot 服务入口：FastAPI + WebSocket 遥测 + 静态大屏 + 任务存档/导出。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import llm, provenance, store
from .config import CONFIG
from .orchestrator import build_registry, run_mission
from .tools.fetch import shutdown_browser

WEB = Path(__file__).resolve().parents[1] / "web"
STATIC_WHITELIST = {"app.js", "style.css"}


@asynccontextmanager
async def lifespan(_: FastAPI):
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


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "tradepilot",
            "provenance": provenance.stats(),
            "data_dir": CONFIG.data_dir,
            "respect_robots": CONFIG.respect_robots,
            "llm": llm.available(),
            "llm_model": CONFIG.llm_model if llm.available() else None}


@app.get("/api/tools")
async def tools() -> dict[str, Any]:
    return {"tools": _registry.spec()}


@app.get("/api/missions")
async def missions(limit: int = 20) -> dict[str, Any]:
    return {"missions": store.list_missions(limit=max(1, min(limit, 100)))}


@app.get("/api/missions/{mission_id}")
async def mission_detail(mission_id: str) -> JSONResponse:
    report = store.get_mission(mission_id)
    if report is None:
        raise HTTPException(status_code=404, detail="任务不存在或 id 非法")
    return JSONResponse(report)


@app.get("/api/missions/{mission_id}/export")
async def mission_export(mission_id: str, format: str = "md") -> Any:
    """导出上架包：format=json（机器可读原始报告）/ md（可交付上架包）。"""
    report = store.get_mission(mission_id)
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
    raise HTTPException(status_code=400, detail="format 仅支持 json / md")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/static/{name}")
async def static_file(name: str) -> FileResponse:
    if name not in STATIC_WHITELIST:  # 白名单：不做目录拼接，杜绝路径穿越
        raise HTTPException(status_code=404, detail="not found")
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

    queue: asyncio.Queue = asyncio.Queue()

    async def runner() -> None:
        try:
            async for event in run_mission(req.goal, req.model_dump()):
                if event["type"] == "mission_done":
                    report = event["report"]
                    report["provenance"] = provenance.stats()
                    try:  # 落盘失败不影响任务结果，但要在事件里如实说明
                        report["archive_path"] = store.save_mission(report)
                    except Exception as exc:  # noqa: BLE001
                        report["archive_path"] = None
                        report["archive_error"] = f"{type(exc).__name__}: {exc}"
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
