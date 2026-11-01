# Copyright 2026 TradePilot project. Apache-2.0.
"""SQLite → Postgres 数据导出（M3-① 迁移工具）。

用法：
    python scripts/export_sqlite_to_pg.py [--data-dir DIR] [--out FILE]

读取 data_dir 下的 auth.db（tenants/users/refresh_tokens）与 provenance.db，
生成一个 Postgres 可执行的 SQL 文件（INSERT 语句 + provenance 序列 setval）。
装载顺序：先 `psql -f scripts/pg_schema.sql` 建表，再 `psql -f 本输出` 导数据。

诚实边界（开发机无 Postgres 服务端）：
- **数据完整性在 SQLite 侧闭环验证**：生成的 INSERT 语句是标准 SQL，
  脚本自检会把它们重新装载进一个内存 SQLite 库，与源库逐行逐列比对
  （tests/test_pg_export.py 守护）；
- **Postgres 方言部分（DDL/setval）未经执行验证**——首次在真实环境
  跑通后把结果回填到 scripts/pg_schema.sql 头注与 docs 4.11。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 迁移的表与列（列清单与 app/auth.py init_db、app/provenance.py 逐列一致；
# 显式列出而不 SELECT *——源库将来加列时，迁移脚本会在这里 fail fast，
# 而不是默默漏迁新列。）
TABLES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # 表名 -> (源库, 建议装载顺序, 列)
    "tenants": ("auth.db", "1", ("id", "name", "company", "created_at")),
    "users": ("auth.db", "1", ("id", "tenant_id", "email", "password_hash",
                               "role", "disabled", "created_at")),
    "refresh_tokens": ("auth.db", "1", ("token_hash", "user_id", "expires_at",
                                        "revoked", "created_at")),
    "provenance": ("provenance.db", "2", ("id", "kind", "url", "fetched_at",
                                          "content_hash", "payload")),
}
IDENTITY_SEQ_TABLE = "provenance"       # 导出后需要 setval 的表


def _quote(value) -> str:
    """SQL 字符串字面量转义（PG/SQLite 通用：单引号 doubling）。
    None → NULL；float 用 repr 保精度（expires_at 是秒级 unix 浮点）。"""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def export(data_dir: str | Path, out_path: str | Path) -> dict[str, int]:
    """导出全部表。返回 {表名: 行数}，供调用方与自检断言。"""
    data_dir = Path(data_dir)
    lines: list[str] = [
        "-- 由 scripts/export_sqlite_to_pg.py 生成，请勿手改。",
        f"-- 源 data_dir：{data_dir}",
        "BEGIN;",
    ]
    counts: dict[str, int] = {}
    for table, (db_name, _order, cols) in TABLES.items():
        db = data_dir / db_name
        if not db.exists():
            counts[table] = 0
            continue
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # 只读打开：迁移不许写源库
        try:
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM {table}").fetchall()  # noqa: S608
        finally:
            conn.close()
        counts[table] = len(rows)
        col_list = ", ".join(cols)
        for row in rows:
            values = ", ".join(_quote(v) for v in row)
            lines.append(f"INSERT INTO {table} ({col_list}) VALUES ({values});")
    # provenance.id 是 IDENTITY：显式插入后必须把序列拨到位，否则新写入撞主键。
    # 事务内表已装载完，直接用 MAX(id) 拨序列即可。
    if counts.get("provenance"):
        lines.append(
            "SELECT setval(pg_get_serial_sequence('provenance', 'id'), "
            "(SELECT COALESCE(MAX(id), 0) FROM provenance));")
    else:
        lines.append("-- provenance 为空，跳过 setval")
    lines.append("COMMIT;")
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def verify_against_source(data_dir: str | Path, sql_text: str) -> list[str]:
    """自检：把生成的 INSERT 重新装载进内存 SQLite 库，与源库逐行比对。

    INSERT 语句是标准 SQL，SQLite 能解析——这验证的是**数据本身**完整
    无损、顺序正确、转义无误；Postgres 方言部分不在此验证范围。
    返回问题列表（空 = 通过）。
    """
    problems: list[str] = []
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    # 在内存库建最小骨架（类型不重要，只为了装得下）
    for table, (_db, _o, cols) in TABLES.items():
        col_defs = ", ".join(f'"{c}"' for c in cols)
        mem.execute(f"CREATE TABLE {table} ({col_defs})")
    loaded = 0
    for line in sql_text.splitlines():
        line = line.strip()
        if line.startswith("INSERT INTO"):
            try:
                mem.execute(line)
                loaded += 1
            except sqlite3.Error as exc:
                problems.append(f"无法解析：{line[:80]}… → {exc}")
    # 逐表比对
    data_dir = Path(data_dir)
    for table, (db_name, _o, cols) in TABLES.items():
        db = data_dir / db_name
        src_n = 0
        if db.exists():
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            src_rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM {table}").fetchall()  # noqa: S608
            conn.close()
            src_n = len(src_rows)
            dst_rows = mem.execute(
                f"SELECT {', '.join(cols)} FROM {table}").fetchall()  # noqa: S608
            if len(dst_rows) != src_n:
                problems.append(f"{table}：源 {src_n} 行，装载 {len(dst_rows)} 行")
            elif [tuple(r) for r in src_rows] != [tuple(r) for r in dst_rows]:
                problems.append(f"{table}：行内容不一致（顺序或值有出入）")
        if table not in counts_of(sql_text) and src_n:
            problems.append(f"{table}：源库有数据但导出文件里没有 INSERT")
    if loaded != sum(counts_of(sql_text).values()):
        problems.append("INSERT 行数与声明不符")
    mem.close()
    return problems


def counts_of(sql_text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in sql_text.splitlines():
        if line.startswith("INSERT INTO"):
            table = line.split()[2]
            out[table] = out.get(table, 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLite → Postgres 数据导出")
    ap.add_argument("--data-dir", default=None, help="默认用 TRADEPILOT_DATA_DIR")
    ap.add_argument("--out", default=None, help="输出 SQL 文件路径")
    args = ap.parse_args()

    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.config import CONFIG
        data_dir = Path(CONFIG.data_dir)
    out = Path(args.out) if args.out else data_dir / "pg_migrate.sql"

    counts = export(data_dir, out)
    text = out.read_text(encoding="utf-8")
    problems = verify_against_source(data_dir, text)

    total = sum(counts.values())
    print(f"已导出 {total} 行 → {out}")
    for table, n in sorted(counts.items()):
        print(f"  {table}: {n} 行")
    if problems:
        print("自检失败：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("自检通过：数据已重新装载并逐行比对一致。")
    print("装载步骤：psql -f scripts/pg_schema.sql && psql -f " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
