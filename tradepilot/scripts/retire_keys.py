#!/usr/bin/env python3
# Copyright 2026 TradePilot project. Apache-2.0.
"""网关吊销旧 Key 之后，安全删除工作区里的明文实体并留审计记录。

设计红线：
1. 默认只检查，不删任何东西。
2. 即使 ``--apply``，没有 ``--revoked`` 或 ``--owner-decided`` 也一律拒绝执行（返回 2）。
   这是防止"先删本地文件、以为已经吊销"的人为事故。
3. ``--revoked`` 表示人已在服务商控制台吊销；``--owner-decided`` 表示业主明确评估后
   决定不吊销（如免费测试 Key、无计费影响），两者都会在审计日志里如实区分，
   不允许把"没吊销"写成"已吊销"。
4. 日志只记文件名、字节数、sha256 前 12 位和时间，**不记密钥内容**。
5. 不联网、不碰网关；吊销动作必须由人在服务商控制台完成。

用法：
    python scripts/retire_keys.py
    python scripts/retire_keys.py --revoked --apply
    python scripts/retire_keys.py --owner-decided --apply
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_KEYS: tuple[Path, ...] = (Path(".llm_key"), Path(".listinggood_key"))


def _sha256_prefix(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def retire_keys(
    root: Path,
    paths: Iterable[Path],
    *,
    revoked: bool,
    apply: bool,
    log_path: Path,
    owner_decided: bool = False,
) -> int:
    """检查/删除密钥实体。返回 0=成功，2=拒绝执行。"""
    if apply and not (revoked or owner_decided):
        return 2

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    basis = "revoked" if revoked else "owner-decided"
    lines = [f"# retire_keys {stamp} basis={basis} apply={apply}"]
    for rel in paths:
        target = root / rel
        if not target.is_file():
            lines.append(f"MISSING {rel}")
            continue
        size = target.stat().st_size
        prefix = _sha256_prefix(target)
        if apply:
            target.unlink()
            tag = "DELETED" if revoked else "DELETED_OWNER_DECIDED"
            note = "" if revoked else " （未在网关吊销，业主评估后决定仅删本地实体）"
            lines.append(f"{tag} {rel} size={size} sha256={prefix}...{note}")
        else:
            lines.append(f"PRESENT {rel} size={size} sha256={prefix}... (not deleted)")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revoked",
        action="store_true",
        help="确认服务商控制台已吊销这些 Key；没有它就不允许 --apply",
    )
    parser.add_argument(
        "--owner-decided",
        action="store_true",
        help="业主已评估并决定不吊销（如免费测试 Key），仅删除本地实体；日志如实区分",
    )
    parser.add_argument("--apply", action="store_true", help="真正删除文件（默认只检查）")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="密钥所在根目录，默认 tradepilot/",
    )
    parser.add_argument("--key", action="append", type=Path, help="覆盖默认密钥相对路径")
    args = parser.parse_args(argv)

    paths = args.key or list(DEFAULT_KEYS)
    log_path = args.root / "docs" / "security" / "key-retirement.log"
    code = retire_keys(
        args.root,
        paths,
        revoked=args.revoked,
        apply=args.apply,
        log_path=log_path,
        owner_decided=args.owner_decided,
    )
    if code == 2:
        print("REFUSED: 必须先确认网关已吊销（--revoked）或业主决定不吊销"
              "（--owner-decided），才允许 --apply 删除本地实体。")
        return code
    for rel in paths:
        target = args.root / rel
        state = "MISSING" if not target.exists() else "PRESENT"
        print(f"{state} {rel}")
    print(f"审计记录：{log_path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
