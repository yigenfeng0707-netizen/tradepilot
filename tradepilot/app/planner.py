# Copyright 2026 TradePilot project. Apache-2.0.
"""自主规划器：候选打分 + 依赖图 + 状态驱动重试。

与旧版（固定序列 + LLM 确认）的本质区别：
- 每一步的下一步由**当前状态**决定，不是由写死的顺序决定；
- 所有硬依赖已满足的工具都是候选，规划器（LLM 或规则评分）在候选里**真实选择**；
- 失败/产出不足会触发**换参重试**（如换数据源重抓），而不是带着缺口往下走；
- 依赖失败的步骤被标为 skipped（降级但不中断），独立分支继续跑完闭环；
- 每个候选都带"为什么没选它"，决策卡可展示——自主性可审计，而不是口号。

评分 = 基础先验（越小越先）+ 目标相关加权 + 就绪加成 − 缺口惩罚。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class StepSpec:
    name: str
    purpose: str
    requires: tuple[str, ...] = ()          # 硬依赖：未完成即不可执行
    prefers: tuple[str, ...] = ()           # 软依赖：未完成扣分，但仍可被选择
    base: float = 50.0                      # 基础先验分（越小越先）
    max_attempts: int = 2                   # 含首次执行的尝试上限
    alt_source: str | None = None           # 重试时切换的数据源（仅抓取类工具）
    goal_hints: tuple[str, ...] = ()        # 目标里出现这些词则加权


@dataclass
class Candidate:
    name: str
    purpose: str
    score: float
    eligible: bool
    blocked_by: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attempt: int = 1

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "score": round(self.score, 1),
                "eligible": self.eligible, "blocked_by": self.blocked_by,
                "notes": self.notes, "attempt": self.attempt}


# 依赖图：pricing 需要真实价格带；listing 需要真实商品卡（软依赖定价/合规结论做事实面）；
# audit 需要已成稿的文案。fx 独立（CNY 数据折算时才必需，由状态动态判定）。
STEPS: tuple[StepSpec, ...] = (
    StepSpec("market_scan", "真实抓取目标品类的市场商品与价格（多源适配，全部带来源存证）",
             base=10, max_attempts=3, alt_source="walmart",
             goal_hints=("市场", "竞品", "选品", "调研", "竞争")),
    StepSpec("fx_rate", "现场真实汇率（Frankfurter/欧洲央行，存证后供定价折算）",
             base=20, goal_hints=("汇率", "定价", "价格")),
    StepSpec("pricing_sandbox", "对照真实价格带与成本参数计算报价下限与敏感性",
             requires=("market_scan",), prefers=("fx_rate",), base=30,
             goal_hints=("定价", "价格", "利润", "毛利", "成本")),
    StepSpec("compliance_check", "目标品类合规要求逐条对官方来源现场验证",
             base=40, goal_hints=("合规", "认证", "法规", "上架", "风险")),
    StepSpec("keyword_plan", "从真实竞品标题反推买家搜索词与后台 Search Terms",
             requires=("market_scan",), base=45,
             goal_hints=("关键词", "流量", "搜索", "listing", "上架", "文案")),
    StepSpec("listing_draft", "基于真实数据组织 Listing 文案与上架包",
             requires=("market_scan",),
             prefers=("keyword_plan", "pricing_sandbox", "compliance_check"),
             base=50, goal_hints=("listing", "文案", "标题", "上架", "卖点")),
    StepSpec("listing_audit", "上架文案合规审计（字符上限/违禁宣称/关键词堆砌）",
             requires=("listing_draft",), base=60,
             goal_hints=("合规", "上架", "listing", "审核")),
)

SPEC_BY_NAME: dict[str, StepSpec] = {s.name: s for s in STEPS}
MAX_STEPS = 14  # 硬上限：防止重试风暴


def _done(state: dict[str, Any], name: str) -> bool:
    return (state.get(name) or {}).get("status") == "done"


def _failed(state: dict[str, Any], name: str) -> bool:
    return name in state and (state.get(name) or {}).get("status") in ("error", "held")


def _attempts(attempts: dict[str, int], name: str) -> int:
    return attempts.get(name, 0)


def _goal_any_hit(goal: str) -> bool:
    low = (goal or "").lower()
    return any(h in low for spec in STEPS for h in spec.goal_hints)


def score_step(spec: StepSpec, state: dict[str, Any], attempts: dict[str, int],
               goal: str, goal_has_hit: bool | None = None) -> Candidate:
    """单个步骤的候选评分。返回的 Candidate 自带可解释理由（notes）。"""
    notes: list[str] = []
    blocked = [dep for dep in spec.requires if not _done(state, dep)]
    eligible = not blocked
    score = spec.base

    # 目标路由：任务目标点名的环节优先，未点名的让位（这才是"目标驱动"）
    low_goal = (goal or "").lower()
    hits = [h for h in spec.goal_hints if h in low_goal]
    if hits:
        score -= min(40.0, 20.0 * len(hits))
        notes.append(f"目标命中「{'/'.join(hits)}」优先")
    elif (goal_has_hit if goal_has_hit is not None else _goal_any_hit(goal)):
        score += 15
        notes.append("目标未点名本步，让位给点名的环节")

    # 软依赖未就绪：扣分（仍可选，但要让位给能补齐事实面的步骤）
    missing_soft = [dep for dep in spec.prefers if not _done(state, dep)]
    if missing_soft:
        score += 12 * len(missing_soft)
        notes.append(f"事实面待补：{'/'.join(missing_soft)}")

    # 依赖失败 → 该步骤降级为 skipped（不再反复重试，节省时间）
    dead_deps = [dep for dep in spec.requires if _failed(state, dep)]
    if dead_deps:
        blocked = dead_deps
        eligible = False
        notes.append(f"依赖 {','.join(dead_deps)} 失败，本步降级跳过")

    # 缺口驱动的重试：产出不足时提高优先级（真正的"自主纠偏"）
    if _failed(state, spec.name):
        if _attempts(attempts, spec.name) < spec.max_attempts:
            score -= 45
            notes.append(f"上次未达预期，触发第 {_attempts(attempts, spec.name) + 1} 次尝试")
            if spec.alt_source:
                notes.append(f"重试策略：切换数据源 → {spec.alt_source}")
        else:
            eligible = False
            blocked = [f"已达尝试上限 {spec.max_attempts}"]
            notes.append("重试次数用尽，本步放弃（依赖它的步骤将降级）")

    # 完成即出局（不再参与选择）
    if _done(state, spec.name):
        eligible = False
        blocked = blocked or ["已完成"]
        score += 1000

    return Candidate(spec.name, spec.purpose, score, eligible, blocked, notes,
                     attempt=_attempts(attempts, spec.name) + 1)


def rank(state: dict[str, Any], attempts: dict[str, int], goal: str) -> list[Candidate]:
    """全部候选按分排序（含不可执行的，便于决策卡展示"为什么不是它"）。"""
    goal_has_hit = _goal_any_hit(goal)
    cands = [score_step(s, state, attempts, goal, goal_has_hit) for s in STEPS]
    return sorted(cands, key=lambda c: (not c.eligible, c.score))


def pick(state: dict[str, Any], attempts: dict[str, int], goal: str) -> tuple[Candidate | None, list[Candidate]]:
    """规则规划：取评分最高的可执行候选。返回 (选中, 完整候选表)。"""
    ranked = rank(state, attempts, goal)
    chosen = next((c for c in ranked if c.eligible), None)
    return chosen, ranked


def build_whitelist(state: dict[str, Any], attempts: dict[str, int], goal: str) -> list[dict[str, Any]]:
    """给 LLM 的候选清单：只含可执行项，附带目的与当前尝试次数（供模型真实选择）。"""
    return [
        {"name": c.name, "purpose": c.purpose, "attempt": c.attempt, "notes": c.notes}
        for c in rank(state, attempts, goal) if c.eligible
    ]


def is_legal(name: str, state: dict[str, Any], attempts: dict[str, int], goal: str) -> bool:
    """LLM 的答案是否落在当前可执行候选内（白名单校验，防止模型瞎选）。"""
    return any(c.name == name and c.eligible for c in rank(state, attempts, goal))


def retry_input(name: str, spec: StepSpec, base_params: dict[str, Any],
                attempt: int) -> dict[str, Any]:
    """重试时的参数改写：抓取类工具换源，其余保持（幂等计算）。"""
    params = dict(base_params)
    if spec.alt_source and attempt > 1:
        params["source"] = spec.alt_source
    return params


def plan_explain(chosen: Candidate | None, ranked: list[Candidate]) -> str:
    """一句话解释"为什么选它、为什么不选别人"。"""
    if chosen is None:
        return "没有可执行的候选：依赖链已断或全部完成"
    others = [c for c in ranked if c.eligible and c.name != chosen.name][:2]
    if not others:
        return f"{chosen.name} 是唯一可执行候选"
    return (f"{chosen.name}（{chosen.score:.0f} 分）优先于 "
            + "、".join(f"{c.name}（{c.score:.0f} 分）" for c in others))


StepHook = Callable[[str, dict[str, Any]], None]
