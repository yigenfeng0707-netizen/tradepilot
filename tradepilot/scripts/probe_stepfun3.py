# -*- coding: utf-8 -*-
"""阶跃排查第二轮：第二把 Key 全矩阵 + 计划端点完整模型清单 + 额度端点猜测。"""
import json
import re
import sys

import httpx

TXT = r"D:\阶跃5preview.txt"
raw = open(TXT, encoding="utf-8").read()
KEYS = re.findall(r"\b([A-Za-z0-9]{40,})\b", raw)[:2]
BASES = {
    "plan": "https://api.stepfun.com/step_plan/v1",
    "std": "https://api.stepfun.com/v1",
}
H = lambda k: {"Authorization": f"Bearer {k}"}  # noqa: E731


def mask(k: str) -> str:
    return k[:6] + "…" + k[-4:]


print("=== 计划端点完整模型清单 ===")
r = httpx.get(f"{BASES['plan']}/models", headers=H(KEYS[0]), timeout=30)
data = r.json().get("data", [])
print("HTTP", r.status_code, "| 模型数:", len(data))
for m in data:
    print("  ·", m.get("id"), "|", m.get("model_type"), "| vision:", m.get("enable_vision_input"))

print("\n=== 第二把 Key 全矩阵 ===")
for bname, base in BASES.items():
    for model in ("step-5-preview", "step-3.7-flash"):
        try:
            r = httpx.post(f"{base}/chat/completions", headers=H(KEYS[1]),
                           json={"model": model, "messages": [{"role": "user", "content": "OK"}],
                                 "max_tokens": 8}, timeout=60)
            print(f"[key2 {bname} × {model}] HTTP {r.status_code}: {r.text[:100]}")
        except Exception as e:  # noqa: BLE001
            print(f"[key2 {bname} × {model}] FAIL {type(e).__name__}")

print("\n=== 额度/账户端点探测（404=不存在，200=有货）===")
GUESSES = [
    ("plan", "/users/me"), ("plan", "/credit"), ("plan", "/quota"), ("plan", "/usage"),
    ("std", "/accounts"), ("std", "/users/me"), ("std", "/dashboard/billing/credit_grants"),
]
for bname, path in GUESSES:
    try:
        r = httpx.get(f"{BASES[bname]}{path}", headers=H(KEYS[0]), timeout=20)
        print(f"[{bname}{path}] HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{bname}{path}] FAIL {type(e).__name__}")
