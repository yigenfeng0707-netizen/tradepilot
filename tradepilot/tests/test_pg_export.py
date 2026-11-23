# -*- coding: utf-8 -*-
"""SQLite → Postgres 导出工具的回归测试（M3-①）。

守的红线：
1. **数据完整性在装载侧闭环**：生成的 INSERT 是标准 SQL，必须能被重新
   装载并逐行逐列比对一致——迁移脚本迁移的是"真实的用户与存证"，
   丢一行、错一个字符都是生产事故；
2. **转义必须可靠**：payload 是任意 JSON 文本，单引号、换行都会出现；
3. **序列必须拨到位**：provenance.id 是 IDENTITY，导出带显式 id，
   没有 setval 的话新写入撞主键；
4. **只读打开源库**：迁移脚本绝不允许写源库。
"""
import dataclasses
import tempfile
import unittest
from pathlib import Path

from app.config import CONFIG  # noqa: E402
from app import auth, provenance  # noqa: E402

sys_path = str(Path(__file__).resolve().parents[1])
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

from scripts.export_sqlite_to_pg import (  # noqa: E402
    counts_of, export, verify_against_source)

_tmpdir = tempfile.mkdtemp(prefix="tradepilot-pgexport-")
# 注意：不在模块级替换 auth/provenance 的 CONFIG——其他测试文件也在模块级
# 替换，收集顺序决定谁覆盖谁，谁后导入谁说了算。改成 setUpModule 运行时
# 接管、tearDownModule 恢复到接管前状态，与导入顺序解耦。
_taken: dict[str, object] = {}


def setUpModule():
    _taken["auth"] = auth.CONFIG
    _taken["provenance"] = provenance.CONFIG
    # db_path 必须一并重置：CONFIG.db_file() 会把首次解析的库路径缓存进
    # 实例，dataclasses.replace 只换 data_dir 的话，provenance 会继续写
    # "原来的"库。
    auth.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
    provenance.CONFIG = dataclasses.replace(CONFIG, data_dir=_tmpdir, db_path="")
    auth.init_db()
    provenance.record("page", "https://example.com/a", {"price": 9.9}, "raw-a")
    provenance.record("item", "https://example.com/b",
                      {"title": "含'单引号'与\n换行"}, "raw-b")
    provenance.record("price", "https://example.com/c", {"v": None})
    reg = auth.register("pgx@example.com", "password123", "迁移公司")
    assert reg.get("ok"), reg


def tearDownModule():
    auth.CONFIG = _taken["auth"]
    provenance.CONFIG = _taken["provenance"]


def _count(table: str) -> int:
    """源库某表当前行数（只读连接）。"""
    import sqlite3
    db = {"provenance": "provenance.db"}.get(table, "auth.db")
    path = Path(_tmpdir) / db
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


class TestExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(_tmpdir) / "pg_migrate.sql"
        # 全量运行时其他测试模块的 provenance.record 也会写进"当前"库
        # （CONFIG 是模块级全局），所以行数断言用 setUpClass 时刻的基线，
        # 不写死数字。
        cls.baseline = {t: _count(t) for t in
                        ("provenance", "users", "tenants", "refresh_tokens")}
        cls.counts = export(_tmpdir, cls.out)
        cls.text = cls.out.read_text(encoding="utf-8")

    def test_all_rows_exported(self):
        self.assertGreaterEqual(self.counts["provenance"], 3)   # setUpModule 造的
        self.assertGreaterEqual(self.counts["users"], 1)
        self.assertEqual(self.counts, self.baseline)
        # counts_of 只统计有 INSERT 行的表——0 行的表本来就没有语句
        self.assertEqual(counts_of(self.text),
                         {k: v for k, v in self.counts.items() if v})

    def test_verify_against_source_passes(self):
        """核心闭环：重新装载并与源库逐行比对必须零问题。"""
        self.assertEqual(verify_against_source(_tmpdir, self.text), [])

    def test_escapes_survive_roundtrip(self):
        """payload 里的单引号原样回来，不丢不裂。

        payload 是 json.dumps 的产物，真实换行会被转义成字面 \\n——
        所以换行的转义在 _quote 单元测试里覆盖（INSERT 字面量里
        出现真实换行也是合法 SQL，这里用最小用例验证）。
        """
        import sqlite3

        from scripts.export_sqlite_to_pg import _quote
        # _quote 对换行 + 单引号混合输入：SQLite 重新装载后必须逐字符还原
        tricky = "a'b\nc"
        mem = sqlite3.connect(":memory:")
        mem.execute("CREATE TABLE t (v)")
        mem.execute(f"INSERT INTO t (v) VALUES ({_quote(tricky)})")
        self.assertEqual(mem.execute("SELECT v FROM t").fetchone()[0], tricky)
        mem.close()

        # 闭环：真实库里的单引号 payload 装载后原样
        mem = sqlite3.connect(":memory:")
        mem.execute("CREATE TABLE provenance (id, kind, url, fetched_at,"
                    " content_hash, payload)")
        for line in self.text.splitlines():
            if line.startswith("INSERT INTO provenance"):
                mem.execute(line)
        payloads = [r[0] for r in mem.execute("SELECT payload FROM provenance")]
        self.assertTrue(any("'单引号'" in p for p in payloads))

    def test_setval_present(self):
        self.assertIn("SELECT setval(pg_get_serial_sequence('provenance', 'id')",
                      self.text)

    def test_readonly_no_writes(self):
        """迁移不写源库：导出后源库行数与导出前完全一致。"""
        self.assertEqual({t: _count(t) for t in
                          ("provenance", "users", "tenants", "refresh_tokens")},
                         self.baseline)

    def test_idempotent_output(self):
        out2 = Path(_tmpdir) / "pg_migrate2.sql"
        export(_tmpdir, out2)
        self.assertEqual(out2.read_text(encoding="utf-8"), self.text)

    def test_missing_db_exported_as_empty(self):
        """provenance.db 不存在不崩溃，按 0 行导出并跳过 setval。"""
        empty_dir = tempfile.mkdtemp(prefix="tradepilot-pgexport-empty-")
        auth.init_db()   # auth 用自己的 CONFIG，无关紧要
        out = Path(empty_dir) / "pg.sql"
        counts = export(empty_dir, out)
        self.assertEqual(counts["provenance"], 0)
        self.assertIn("跳过 setval", out.read_text(encoding="utf-8"))
        self.assertEqual(verify_against_source(empty_dir,
                                               out.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
