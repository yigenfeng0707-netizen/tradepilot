# -*- coding: utf-8 -*-
"""阶跃 API 连通性探针：从 D:\阶跃5preview.txt 读 key（不写死在代码里），测 step-5-preview。"""
import json
import re
import sys

sys.path.insert(0, ".")

import httpx

TXT = r"D:\阶跃5preview.txt"
raw = open(TXT, encoding="utf-8").read()
KEYS = [k for k in re.findall(r"\b([A-Za-z0-9]{40,})\b", raw)][:2]
BASE = "https://api.stepfun.com/step_plan/v1"


def mask(k: str) -> str:
    return k[:6] + "…" + k[-4:]


for key in KEYS:
    try:
        r = httpx.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "step-5-preview",
                  "messages": [{"role": "user", "content": "只回复两个字：在线"}],
                  "max_tokens": 16},
            timeout=60,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"[{mask(key)}] HTTP 200 OK · step-5-preview 回复: {content!r}")
        else:
            print(f"[{mask(key)}] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{mask(key)}] FAIL {type(e).__name__}: {e}")
    print("-" * 50)
