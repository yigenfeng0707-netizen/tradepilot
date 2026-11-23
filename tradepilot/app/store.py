# Copyright 2026 TradePilot project. Apache-2.0.
"""任务存档与上架包导出。

为什么必须有：赛事的"证据链归档"和答辩的"现场回放"都依赖任务可追溯。
只把报告推给前端、进程一重启就没了，等于没有证据。

存储形态：一条任务一个 JSON 文件（data_dir/missions/{mission_id}.json）。
选它而不是 SQLite 的理由：任务报告是**不可变的整体**，按 id 直读最快，
且天然可打包带走（评委要"给我原始记录"时直接给目录）。
云上注意：Cloud Run 的本地盘是临时的，跨实例不共享，见 docs/gcp-deploy.md 的持久化方案。

多租户（M3）：报告带 tenant_id（save_mission 写入），读取/list 按租户过滤；
tenant_id=None 表示单机/演示模式，全部可见。跨租户读取返回 None（同不存在，
不泄露存在性）。存证库（provenance）刻意**不**按租户隔离：它是数据信任层的
全局事实库（同一 URL 的真实抓取对所有租户都是事实），也是防编造闸门的查询面。
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from .config import CONFIG

# 历史格式 mission-YYYYMMDD-HHMMSS 仍然兼容；新落档必带 6 位随机后缀——
# 精度到秒时，同一秒并发起的两个任务会生成同名 id，后写者原子覆盖前者，
# 证据链凭空少一条（评测脚本批量打、现场演示连点两次都会触发）。
_SAFE_ID = re.compile(r"^mission-[0-9]{8}-[0-9]{6}(-[0-9a-f]{6})?$")


def new_mission_id() -> str:
    """生成任务 id：秒级时间戳 + 随机后缀防同秒覆盖。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"mission-{stamp}-{secrets.token_hex(3)}"


