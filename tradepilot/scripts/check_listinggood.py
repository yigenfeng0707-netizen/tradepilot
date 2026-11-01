# Copyright 2026 TradePilot project. Apache-2.0.
"""外部合规复核的一次性联调脚本（不是测试，不进 CI）。

用途：改了 `app/datasources/listinggood.py` 之后，用真实 Key 打一次真实接口，
确认契约没变（对方改字段是最常见的静默破坏）。

    # 免费浅检（不扣额度）
    TRADEPILOT_LISTINGGOOD=check python scripts/check_listinggood.py

    # 深度体检（**扣月度额度**，只在演示前手动跑一次）
    TRADEPILOT_LISTINGGOOD=deep python scripts/check_listinggood.py --deep

    # 指定一段真实文案
    python scripts/check_listinggood.py --mission assets/missions/xxx.json

注意：这个脚本会打印**本地审计**与**外部复核**两份结论及分歧。
两者不一致是正常现象（方法不同），不要为了"看起来一致"去调本地规则。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CONFIG                        # noqa: E402
from app.datasources import listinggood as LG        # noqa: E402
from app.tools import listing_audit as LA            # noqa: E402

# 内置样例：一段**故意合规**的英文文案。用它来验证"外部引擎不会无脑报警"，
# 如果这段被判高危，多半是对方规则误报（实测它的浅检会把 stops 判医疗宣称）。
SAMPLE = {
    "title": "Stainless Steel Dog Bowl with Non Slip Silicone Base, 32 oz",
    "bullets": [
        "Rust resistant 304 stainless steel keeps water cooler for longer "
        "than plastic bowls during warm afternoons indoors or outdoors.",
        "Non slip silicone ring on the base grips tile and hardwood so the "
        "bowl stays put while a medium sized dog drinks or eats quickly.",
        "Dishwasher safe on the top rack makes the daily rinse and weekly "
        "deep clean take a couple of minutes instead of scrubbing by hand.",
        "Holds 32 oz of water or about 4 cups of dry food, which covers one "
        "full day for a dog between 20 and 50 pounds according to the label.",
        "Wide rim and shallow profile let flat faced breeds reach the water "
        "without pressing their muzzle against the side of the bowl.",
    ],
    "description": "A stainless steel bowl for medium dogs.",
}


def _from_mission(path: str) -> dict:
    """从任务存档里取真实文案（listing_draft.copy）。取不到就退回样例。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取任务存档失败：{exc}")
        return SAMPLE
    draft = ((data.get("results") or {}).get("listing_draft") or {}).get("data") or {}
    copy = draft.get("copy") or {}
    if copy.get("title"):
        print(f"文案来源：{path}（keyword={draft.get('keyword')!r}）")
        return copy
    print(f"{path} 里没有 listing_draft.copy，改用内置样例")
    return SAMPLE


async def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mission", default="", help="从任务存档取文案")
    ap.add_argument("--deep", action="store_true",
                    help="强制深度体检（扣额度）；等价于 TRADEPILOT_LISTINGGOOD=deep")
    args = ap.parse_args()

    if args.deep:
        os.environ["TRADEPILOT_LISTINGGOOD"] = "deep"
        CONFIG.listinggood_mode = "deep"

    mode = LG.mode()
    print(f"模式：{mode} | Key：{'已配置' if CONFIG.listinggood_api_key else '未配置'}")
    if mode == "off":
        print("外部复核未启用。设置 TRADEPILOT_LISTINGGOOD=check 或 =deep 后再跑。")
        return 2

    copy = _from_mission(args.mission) if args.mission else SAMPLE

    local = LA.audit(copy, keyword="stainless steel dog bowl", marketplace="US")
    print(f"\n[本地规则审计] {local['score']} 分（{local['grade']}）→ {local['verdict']}")
    for i in local["issues"][:6]:
        print(f"  - [{i['level']}] {i['rule']}: {i['evidence'][:60]}")

    ext = await LG.review(copy, "US")
    print(f"\n[外部复核] status={ext.status} score={ext.score} "
          f"level={ext.level} 耗时={ext.latency_ms}ms")
    if ext.note:
        print(f"  说明：{ext.note}")
    if ext.summary:
        print(f"  结论：{ext.summary[:300]}")
    for i in ext.issues[:6]:
        print(f"  - [{i['severity']}] {i['finding'][:60]} ← {i['evidence'][:50]}")
    if ext.task_id:
        print(f"  task_id={ext.task_id}（超时也能拿它去后台查）")

    cc = LG.cross_check(local, ext)
    print(f"\n[交叉校验] {cc['verdict']}")
    if cc.get("reason"):
        print(f"  {cc['reason']}")
    else:
        print(f"  本地 {cc['local_score']} 分 / 外部 {cc['external_score']} 分（不取平均）")
        print(f"  本地必改 {cc['local_critical_n']} 项 · 外部高危 {cc['external_critical_n']} 项")
        if cc["external_only_n"]:
            print(f"  ⚠ 外部独有 {cc['external_only_n']} 项（本地规则未命中，需人工确认）：")
            for i in cc["external_only"]:
                print(f"    - {i.get('finding') or i.get('evidence')} ← {i.get('source')}")
        if cc["local_only_n"]:
            print(f"  本地独有 {cc['local_only_n']} 项（多为字符上限/结构类，外部不查）：")
            for i in cc["local_only"]:
                print(f"    - {i.get('rule')}: {i.get('evidence')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
