# -*- coding: utf-8 -*-
"""人工维度的两个入口：掐表（人工基线）与回访（事实抽检）。

为什么要有这个脚本：方案 §四 的两个维度只能由真人产出，但手工改 JSON + 手算
成本折算的代价高到没人愿意做，结果就是 `human_baseline` 与
`fact_spot_check_accuracy` 长期为 null——红线写在纸上，账上却是空的。
本脚本把这两件事压成终端里的一条命令，且**不替人做任何判断**。

四条不可绕过的规矩：
1. **只记录真人此刻输入的东西**：没有默认值、没有"经验值"、没有自动填充；
2. **跳过 ≠ 通过**：跳过的条目保持 null，准确率恒为 null，直到全量回访完成；
3. **中断不留半截假数据**：每阶段/每条结束才落盘，Ctrl+C 不会产生半个基线；
4. **必须真人在终端交互**：非 TTY 直接拒绝，防止 CI 或脚本代跑出"人工数据"。

用法：
    python scripts/human_baseline.py status
    python scripts/human_baseline.py timing --name 张三 --hourly-cny 80
    python scripts/human_baseline.py spotcheck --limit 10
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import eval_metrics  # noqa: E402

EVAL_OUT = ROOT / "assets" / "eval_results.json"
SAMPLE_OUT = ROOT / "assets" / "audit_sample.json"

# 与 docs/eval/人工SOP.md 的阶段一一对应；改这里必须同步改 SOP，否则两份口径会漂
STAGES = ("市场扫描", "汇率折算", "成本定价", "合规核查", "Listing 文案")


# ---------------------------------------------------------------- 纯函数层
# 下面几个函数不碰终端、不碰文件，便于单测复算；脚本只负责取数与落盘。

def human_baseline_record(stage_seconds: list[float], performed_by: str,
                          data_points: int | None = None,
                          sources_cited_pct: float | None = None) -> dict:
    """把分阶段秒数整理成 human_baseline 结构。

    合计**不取各阶段最大值，也不四舍五入到整数分钟**：方案里"自动化率"是用
    合计分钟算的，少一分钟就会把结果往有利方向偏。
    """
    if len(stage_seconds) != len(STAGES):
        raise ValueError(f"阶段数不符：需要 {len(STAGES)} 段，收到 {len(stage_seconds)} 段")
    if any(s < 0 for s in stage_seconds):
        raise ValueError("阶段用时不得为负")
    total = float(sum(stage_seconds))
    if total <= 0:
        raise ValueError("合计用时为 0——未真正计时，拒绝写入")
    return {
        "_说明": "由真人按 docs/eval/人工SOP.md 执行并计时后填写，禁估算",
        "task_time_min": round(total / 60.0, 1),
        "task_time_s": round(total, 1),
        "stages_min": {name: round(s / 60.0, 1)
                       for name, s in zip(STAGES, stage_seconds)},
        "data_points_collected": data_points,
        "sources_cited": sources_cited_pct,
        "performed_by": performed_by,
        "performed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timing_method": "脚本秒表（time.monotonic，阶段间回车记时）",
    }


def apply_spot_check(sheet: list[dict]) -> dict:
    """回填后的抽检表 → 准确率（复用 eval_metrics 的口径，不另立标准）。"""
    return eval_metrics.spot_check_accuracy(sheet)


def agent_elapsed_per_run(runs: list[dict]) -> float | None:
    """单次任务平均耗时（口径定义在 eval_metrics，跑批与回填共用一份）。"""
    return eval_metrics.per_run_mean_elapsed(runs)


def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data) -> None:
    """原子写：先写临时文件再替换，中途断电不会留下半个 JSON。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _require_tty() -> None:
    if not sys.stdin.isatty():
        print("✗ 本脚本必须由真人在终端交互执行（检测到非 TTY 环境）。\n"
              "  人工数据不能由脚本代跑——这是红线，不是限制。", file=sys.stderr)
        raise SystemExit(2)


def _ask(prompt: str) -> str:
    """读一行；EOF / Ctrl+C 一律干净退出，绝不在半途写入任何数据。

    为什么单独包一层：某些终端把 stdin 重定向后 `isatty()` 仍报真，
    只靠 TTY 检查挡不住"脚本代跑"，必须让读不到输入这件事本身也安全终止。
    """
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n✗ 已中止，未写入任何数据（半截基线比没有基线更糟）。", file=sys.stderr)
        raise SystemExit(2)


