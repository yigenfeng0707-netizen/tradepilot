# Copyright 2026 TradePilot project. Apache-2.0.
"""任务存档与上架包导出。

为什么必须有：赛事的"证据链归档"和答辩的"现场回放"都依赖任务可追溯。
只把报告推给前端、进程一重启就没了，等于没有证据。

存储形态：一条任务一个 JSON 文件（data_dir/missions/{mission_id}.json）。
选它而不是 SQLite 的理由：任务报告是**不可变的整体**，按 id 直读最快，
且天然可打包带走（评委要"给我原始记录"时直接给目录）。
云上注意：Cloud Run 的本地盘是临时的，跨实例不共享，见 docs/gcp-deploy.md 的持久化方案。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .config import CONFIG

_SAFE_ID = re.compile(r"^mission-[0-9]{8}-[0-9]{6}$")


def save_mission(report: dict[str, Any]) -> str:
    """落盘一份任务报告，返回文件路径。"""
    mission_id = str(report.get("mission_id") or "")
    if not _SAFE_ID.match(mission_id):
        raise ValueError(f"非法 mission_id：{mission_id!r}")
    path = os.path.join(CONFIG.missions_dir(), f"{mission_id}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)  # 原子替换，避免半截文件
    return path


def list_missions(limit: int = 20) -> list[dict[str, Any]]:
    """按时间倒序列出任务摘要（不含完整 results，避免列表接口过大）。"""
    folder = CONFIG.missions_dir()
    files = [f for f in os.listdir(folder) if f.endswith(".json")]
    files.sort(reverse=True)  # mission id 含时间戳，字典序即时间序
    out: list[dict[str, Any]] = []
    for name in files[:limit]:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        results = report.get("results") or {}
        audit = (results.get("listing_audit") or {}).get("data") or {}
        out.append({
            "mission_id": report.get("mission_id"),
            "goal": report.get("goal"),
            "started_at": report.get("started_at"),
            "elapsed_s": report.get("elapsed_s"),
            "plan_engine": report.get("plan_engine"),
            "steps": len(report.get("decisions") or []),
            "autonomy": report.get("autonomy"),
            "audit_score": audit.get("score"),
            "audit_grade": audit.get("grade"),
        })
    return out


def get_mission(mission_id: str) -> dict[str, Any] | None:
    if not _SAFE_ID.match(mission_id):
        return None
    path = os.path.join(CONFIG.missions_dir(), f"{mission_id}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def export_markdown(report: dict[str, Any]) -> str:
    """把任务报告渲染成可交付的上架包（Markdown），供导出下载与归档。"""
    results = report.get("results") or {}
    scan = (results.get("market_scan") or {}).get("data") or {}
    pricing = (results.get("pricing_sandbox") or {}).get("data") or {}
    compliance = (results.get("compliance_check") or {}).get("data") or {}
    listing = (results.get("listing_draft") or {}).get("data") or {}
    audit = (results.get("listing_audit") or {}).get("data") or {}
    copy = listing.get("copy") or {}
    autonomy = report.get("autonomy") or {}

    lines: list[str] = [
        f"# 上架包 · {report.get('mission_id', '')}",
        "",
        f"- 目标：{report.get('goal', '')}",
        f"- 开始时间：{report.get('started_at', '')}　总耗时：{report.get('elapsed_s', '')}s",
        f"- 规划引擎：{report.get('plan_engine', '')}"
        f"（LLM 决策 {autonomy.get('llm_decisions', 0)} 次 / 覆盖规则序 "
        f"{autonomy.get('llm_overrides', 0)} 次 / 重试 {autonomy.get('retries', 0)} 次）",
        f"- 降级步骤：{', '.join(autonomy.get('steps_degraded') or []) or '无'}",
        "",
        "## 一、真实市场数据（每条带来源）",
        "",
    ]
    items = scan.get("items") or []
    if items:
        lines += ["| # | 商品 | 价格 | 来源 |", "|---|---|---|---|"]
        for i, item in enumerate(items, 1):
            lines.append(
                f"| {i} | {str(item.get('title', ''))[:60]} | "
                f"{item.get('currency', '')} {item.get('price', '')} | "
                f"存证#{item.get('provenance_id', '')} |")
        band = scan.get("price_band") or {}
        lines += ["", f"价格带：{band.get('min')} / {band.get('median')} / {band.get('max')}"
                      f" {scan.get('currency', '')}，来源：{scan.get('source_url', '')}"]
    else:
        lines.append("（本次任务未取得真实商品数据——如实留空，不填充估算值）")

    lines += ["", "## 二、定价沙盘", ""]
    if pricing:
        cb = pricing.get("cost_breakdown") or {}
        lines += [
            f"- 现场汇率：USD/CNY = {pricing.get('fx_rate')}（存证#{pricing.get('fx_provenance_id')}）",
            f"- 采购成本：¥{pricing.get('cost_basis_cny')} ≈ ${pricing.get('unit_cost_usd')}"
            f"（{pricing.get('cost_basis_note')}）",
            f"- 到岸成本：${pricing.get('landed_cost')}"
            f"（采购 ${cb.get('unit_cost_usd')} + 头程 ${cb.get('freight_usd')}"
            f" + 关税 ${cb.get('duty_usd')} + 尾程 ${cb.get('last_mile_usd')}）",
            f"- 报价下限：**${pricing.get('floor_price')}**",
            f"- 对照口径：{pricing.get('band_source')}",
            f"- 建议：{(pricing.get('suggestion') or {}).get('positioning')}",
        ]
    else:
        lines.append("（未生成定价结论）")

    lines += ["", "## 三、合规体检", ""]
    checks = compliance.get("checks") or []
    if checks:
        for c in checks:
            mark = "✅ 现场验证" if c.get("live") else "⚠️ 引用标注（来源不可达）"
            lines.append(f"- {mark} [{c.get('kind')}] {c.get('item')} — {c.get('authority')}"
                         f"（{c.get('source_url')}）")
    else:
        lines.append("（未生成合规清单）")

    lines += ["", "## 四、Listing 文案", ""]
    if copy:
        lines += [f"**Title**（{len(str(copy.get('title', '')))} 字符）", "",
                  str(copy.get("title", "")), "", "**Bullets**", ""]
        for b in copy.get("bullets") or []:
            lines.append(f"- {b}")
        if copy.get("description"):
            lines += ["", "**Description**", "", str(copy.get("description"))]
        lines += ["", f"生成方式：{listing.get('generated_by')}"]
    else:
        lines.append("（未生成文案）")

    lines += ["", "## 五、文案合规审计", ""]
    if audit:
        counts = audit.get("counts") or {}
        lines += [
            f"**{audit.get('score')} 分 / {audit.get('grade')} 级 — {audit.get('verdict')}**"
            f"（必改 {counts.get('critical', 0)} / 建议 {counts.get('warning', 0)}"
            f" / 提示 {counts.get('info', 0)}）", "",
        ]
        for issue in audit.get("issues") or []:
            lines.append(f"- [{issue.get('level')}] {issue.get('rule')}"
                         f"（{issue.get('field')}）：{issue.get('evidence')}"
                         f" → 修复：{issue.get('fix')}")
        lines += ["", f"> {audit.get('basis', '')}"]
    else:
        lines.append("（未执行审计）")

    lines += ["", "## 六、决策实录", ""]
    for d in report.get("decisions") or []:
        lines.append(f"{d.get('n')}. `/{d.get('tool')}`（第 {d.get('attempt')} 次）"
                     f" → {d.get('status')}：{d.get('summary')}")
        lines.append(f"   - 理由：{d.get('reason')}")
    lines += ["", "---", "", "数据来源与抓取时间戳见来源存证库（provenance）；"
                            "本文件由 TradePilot 自动生成，数字均来自真实抓取与确定性计算。"]
    return "\n".join(lines)
