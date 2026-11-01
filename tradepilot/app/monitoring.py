# Copyright 2026 TradePilot project. Apache-2.0.
"""进程内监控（M3-⑤）：请求计数、延迟分位、告警判定。

定位：单实例的最小生产级监控。指标存进程内存——**重启即清零**，
这是如实声明的边界；多实例聚合要上 Prometheus/Cloud Monitoring（M4 的事），
但在那之前进程内指标也远好于"出事了靠用户反馈才知道"。

告警判定在 snapshot() 里给出布尔结论，口径写死在注释：
- `high_error_rate`：总请求 ≥ 20 且 5xx 占比 > 5%。样本太小时判定等于噪声。
- `llm_offline` / `db_ok` 等外部状态由 main.py 组装——本模块只管自己的计数。
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any

_START = time.time()

_counts: dict[str, int] = defaultdict(int)          # route -> 次数
_errors: dict[str, int] = defaultdict(int)          # route -> 5xx 次数
_latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=256))


def normalize_path(path: str) -> str:
    """路径参数归一成 route 模板：/api/missions/mission-20261101-090001
    与 /api/provenance/12 各算一个 route——否则每个 id 一个计数桶，
    桶数无限膨胀且毫无聚合价值。"""
    path = re.sub(r"/mission-[0-9]{8}-[0-9]{6}", "/{mission_id}", path)
    path = re.sub(r"/(api/provenance)/\d+", r"/\1/{pid}", path)
    return path


def record_request(path: str, status: int, elapsed_s: float) -> None:
    """中间件每次请求后调用。5xx 计错误；延迟存环形样本（每 route 最多 256 条）。"""
    route = normalize_path(path)
    _counts[route] += 1
    if status >= 500:
        _errors[route] += 1
    _latencies[route].append(elapsed_s)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vs = sorted(values)
    idx = min(len(vs) - 1, max(0, round(q * (len(vs) - 1))))
    return round(vs[idx], 3)


def _rss_mb() -> float | None:
    """进程 RSS。只有 Linux（生产目标 Cloud Run）能读到；其他平台如实 null，
    不猜。"""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)  # kB -> MB
    except OSError:
        pass
    return None


def snapshot() -> dict[str, Any]:
    total = sum(_counts.values())
    err_total = sum(_errors.values())
    error_rate = round(err_total / total, 4) if total else 0.0

    routes: dict[str, Any] = {}
    for route, n in sorted(_counts.items()):
        lats = list(_latencies[route])
        routes[route] = {
            "count": n,
            "errors_5xx": _errors[route],
            "latency_p50_s": _percentile(lats, 0.50),
            "latency_p95_s": _percentile(lats, 0.95),
        }

    alarms: list[str] = []
    if total >= 20 and error_rate > 0.05:
        alarms.append("high_error_rate")

    return {
        "uptime_s": round(time.time() - _START, 1),
        "process_rss_mb": _rss_mb(),
        "requests": {"total": total, "errors_5xx": err_total,
                     "error_rate": error_rate},
        "routes": routes,
        "alarms": alarms,
        "note": "指标存进程内存，重启清零；单实例口径。多实例聚合属 M4。",
    }


def reset_for_tests() -> None:
    """仅供测试：清空计数与样本。生产代码不要调用。"""
    _counts.clear()
    _errors.clear()
    _latencies.clear()