def _opt_int(raw: str) -> int | None:
    """可留空的整数：留空或写错都回 None（不猜、也不崩）。"""
    try:
        return int(raw) if raw.strip() else None
    except ValueError:
        print(f"  ! 无法解析「{raw}」，按留空处理（null）")
        return None


def _opt_float(raw: str) -> float | None:
    try:
        return float(raw.rstrip("%")) if raw.strip() else None
    except ValueError:
        print(f"  ! 无法解析「{raw}」，按留空处理（null）")
        return None


# ---------------------------------------------------------------- 掐表
def cmd_timing(args) -> None:
    _require_tty()
    if not args.name.strip():
        print("✗ 需要 --name（执行人姓名），人工基线必须可追溯到具体的人。", file=sys.stderr)
        raise SystemExit(2)

    print("=" * 64)
    print("人工基线掐表 · 任务与 Agent 同题：宠物用品（dog bed）美国站上架前期调研")
    print("流程见 docs/eval/人工SOP.md。现在开始计时，回车进入下一阶段。")
    print("=" * 64)
    _ask("\n准备好后按回车【开始计时】…")

    stage_seconds: list[float] = []
    t_prev = time.monotonic()
    for idx, name in enumerate(STAGES, 1):
        print(f"\n阶段 {idx}/{len(STAGES)}：{name}（正在计时…）")
        _ask("  做完后按回车【结束本阶段】…")
        t_now = time.monotonic()
        lap = t_now - t_prev
        stage_seconds.append(lap)
        t_prev = t_now
        print(f"  ✓ 用时 {lap / 60.0:.1f} 分钟")

    total = sum(stage_seconds)
    print("\n" + "-" * 64)
    for name, s in zip(STAGES, stage_seconds):
        print(f"  {name:<12} {s / 60.0:>6.1f} 分钟")
    print(f"  {'合计':<12} {total / 60.0:>6.1f} 分钟")
    print("-" * 64)

    # 这两项 SOP 也要求记录，允许留空（留空就是 null，不猜）
    dp_raw = _ask("数据点采集数（价格+汇率+合规条目，可留空）：").strip()
    src_raw = _ask("带来源网址记录的比例 %（可留空）：").strip()

    try:
        record = human_baseline_record(
            stage_seconds, args.name.strip(),
            data_points=_opt_int(dp_raw), sources_cited_pct=_opt_float(src_raw),
        )
    except ValueError as exc:
        print(f"✗ 拒绝写入：{exc}", file=sys.stderr)
        raise SystemExit(1)

    if _ask(f"\n写入 assets/eval_results.json？合计 {record['task_time_min']} 分钟 [y/N] ").lower() != "y":
        print("已放弃，未写入任何数据。")
        return

    data = _load(EVAL_OUT, {})
    data["human_baseline"] = record
    _recompute_labor(data, args.hourly_cny)
    _save(EVAL_OUT, data)
    print(f"✓ 已写入 human_baseline（{record['performed_by']}，{record['task_time_min']} 分钟）")
    _print_labor(data)


def _recompute_labor(data: dict, hourly_cny: float | None) -> None:
    """人工成本折算：只有真人基线在场时才出数，且 agent 侧取单次均值。"""
    per_run = agent_elapsed_per_run(data.get("runs") or [])
    if per_run is None:
        return
    rate = hourly_cny if hourly_cny else (data.get("labor_cost_equiv") or {}).get("hourly_cny") or 0
    data["labor_cost_equiv"] = eval_metrics.labor_cost_equiv(
        data.get("human_baseline"), per_run, rate)
    data["labor_cost_equiv"]["agent_elapsed_basis"] = "per_run_mean_s（单次任务均值）"
    data["labor_cost_equiv"]["agent_elapsed_s"] = per_run


def _print_labor(data: dict) -> None:
    labor = data.get("labor_cost_equiv") or {}
    if labor.get("automation_rate") is None:
        print(f"  成本折算仍为 null：{labor.get('reason') or '未提供时薪（--hourly-cny）或缺少 agent 耗时数据'}")
        return
    print(f"  人工成本折算 ¥{labor['labor_cost_equiv_cny']}（时薪 ¥{labor['hourly_cny']}，"
          f"人工 {labor['human_minutes']} 分钟 / Agent {labor['agent_elapsed_s']} 秒）"
          f" → 自动化率 {labor['automation_rate'] * 100:.1f}%")


