# -*- coding: utf-8 -*-
"""InternAI 全模型可用性矩阵：谁有额度 + 谁能正常回 content（JSON 任务实测）。"""
import json
import re
import time

import httpx

TXT = r"D:\fyg199internAPI.txt"
raw = open(TXT, encoding="utf-8").read()
KEY = re.findall(r"\bsk-[A-Za-z0-9]{40,}\b", raw)[0]
BASE = "https://discovery-api.intern-ai.org.cn/v1"
H = {"Authorization": f"Bearer {KEY}"}

MODELS = ["Atria-Dawn-Preview", "Atria-Dawn", "Sage-A1.5", "kimi-k2.6",
          "deepseek-v4.1-flash-1017", "minimax-m3", "qwen3.8-27b",
          "Agents-A1", "deepseek-v4-pro-0813"]

PROMPT = ('你是编排规划器。输出严格 JSON（不要其他文字）：'
          '{"next_tool":"market_scan","reason":"先看真实市场"}')

for model in MODELS:
    t0 = time.time()
    try:
        r = httpx.post(f"{BASE}/chat/completions", headers=H, timeout=90, json={
            "model": model, "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 300})
        dt = time.time() - t0
        if r.status_code != 200:
            print(f"[{model}] HTTP {r.status_code}: {r.text[:80]}")
            continue
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content")
        reasoning = len(str(msg.get("reasoning_content") or ""))
        try:
            parsed = json.loads(str(content).strip().strip("`").lstrip("json").strip())
            ok = isinstance(parsed, dict) and "next_tool" in parsed
        except Exception:  # noqa: BLE001
            ok = False
        print(f"[{model}] {dt:.1f}s content={str(content)[:60]!r} reasoning={reasoning}ch JSON解析={'✓' if ok else '✗'}")
    except Exception as e:  # noqa: BLE001
        print(f"[{model}] FAIL {type(e).__name__}: {str(e)[:80]}")
