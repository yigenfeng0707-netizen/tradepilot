# -*- coding: utf-8 -*-
"""发布面漂移检查：线上部署 vs 本地代码，任一项不一致就非零退出。

为什么需要它：参赛材料写"8 个工具 / 原生 Canvas / 每个数字可点击溯源"，
而公网链接上跑的可能是上周的旧镜像。评委点开链接看到的和 BP 写的不一致，
比代码有 bug 更致命——这是方案的"运河教训"（简介与 JSON 相互矛盾）的部署版。

只做只读 GET，不触发任务、不写云端数据。跑法：
    python scripts/check_release_drift.py [--base https://xxx.ms.show]
"""
import argparse
import hashlib
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UA = "Mozilla/5.0 (compatible; TradePilotReleaseDrift/1.0)"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def local_truth() -> dict:
    """本地代码的"应当如此"——从代码本身取，不手写常量，否则门禁自己会过期。"""
    from app.main import app
    from app.orchestrator import build_registry

    routes = {getattr(r, "path", "") for r in app.routes}
    js = (ROOT / "web" / "app.js").read_bytes()
    return {
        "tools": set(build_registry().names()),
        "has_provenance_route": "/api/provenance" in routes,
        "app_js_sha": _sha(js),
        "app_js_bytes": len(js),
        "canvas_in_js": b"canvas" in js.lower(),
    }


def remote_state(base: str, client: httpx.Client) -> dict:
    tools: set[str] = set()
    r = client.get(f"{base}/api/tools", headers={"User-Agent": UA})
    if r.status_code == 200:
        tools = {t["name"] for t in r.json().get("tools", [])}
    prov = client.get(f"{base}/api/provenance", headers={"User-Agent": UA})
    js_resp = client.get(f"{base}/static/app.js", headers={"User-Agent": UA})
    js = js_resp.content if js_resp.status_code == 200 else b""
    return {
        "tools": tools,
        "has_provenance_route": prov.status_code != 404,
        "app_js_sha": _sha(js) if js else None,
        "app_js_bytes": len(js),
        "canvas_in_js": b"canvas" in js.lower(),
    }


def compare(local: dict, remote: dict) -> list[tuple[str, bool, str]]:
    rows = []
    missing = local["tools"] - remote["tools"]
    extra = remote["tools"] - local["tools"]
    rows.append((
        f"工具白名单（本地 {len(local['tools'])} / 线上 {len(remote['tools'])}）",
        not missing and not extra,
        f"线上缺：{sorted(missing) or '—'}；线上多：{sorted(extra) or '—'}",
    ))
    rows.append((
        "溯源接口 /api/provenance",
        local["has_provenance_route"] == remote["has_provenance_route"],
        f"本地路由={local['has_provenance_route']}，线上可用={remote['has_provenance_route']}",
    ))
    rows.append((
        "前端 Canvas 大屏",
        local["canvas_in_js"] == remote["canvas_in_js"],
        f"本地 app.js canvas={local['canvas_in_js']}，线上 canvas={remote['canvas_in_js']}"
        f"（线上 {remote['app_js_bytes']}B vs 本地 {local['app_js_bytes']}B）",
    ))
    rows.append((
        "前端静态文件与本地一致",
        remote["app_js_sha"] == local["app_js_sha"],
        f"sha256[:16] 本地={local['app_js_sha']} 线上={remote['app_js_sha']}",
    ))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://fygrobert-tradepilot.ms.show")
    args = ap.parse_args()

    local = local_truth()
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        health = client.get(f"{args.base}/api/health", headers={"User-Agent": UA})
        if health.status_code != 200:
            print(f"✗ 线上不可达：{args.base} → HTTP {health.status_code}")
            return 2
        remote = remote_state(args.base, client)

    print(f"发布面漂移检查：{args.base}\n")
    bad = 0
    for name, ok, detail in compare(local, remote):
        print(f"  [{'OK  ' if ok else 'DRIFT'}] {name}\n         {detail}")
        bad += 0 if ok else 1
    print(f"\n{'✅ 线上与本地一致，可以按材料演示' if not bad else f'❌ {bad} 项漂移：线上镜像落后于代码，先重新部署再谈演示'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