# ---------------------------------------------------------------- 抽检回访
def cmd_spotcheck(args) -> None:
    _require_tty()
    sheet = _load(SAMPLE_OUT, [])
    if not sheet:
        print("✗ assets/audit_sample.json 为空，先跑 scripts/run_eval.py 生成抽检表。",
              file=sys.stderr)
        raise SystemExit(2)

    pending = [r for r in sheet if r.get("human_verified") is None]
    todo = pending[:args.limit] if args.limit else pending
    print(f"抽检表 {len(sheet)} 条，已回填 {len(sheet) - len(pending)} 条，"
          f"本轮待回访 {len(todo)} 条。")
    print("判定标准：打开 url，看页面内容是否支持 payload 里这条事实。\n")

    answered = 0
    for i, row in enumerate(todo, 1):
        print("-" * 64)
        print(f"[{i}/{len(todo)}] #{row['provenance_id']} · {row['kind']} · {row['fetched_at']}")
        print(f"  URL: {row['url']}")
        print(f"  事实: {json.dumps(row.get('payload'), ensure_ascii=False)[:280]}")
        while True:
            ans = _ask("  与页面一致？[y]是 [n]否 [s]跳过 [q]退出并保存 > ").strip().lower()
            if ans in ("y", "n", "s", "q"):
                break
            print("  请输入 y / n / s / q")
        if ans == "q":
            break
        if ans == "s":
            continue
        row["human_verified"] = (ans == "y")
        if ans == "n":
            row["human_note"] = _ask("  不一致说明（可留空）：").strip()
        answered += 1
        _save(SAMPLE_OUT, sheet)          # 每条即存：中断不丢已回访的进度

    _save(SAMPLE_OUT, sheet)
    accuracy = apply_spot_check(sheet)
    data = _load(EVAL_OUT, {})
    data["fact_spot_check_accuracy"] = accuracy
    _save(EVAL_OUT, data)

    print("-" * 64)
    print(f"本轮回访 {answered} 条。")
    if accuracy["accuracy"] is None:
        print(f"  准确率仍为 null：{accuracy['reason']}")
    else:
        print(f"  ✓ 事实抽检准确率 {accuracy['accuracy'] * 100:.1f}%"
              f"（{accuracy['checked']}/{accuracy['total']} 全量回访）")


# ---------------------------------------------------------------- 状态
def cmd_status(_args) -> None:
    data = _load(EVAL_OUT, {})
    hb = data.get("human_baseline") or {}
    sheet = _load(SAMPLE_OUT, [])
    filled = sum(1 for r in sheet if r.get("human_verified") is not None)

    print("人工维度进度（这两项脚本不能代填）")
    if hb.get("task_time_min"):
        print(f"  人工基线    : 已填 {hb['task_time_min']} 分钟 / {hb.get('performed_by')}"
              f" @ {hb.get('performed_at')}")
    else:
        print("  人工基线    : null —— 待真人掐表（scripts/human_baseline.py timing）")
    acc = (data.get("fact_spot_check_accuracy") or {}).get("accuracy")
    print(f"  事实抽检    : {filled}/{len(sheet)} 条已回访"
          f"（准确率 {acc if acc is not None else 'null'}）")
    labor = data.get("labor_cost_equiv") or {}
    if labor.get("labor_cost_equiv_cny") is None:
        print("  成本折算    : null —— 人工基线填入后自动重算")
    else:
        print(f"  成本折算    : ¥{labor['labor_cost_equiv_cny']}"
              f"（人工 {labor['human_minutes']} 分钟 vs Agent {labor['agent_elapsed_s']} 秒"
              f"，自动化率 {labor['automation_rate'] * 100:.1f}%）")
    print(f"  agent 单次均值: {agent_elapsed_per_run(data.get('runs') or [])} 秒")


def main() -> None:
    ap = argparse.ArgumentParser(description="人工基线掐表 / 事实抽检回访")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_t = sub.add_parser("timing", help="交互式掐表，写入 human_baseline")
    p_t.add_argument("--name", required=True, help="执行人姓名（必须可追溯）")
    p_t.add_argument("--hourly-cny", type=float, default=None,
                     help="人工时薪（元），用于成本折算；不填则该项保持 null")
    p_t.set_defaults(func=cmd_timing)

    p_s = sub.add_parser("spotcheck", help="逐条回访抽检表，写入准确率")
    p_s.add_argument("--limit", type=int, default=0, help="本轮最多回访几条（0=全部）")
    p_s.set_defaults(func=cmd_spotcheck)

    p_st = sub.add_parser("status", help="查看两项人工维度的完成进度")
    p_st.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
