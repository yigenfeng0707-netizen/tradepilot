# -*- coding: utf-8 -*-
"""阶跃排查第三轮：标准端点模型全清单 + 用标准模型试聊天 + 计划用量全量。"""
import json
import re
import sys

import httpx

TXT = r"D:\阶跃5preview.txt"
raw = open(TXT, encoding="utf-8").read()
KEY = re.findall(r"\b([A-Za-z0-9]{40,})\b", raw)[0]
H = {"Authorization": f"Bearer {KEY}"}

print("=== 标准端点全部模型 ===")
r = httpx.get("https://api.stepfun.com/v1/models", headers=H, timeout=30)
data = r.json().get("data", [])
llm_ids = [m["id"] for m in data if "大语言" in str(m.get("model_type", ""))]
all_ids = [m["id"] for m in data]
print("HTTP", r.status_code, "| 模型数:", len(all_ids))
print("LLM 类:", llm_ids)
print("全部:", all_ids)

print("\n=== 标准端点 × 各 LLM 模型试聊天 ===")
for model in llm_ids[:4]:
    try:
        r = httpx.post("https://api.stepfun.com/v1/chat/completions", headers=H,
                       json={"model": model, "messages": [{"role": "user", "content": "回复OK"}],
                             "max_tokens": 8}, timeout=60)
        if r.status_code == 200:
            print(f"[std × {model}] HTTP 200 ✓ 回复: {r.json()['choices'][0]['message']['content']!r}")
        else:
            print(f"[std × {model}] HTTP {r.status_code}: {r.text[:110]}")
    except Exception as e:  # noqa: BLE001
        print(f"[std × {model}] FAIL {type(e).__name__}")

print("\n=== 计划用量接口完整返回 ===")
r = httpx.get("https://api.stepfun.com/step_plan/v1/usage", headers=H, timeout=30)
try:
    print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:1200])
except Exception:  # noqa: BLE001
    print(r.text[:500])
