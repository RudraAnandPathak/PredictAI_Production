"""Live checks against a REAL Turso test database. Skipped unless you provide one.

    pip install -r requirements-turso.txt
    PREDICTAI_TEST_TURSO_URL=libsql://predictai-test-<org>.turso.io \
    PREDICTAI_TEST_TURSO_TOKEN=<token for that test database> \
    PREDICTAI_TEST_TURSO_I_UNDERSTAND=this-database-may-be-changed \
    python -m unittest test_p17_turso_live -v

Use a separate, empty database created only for testing (turso db create predictai-test). Never point this at production:
the suite refuses to run if the URL equals TURSO_DATABASE_URL, and it refuses if the database already holds user accounts.
It creates its own scratch tables (named p11_live_*) and drops them afterwards; the full-schema check migrates the database.
"""
import os
import sqlite3
import threading
import unittest
import uuid

URL = os.environ.get("PREDICTAI_TEST_TURSO_URL", "").strip()
TOKEN = os.environ.get("PREDICTAI_TEST_TURSO_TOKEN", "").strip()
CONFIRMED = os.environ.get("PREDICTAI_TEST_TURSO_I_UNDERSTAND") == "this-database-may-be-changed"
PROD = os.environ.get("TURSO_DATABASE_URL", "").strip()

REASON = None
if not (URL and TOKEN):
    REASON = "set PREDICTAI_TEST_TURSO_URL and PREDICTAI_TEST_TURSO_TOKEN to a test Turso database"
elif not CONFIRMED:
    REASON = "set PREDICTAI_TEST_TURSO_I_UNDERSTAND=this-database-may-be-changed"
elif PROD and PROD == URL:
    REASON = "refusing: PREDICTAI_TEST_TURSO_URL is the production TURSO_DATABASE_URL"
else:
    try:
        import libsql  # noqa: F401
    except ImportError:
        REASON = "the libsql package is not installed (pip install -r requirements-turso.txt)"


@unittest.skipIf(REASON is not None, REASON or "")
class LiveTurso(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.db import TursoDatabase
        cls.db = TursoDatabase(URL, TOKEN)
        with cls.db.session() as c:
            has_users = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
            if has_users and c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise unittest.SkipTest("refusing: the test database already holds user accounts")
        cls.t = "p11_live_" + uuid.uuid4().hex[:8]

    @classmethod
    def tearDownClass(cls):
        with cls.db.session() as c:
            for suffix in ("child", "parent"):
                c.execute(f"DROP TABLE IF EXISTS {cls.t}_{suffix}")
        cls.db.close_all()

    def test_1_driver_shape_the_adapter_relies_on(self):
        import libsql
        from app.db.database import _connect_compat
        raw = _connect_compat(libsql, database=URL, auth_token=TOKEN)
        cur = raw.execute("SELECT 1 AS one, 'x' AS two")
        self.assertIsNotNone(cur.description, "cursor.description is needed to read columns by name")
        self.assertEqual([d[0] for d in cur.description], ["one", "two"])
        self.assertEqual(tuple(cur.fetchone()), (1, "x"))
        raw.close()

    def test_2_crud_transactions_constraints_and_foreign_keys(self):
        from app.db import transaction
        p, ch = f"{self.t}_parent", f"{self.t}_child"
        with self.db.session() as c:
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            c.execute(f"CREATE TABLE {p} (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, n INTEGER CHECK (n >= 0))")
            c.execute(f"CREATE TABLE {ch} (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id TEXT NOT NULL REFERENCES {p}(id))")
            c.execute(f"INSERT INTO {p} VALUES (?, ?, ?)", ("a", "A", 1))
            r = c.execute(f"SELECT id, name FROM {p}").fetchone()
            self.assertEqual((r["id"], r["name"], dict(r)), ("a", "A", {"id": "a", "name": "A"}))
            self.assertEqual(c.execute(f"UPDATE {p} SET n = 2 WHERE id = ?", ("a",)).rowcount, 1)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(f"INSERT INTO {p} VALUES ('b', 'A', 1)")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(f"INSERT INTO {ch} (parent_id) VALUES ('ghost')")
            with self.assertRaises(RuntimeError):
                with transaction(c):
                    c.execute(f"INSERT INTO {p} VALUES ('c', 'C', 1)")
                    raise RuntimeError("roll back")
            self.assertIsNone(c.execute(f"SELECT 1 FROM {p} WHERE id = 'c'").fetchone())
            with transaction(c):
                c.execute(f"INSERT INTO {p} VALUES ('d', 'D', 1)")
                c.execute(f"INSERT INTO {ch} (parent_id) VALUES ('d')")
        with self.db.session() as other:                                # committed work is visible from another connection
            self.assertEqual(other.execute(f"SELECT COUNT(*) FROM {ch}").fetchone()[0], 1)

    def test_3_concurrent_writers(self):
        from app.db import transaction
        p = f"{self.t}_parent"
        with self.db.session() as c:
            c.execute(f"CREATE TABLE IF NOT EXISTS {p} (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, n INTEGER CHECK (n >= 0))")
        errors = []

        def work(i):
            try:
                with self.db.session() as c:
                    with transaction(c):
                        c.execute(f"INSERT INTO {p} VALUES (?, ?, 0)", (f"w{i}", f"W{i}"))
            except Exception as e:                                      # noqa: BLE001
                errors.append(repr(e))
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join(120) for t in ts]
        self.assertEqual(errors, [], "concurrent write transactions failed; see docs section 10 (busy errors)")

    def test_4_the_whole_schema_migrates_including_full_text_search(self):
        from app.db import migrate, pending_versions
        with self.db.session() as c:
            migrate(c)
            self.assertEqual(pending_versions(c), [])
            self.assertEqual(migrate(c), [])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'ri_chunks_fts'").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_5_a_wrong_token_is_refused_clearly(self):
        from app.db import TursoDatabase
        from app.db.libsql_adapter import DatabaseAuthError
        bad = "not-a-real-token-" + "x" * 30
        with self.assertRaises((DatabaseAuthError, sqlite3.DatabaseError)) as e:
            TursoDatabase(URL, bad).connect().execute("SELECT 1")
        self.assertNotIn(bad, str(e.exception))


if __name__ == "__main__":
    unittest.main()