def save_mission(report: dict[str, Any], tenant_id: str | None = None) -> str:
    """落盘一份任务报告，返回文件路径。

    多租户（M3）：登录用户跑出的任务写入 tenant_id；未登录（单机/演示模式）
    为 None。归属不明的 legacy 存档在带租户查询时不可见——
    把旧演示数据冒充成某租户的任务，比看不到更糟。
    """
    mission_id = str(report.get("mission_id") or "")
    if not _SAFE_ID.match(mission_id):
        raise ValueError(f"非法 mission_id：{mission_id!r}")
    report["tenant_id"] = tenant_id
    path = os.path.join(CONFIG.missions_dir(), f"{mission_id}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)  # 原子替换，避免半截文件
    return path


def _visible(report: dict[str, Any], tenant_id: str | None) -> bool:
    """租户可见性判定。tenant_id=None 是单机模式，全部可见；
    带租户时只看自己的——无归属的 legacy 存档一概不冒充归属。"""
    if tenant_id is None:
        return True
    return report.get("tenant_id") == tenant_id


def list_missions(limit: int = 20, tenant_id: str | None = None) -> list[dict[str, Any]]:
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
        if not _visible(report, tenant_id):
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


def missions_by_day(days: int = 14) -> list[dict[str, Any]]:
    """近 N 天每日任务量（看板用）。按报告 started_at 的日期前缀聚合；
    没有任务的天补 0——图表上的平线必须是真的没任务，不是缺数据。
    注意：**只有本实例磁盘上的任务**（多实例部署时这不是全量口径，
    云上持久化后天然齐全）。"""
    from datetime import date, timedelta
    folder = CONFIG.missions_dir()
    today = date.today()
    counts: dict[str, int] = {}
    try:
        files = [f for f in os.listdir(folder) if f.endswith(".json")]
    except OSError:
        files = []
    for name in files[:2000]:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                started = str(json.load(fh).get("started_at") or "")
        except (OSError, json.JSONDecodeError):
            continue
        day = started[:10]          # ISO 日期前缀；解析失败的不算数，不猜
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            counts[day] = counts.get(day, 0) + 1
    return [{"day": (today - timedelta(days=days - 1 - i)).isoformat(),
             "missions": counts.get((today - timedelta(days=days - 1 - i)).isoformat(), 0)}
            for i in range(days)]


def get_mission(mission_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """读一份任务报告。跨租户访问与不存在返回同样的 None——
    404 不泄露"这份报告存在但不属于你"。"""
    if not _SAFE_ID.match(mission_id):
        return None
    path = os.path.join(CONFIG.missions_dir(), f"{mission_id}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return report if _visible(report, tenant_id) else None


def _external_off_line(note: str) -> str:
    """外部复核未启用时的说明行。

    `note` 有两种形态：新任务里是完整句子（"外部复核未启用（…）；本次结论…"），
    老存档或手工构造的只写一句原因（"模式为 off"）。两种都要读得通，
    且不能出现"未启用（未启用（…））"这种重复包裹。

    （`export_docx.py` 里有一份同样的实现——那个模块刻意只用标准库，
    不能为了一个字符串去依赖 app 包，所以宁可重复这 8 行。）
    """
    label = "外部复核未启用"
    note = (note or "").strip()
    if not note:
        return f"{label}，本节结论仅来自本地规则审计。"
    if note.startswith(label):
        return note
    return f"{label}（{note}）；本节结论仅来自本地规则审计。"


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
        marks = {"clause_matched": "✅ 已定位官方条款原文",
                 "page_only": "⚠ 已取得官方正文但未定位到条款原文",
                 "blocked": "⛔ HTTP 200 但内容不可核验（拦截页/SPA 空壳/软 404）",
                 "offline": "⚠ 网络不可达（引用标注）"}
        for c in checks:
            mark = marks.get(str(c.get("verification")), "⚠️ 未验证")
            page = c.get("page_url") or c.get("source_url")
            extra = f"（{c.get('block_reason')}）" if c.get("block_reason") else ""
            lines.append(f"- {mark} [{c.get('kind')}] {c.get('item')} — {c.get('authority')}"
                         f"（{page}）{extra}")
            if c.get("excerpt"):
                lines.append(f"  - 条款原文片段：「{str(c['excerpt'])[:200]}」"
                             f"（命中词 {'/'.join(c.get('matched_terms') or [])} · "
                             f"存证#{c.get('provenance_id')}）")
        lines += ["", f"> {compliance.get('basis', '')}"]
    else:
        lines.append("（未生成合规清单）")

    lines += ["", "## 四、关键词层", ""]
    kplan = (results.get("keyword_plan") or {}).get("data") or {}
    if kplan:
        buckets = kplan.get("buckets") or {}
        for label, key in (("核心", "core"), ("属性", "attribute"), ("场景", "scenario"),
                           ("人群", "audience"), ("相关品类", "related")):
            items_ = buckets.get(key) or []
            if items_:
                terms = "、".join(
                    f"{(e.get('english') or [''])[0] or e.get('observed')}（{e.get('count')} 次）"
                    for e in items_[:8])
                lines.append(f"- **{label}**：{terms}")
        proxy = kplan.get("competition_proxy") or {}
        lines += [
            "",
            f"样本 {proxy.get('sample_size')} 条真实竞品标题，核心词覆盖率 "
            f"{round((proxy.get('core_term_coverage') or 0) * 100)}%，"
            f"标题均长 {proxy.get('avg_title_len')} 字。",
            f"**后台 Search Terms**（{kplan.get('search_terms_bytes')}/250 字节）："
            f"`{kplan.get('search_terms')}`",
            "",
            "> 词表来自真实竞品标题的观测计数（附证据可回访），"
            "不含搜索量/竞争度评分——未接入相应数据源，编造该指标等于假数据。",
        ]
        evidence = kplan.get("search_term_evidence") or {}
        if evidence.get("data_source") == "ads_search_term_report":
            lines.append(
                f"> 曝光/点击来自广告搜索词报告（周期 {evidence.get('period') or '未标注'}，"
                f"命中 {evidence.get('matched_terms')} 词）；报告无搜索量字段，故仍不提供。")
        elif evidence:
            lines.append(f"> 数据源状态：{evidence.get('data_source')}——{evidence.get('note', '')}")
    else:
        lines.append("（未执行关键词分析）")

    lines += ["", "## 五、Listing 文案", ""]
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

    lines += ["", "## 六、文案合规审计", ""]
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

    # 外部复核：默认未启用（off），启用后作为第二意见与本地结论并排展示。
    # 刻意不合并成"综合分"——两个引擎方法不同，平均出来的数没有意义。
    ext = audit.get("external_review") or {}
    cc = audit.get("cross_check") or {}
    if ext.get("status") == "ok":
        lines += ["", "### 外部复核（第三方 ListingGood，第二意见，不计入上方评分）", "",
                  f"- 结论：{ext.get('score')} 分 / {ext.get('level')}"
                  f"（{ext.get('issue_n')} 项问题，其中高危 {ext.get('critical_n')} 项）"
                  f"　接口：{ext.get('endpoint')}　耗时：{ext.get('latency_ms')}ms",
                  f"- 交叉校验：**{cc.get('verdict')}**"
                  f"（本地{'通过' if cc.get('local_passed') else '未通过'} / "
                  f"外部{'通过' if cc.get('external_passed') else '未通过'}）"]
        for i in (ext.get("issues") or [])[:5]:
            lines.append(f"- [{i.get('severity')}] {i.get('finding')}"
                         f" ← {i.get('evidence')}（依据：{i.get('source')}）")
        if cc.get("external_only_n"):
            lines += ["", f"> 外部独有 {cc.get('external_only_n')} 项：本地规则未命中，"
                          "需人工判断是真实风险还是对方误报。"]
        if ext.get("note"):
            lines += ["", f"> {ext.get('note')}"]
    elif ext.get("status") == "off":
        lines += ["", f"> {_external_off_line(ext.get('note'))}"]
    elif ext.get("status"):
        lines += ["", f"> 外部复核未取得结果（{ext.get('status')}："
                      f"{ext.get('summary', '')}）。本地审计结论不受影响。"]

    lines += ["", "## 七、决策实录", ""]
    for d in report.get("decisions") or []:
        lines.append(f"{d.get('n')}. `/{d.get('tool')}`（第 {d.get('attempt')} 次）"
                     f" → {d.get('status')}：{d.get('summary')}")
        lines.append(f"   - 理由：{d.get('reason')}")
    lines += ["", "---", "", "数据来源与抓取时间戳见来源存证库（provenance）；"
                            "本文件由 TradePilot 自动生成，数字均来自真实抓取与确定性计算。"]
    return "\n".join(lines)
