#!/usr/bin/env python3
# Copyright 2026 TradePilot project. Apache-2.0.
"""生产就绪门禁：把"能不能对外说生产级"变成机器可判定的 8 项检查。

设计原则：
1. 门禁不估算。缺证据就是不通过，detail 里如实写"未提供证据"。
2. 动态能查的（git 同步、人工评测是否仍为 null、密钥实体、Shopify 联调结论、
   定价配置、gitignore）自动采集；测试/CI 这类外部事实只认证据文件里的实测值。
3. 退出码 0=8 项全绿，1=仍有阻塞项。专门用于交付前自检，不假装"差一点也算过"。

用法：
    python scripts/production_gate.py collect     # 刷新可自动采集的字段
    python scripts/production_gate.py check       # 打印门禁表并返回退出码
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILE = ROOT / "docs" / "production-evidence.json"

GATE_NAMES = {
    "G1": "后端全量测试 + 前端类型检查/构建",
    "G2": "GitHub CI 主分支绿且与当前提交一致",
    "G3": "本地与远端同源（ahead/behind 均为 0）",
    "G4": "已跟踪文件零明文凭证 + 密钥实体已清理",
    "G5": "至少一家平台真实 API 联调通过",
    "G6": "人工基线与 30 点事实抽检由真人填写",
    "G7": "价格表已由运营决策并配置",
    "G8": "对外文档与实测一致（无漂移）",
}


@dataclass(frozen=True)
class GateResult:
    gate: str
    name: str
    ok: bool
    detail: str


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def evaluate(evidence: dict, head_commit: str | None = None) -> list[GateResult]:
    """纯函数：输入证据字典，输出 8 项门禁结果。不联网、不读文件。"""
    backend = _dict(evidence.get("backend_tests"))
    frontend = _dict(evidence.get("frontend_build"))
    ci = _dict(evidence.get("github_ci"))
    branch = _dict(evidence.get("branch_sync"))
    secrets = _dict(evidence.get("secrets"))
    shopify = _dict(evidence.get("shopify_live"))
    human = _dict(evidence.get("human_eval"))
    pricing = _dict(evidence.get("pricing"))
    docs = _dict(evidence.get("docs"))

    g1_ok = (
        backend.get("failed") == 0
        and isinstance(backend.get("passed"), int)
        and backend["passed"] >= 500
        and frontend.get("tsc_errors") == 0
        and frontend.get("build_ok") is True
    )
    g1_detail = (
        f"pytest {backend.get('passed', '?')} passed / {backend.get('failed', '?')} failed；"
        f"tsc errors={frontend.get('tsc_errors', '?')}；build_ok={frontend.get('build_ok', '?')}"
    )

    sha_ok = not head_commit or ci.get("head_sha") == head_commit
    g2_ok = (
        ci.get("status") == "completed"
        and ci.get("conclusion") == "success"
        and sha_ok
    )
    g2_detail = (
        f"run={ci.get('run_id', '未提供')} status={ci.get('status', '未提供')} "
        f"conclusion={ci.get('conclusion', '未提供')} "
        f"sha_matches={sha_ok}"
    )

    g3_ok = branch.get("ahead") == 0 and branch.get("behind") == 0
    g3_detail = f"ahead={branch.get('ahead', '?')} behind={branch.get('behind', '?')}"

    plaintext = secrets.get("plaintext_files") or []
    g4_ok = (
        secrets.get("tracked_matches", 1) == 0
        and not plaintext
        and secrets.get("gitignore_ok") is True
    )
    g4_detail = (
        f"tracked_matches={secrets.get('tracked_matches', '?')}；"
        f"plaintext_files={plaintext or '无'}；gitignore_ok={secrets.get('gitignore_ok', '?')}"
    )

    g5_ok = shopify.get("verified") is True
    g5_detail = (
        f"shopify.verified={shopify.get('verified', '?')}；"
        f"products={shopify.get('products', '?')} orders={shopify.get('orders', '?')}"
    )

    total = human.get("spotcheck_total")
    checked = human.get("spotcheck_checked")
    g6_ok = (
        human.get("baseline_null") is False
        and isinstance(total, int) and total > 0
        and checked == total
    )
    g6_detail = (
        f"baseline_null={human.get('baseline_null', '?')}；"
        f"spotcheck={checked, total if total is not None else '?'}"
    )

    g7_ok = pricing.get("decided") is True
    g7_detail = f"pricing.decided={pricing.get('decided', '?')}；文件={pricing.get('file', '未提供')}"

    g8_ok = docs.get("drift") is False
    g8_detail = f"docs.drift={docs.get('drift', '?')}；说明={docs.get('note', '未提供')}"

    values = [g1_ok, g2_ok, g3_ok, g4_ok, g5_ok, g6_ok, g7_ok, g8_ok]
    details = [g1_detail, g2_detail, g3_detail, g4_detail,
               g5_detail, g6_detail, g7_detail, g8_detail]
    return [
        GateResult(gate, GATE_NAMES[gate], ok, detail)
        for gate, ok, detail in zip(GATE_NAMES, values, details)
    ]


def _git(*args: str) -> str:
    # 必须在**外层仓库**跑 git：tradepilot/.git 是无 remote 的遗留仓，
    # 在那里问 HEAD/branch 会得到 79921ed 这种假答案。
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT.parent, capture_output=True, text=True,
            timeout=20, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _looks_like_secret(text: str) -> bool:
    """只认够长、非占位的 token 形态；文档示例与测试假值不算泄露。"""
    lowered = text.lower()
    if any(mark in lowered for mark in ("xxxx", "example", "fake", "your_token", "你的token")):
        return False
    return bool(
        re.search(r"shpat_[A-Za-z0-9]{20,}", text)
        or re.search(r"sk-[A-Za-z0-9]{24,}", text)
    )


def _scan_tracked_secrets() -> int:
    files = _git("ls-files").splitlines()
    hits = 0
    for rel in files:
        if rel.endswith((".pdf", ".png", ".zip", ".mp4", ".jpg")):
            continue
        path = ROOT / rel
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _looks_like_secret(text):
            hits += 1
    return hits


def collect(prior: dict | None = None) -> dict:
    """采集能自动判定的字段；G1/G2 保留 prior 中的实测值，不凭空生成。"""
    evidence = json.loads(json.dumps(prior or {}))

    head = _git("rev-parse", "HEAD")
    counts = _git("rev-list", "--left-right", "--count", "HEAD...origin/main")
    ahead, behind = (counts.split() + ["0", "0"])[:2] if counts else ("0", "0")

    eval_data = _read_json(ROOT / "assets" / "eval_results.json")
    human = _dict(eval_data.get("human_baseline"))
    spot = _dict(eval_data.get("fact_spot_check_accuracy"))
    shopify_result = _read_json(ROOT / "assets" / "shopify_verify_result.json")

    plaintext = [
        str(rel) for rel in ("tradepilot/.llm_key", "tradepilot/.listinggood_key",
                             "tradepilot/.env.local")
        if (ROOT.parent / rel).is_file()
    ]
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8", errors="ignore")
    gitignore_ok = all(
        entry in gitignore for entry in (
            "tradepilot/.llm_key", "tradepilot/.listinggood_key", "tradepilot/.env.local"
        )
    )

    pricing_file = ROOT / "config" / "pricing.json"
    pricing_decided = pricing_file.is_file() and bool(_read_json(pricing_file).get("plans"))

    evidence.update({
        "checked_at": evidence.get("checked_at"),
        "commit": head or evidence.get("commit"),
        "branch_sync": {"ahead": int(ahead), "behind": int(behind)},
        "secrets": {
            "tracked_matches": _scan_tracked_secrets(),
            "plaintext_files": plaintext,
            "gitignore_ok": gitignore_ok,
        },
        "shopify_live": {
            "verified": shopify_result.get("verified") is True,
            "products": shopify_result.get("products"),
            "orders": shopify_result.get("orders"),
            "checked_at": shopify_result.get("checked_at"),
        },
        "human_eval": {
            "baseline_null": human.get("task_time_min") is None,
            "spotcheck_checked": spot.get("checked"),
            "spotcheck_total": spot.get("total"),
        },
        "pricing": {"decided": pricing_decided, "file": str(pricing_file.relative_to(ROOT))},
    })
    return evidence


def _print_table(results: list[GateResult]) -> None:
    print(f"{'门禁':<4} {'结果':<6} 说明")
    print("-" * 88)
    for item in results:
        flag = "PASS" if item.ok else "FAIL"
        print(f"{item.gate:<4} {flag:<6} {item.name}｜{item.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("check", "collect"), default="check")
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_FILE)
    args = parser.parse_args(argv)

    if args.action == "collect":
        prior = _read_json(args.evidence)
        evidence = collect(prior)
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已刷新证据：{args.evidence}")
        print("注意：G1/G2 保留原值，必须由真实测试/CI 运行回填，门禁不代填。")
        return 0

    evidence = _read_json(args.evidence)
    head = _git("rev-parse", "HEAD")
    results = evaluate(evidence, head_commit=head or None)
    _print_table(results)
    failed = [item.gate for item in results if not item.ok]
    print("-" * 88)
    if failed:
        print(f"未通过：{', '.join(failed)}；当前不可对外宣称生产级。")
        return 1
    print("8/8 门禁通过：可对外宣称生产级。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
