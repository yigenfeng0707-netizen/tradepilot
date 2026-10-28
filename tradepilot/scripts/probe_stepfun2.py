# -*- coding: utf-8 -*-
"""阶跃 API 系统排查：Base×模型×端点矩阵，定位额度真正的位置。"""
import re
import sys

import httpx

TXT = r"D:\阶跃5preview.txt"
raw = open(TXT, encoding="utf-8").read()
KEY = re.findall(r"\b([A-Za-z0-9]{40,})\b", raw)[0]
MASK = KEY[:6] + "…" + KEY[-4:]

BASES = {
    "step_plan/v1": "https://api.stepfun.com/step_plan/v1",
    "标准/v1": "https://api.stepfun.com/v1",
}
MODELS = ["step-5-preview", "step-3.7-flash", "step-router-v1", "step-3.5-flash"]

H = {"Authorization": f"Bearer {KEY}"}


def try_models_list() -> None:
    for name, base in BASES.items():
        try:
            r = httpx.get(f"{base}/models", headers=H, timeout=30)
            body = r.text[:300]
            print(f"[GET {name}/models] HTTP {r.status_code}: {body}")
        except Exception as e:  # noqa: BLE001
            print(f"[GET {name}/models] FAIL {type(e).__name__}: {e}")


def try_chat(base_name: str, base: str, model: str) -> bool:
    try:
        r = httpx.post(
            f"{base}/chat/completions", headers=H,
            json={"model": model, "messages": [{"role": "user", "content": "回复：OK"}],
                  "max_tokens": 8},
            timeout=60,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"[chat {base_name} × {model}] HTTP 200 ✓ 回复: {content!r}")
            return True
        msg = r.json().get("error", {}).get("message", r.text[:120]) if r.text.startswith("{") else r.text[:120]
        print(f"[chat {base_name} × {model}] HTTP {r.status_code}: {msg}")
    except Exception as e:  # noqa: BLE001
        print(f"[chat {base_name} × {model}] FAIL {type(e).__name__}: {e}")
    return False


def try_messages() -> None:
    """Anthropic 风格 /v1/messages 端点。"""
    try:
        r = httpx.post(
            "https://api.stepfun.com/step_plan/v1/messages", headers=H,
            json={"model": "step-5-preview", "max_tokens": 8,
                  "messages": [{"role": "user", "content": "回复：OK"}]},
            timeout=60,
        )
        print(f"[messages step_plan] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"[messages step_plan] FAIL {type(e).__name__}: {e}")


print(f"Key {MASK}")
print("=" * 60)
try_models_list()
print("=" * 60)
for name, base in BASES.items():
    for model in MODELS:
        if try_chat(name, base, model):
            print(f"\n*** 可用组合：{name} + {model} ***")
            sys.exit(0)
try_messages()
