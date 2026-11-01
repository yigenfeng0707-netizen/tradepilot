# Copyright 2026 TradePilot project. Apache-2.0.
"""部署前清理：把「本机运行态」与「不参与演示的凭证」移出上传目录。

## 为什么需要它

sites 部署会把**整个项目目录**打包上传，只排除 `node_modules` / `.git` / 构建产物。
所以这些会被一起传到沙箱：

| 文件 | 传上去的后果 |
|---|---|
| `assets/provenance.db` | 线上存证不是从 0 开始，任务里"存证条数"混着本机测试数据 |
| `assets/missions/*.json` | 线上任务列表躺着本机的历史任务，评委点开是别人的实验 |
| `.llm_key` / `.listinggood_key` | 个人密钥被复制到托管沙箱（应用本身读不到公网，但没必要传） |

2026-10-31 就踩了这个坑：线上跑起来 `plan_engine=llm`（说明用了本机 Key），
任务列表里还挂着一周前那条 USB-C 的任务。

## 用法

    python scripts/deploy_clean.py stash                      # 只移运行态数据
    python scripts/deploy_clean.py stash --keys .listinggood_key
    <部署>
    python scripts/deploy_clean.py restore                    # 移回来，本地开发照旧
    python scripts/deploy_clean.py status

`stash` 会把东西移到 `.deploy_stash/`（已 gitignore）并写一份清单，
`restore` 按清单放回原位。**stash 之后忘 restore 不影响本地跑**——
程序会在数据目录里重建空的存证库，只是历史没了。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _root() -> Path:
    """上传目录。测试用 TRADEPILOT_DEPLOY_ROOT 指到临时目录，避免动真实数据。"""
    env = os.environ.get("TRADEPILOT_DEPLOY_ROOT", "").strip()
    return Path(env) if env else ROOT


def _stash_dir(root: Path) -> Path:
    return root / ".deploy_stash"

# 运行态数据：可由抓取重建，但没必要带上线
RUNTIME = [
    "assets/provenance.db",
    "assets/provenance.db-wal",
    "assets/provenance.db-shm",
    "assets/missions",
]

KNOWN_KEYS = (".llm_key", ".listinggood_key")


def _stash(keys: list[str]) -> int:
    root = _root()
    stash = _stash_dir(root)
    targets = list(RUNTIME) + list(keys)
    stash.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for rel in targets:
        src = root / rel
        if not src.exists():
            continue
        dst = stash / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():                       # 上一轮没 restore，先清掉旧的
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        shutil.move(str(src), str(dst))
        moved.append(rel)
    # 合并既有清单，避免两次 stash 互相覆盖
    prev = _load_manifest(root)
    manifest = sorted(set(prev) | set(moved))
    _manifest_path(root).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not moved:
        print("没有需要移出的文件（已经是干净状态）")
    else:
        print("已移出上传目录：")
        for m in moved:
            print(f"  - {m}")
    print(f"\n现在可以部署。部署完成后执行：python scripts/deploy_clean.py restore")
    return 0


def _restore() -> int:
    root = _root()
    stash = _stash_dir(root)
    manifest = _load_manifest(root)
    if not manifest:
        print("没有待恢复的文件（.deploy_stash 为空或不存在）")
        return 0
    restored: list[str] = []
    for rel in manifest:
        src = stash / rel
        if not src.exists():
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        shutil.move(str(src), str(dst))
        restored.append(rel)
    _manifest_path(root).write_text("[]", encoding="utf-8")
    print("已恢复：")
    for r in restored:
        print(f"  - {r}")
    return 0


def _manifest_path(root: Path) -> Path:
    return _stash_dir(root) / "manifest.json"


def _load_manifest(root: Path) -> list[str]:
    try:
        data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _status() -> int:
    root = _root()
    stash = _stash_dir(root)
    manifest = _load_manifest(root)
    print(f"上传目录：{root}")
    print(f"暂存区：  {stash}{'（存在）' if stash.exists() else '（不存在）'}")
    if manifest:
        print("\n仍在暂存区（部署完记得 restore）：")
        for m in manifest:
            print(f"  - {m}  {'✓在暂存区' if (stash / m).exists() else '✗已不在'}")
    else:
        print("\n暂存区为空：当前目录会**原样上传**。")
        dirty = [r for r in RUNTIME if (root / r).exists()]
        keys = [k for k in KNOWN_KEYS if (root / k).exists()]
        if dirty:
            print("  含运行态数据：" + "、".join(dirty))
        if keys:
            print("  含凭证文件：" + "、".join(keys) + "（会被上传到沙箱）")
        if dirty or keys:
            print("\n  部署前建议先跑：python scripts/deploy_clean.py stash --keys <要移走的凭证>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("stash", "restore", "status"))
    ap.add_argument("--keys", default="",
                    help="额外移走的凭证文件，逗号分隔，如 .listinggood_key")
    args = ap.parse_args()
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    for k in keys:
        if k not in KNOWN_KEYS:
            print(f"未知凭证 {k}；已知：{', '.join(KNOWN_KEYS)}", file=sys.stderr)
            return 2
    if args.action == "stash":
        return _stash(keys)
    if args.action == "restore":
        return _restore()
    return _status()


if __name__ == "__main__":
    sys.exit(main())
