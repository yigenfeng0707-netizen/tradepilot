# -*- coding: utf-8 -*-
"""InternAI 网关探针：验证两把 Key × 全部模型，选出默认模型。"""
import time

import httpx

TXT = r"D:\fyg199internAPI.txt"
raw = open(TXT, encoding="utf-8").read()
KEYS = re.findall(r"\bsk-[A-Za-z0-9]{40,}\b", raw) if (re := __import__("re")) else []
BASE = "https://discovery-api.intern-ai.org.cn/v1"
MODELS = ["intern-s2", "glm-5.3", "deepseek-v4-flash-0731", "deepseek-v4-pro-0813",
          "minimax-m3", "qwen3.8-27b", "Atria-Dawn-Preview", "Agents-A1"]


def mask(k: str) -> str:
    return k[:8] + "…" + k[-4:]


# 先列模型清单（确认 Key 有效性与实际可用集合）
for key in KEYS:
    try:
        r = httpx.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        ids = [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else []
        print(f"[models {mask(key)}] HTTP {r.status_code} · {len(ids)} 个模型: {ids[:12]}")
    except Exception as e:  # noqa: BLE001
        print(f"[models {mask(key)}] FAIL {type(e).__name__}: {e}")

print("-" * 60)

winner = None
for key in KEYS:
    for model in MODELS:
        t0 = time.time()
        try:
            r = httpx.post(
                f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": "只回复两个字：在线"}],
                      "max_tokens": 16},
                timeout=90,
            )
            dt = time.time() - t0
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                print(f"[{mask(key)} × {model}] 200 ✓ {dt:.1f}s 回复: {content!r}")
                if winner is None:
                    winner = (key, model, dt)
            else:
                print(f"[{mask(key)} × {model}] HTTP {r.status_code}: {r.text[:90]}")
        except Exception as e:  # noqa: BLE001
            print(f"[{mask(key)} × {model}] FAIL {type(e).__name__}: {str(e)[:80]}")
        if winner:
            break
    if winner:
        break

print("=" * 60)
if winner:
    print(f"默认组合选定：{mask(winner[0])} × {winner[1]}（{winner[2]:.1f}s）")
