"""Phase 11: Turso (libSQL) support, tested against the libsql stand-in in tests/fake_libsql.py (the real package cannot be installed
here). These tests show the adapter works without sqlite3 conveniences; test_p17_turso_live.py checks real Turso."""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import fake_libsql
from app.core.config import ConfigError, load_settings
from app.db import Database, TursoDatabase, migrate, open_database, pending_versions, transaction
from app.db.libsql_adapter import DatabaseAuthError, DatabaseConfigError, DatabaseUnavailable, Row, is_read_only, map_error
from app.ops.turso_import import run_import

TOKEN = "turso-test-token-" + "z" * 40


class Base(unittest.TestCase):
    def setUp(self):
        fake_libsql.controls.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = f"{self.tmp.name}/remote.db"
        self.url = f"libsql://predictai-{id(self)}.turso.io"
        fake_libsql.register(self.url, self.path, TOKEN)

    def db(self, **kw):
        d = TursoDatabase(self.url, kw.pop("token", TOKEN), module=fake_libsql, sleep=lambda s: None, **kw)
        self.addCleanup(d.close_all)
        return d


class Config(unittest.TestCase):
    def env(self, **kw):
        return {"PREDICTAI_ENV": "development", **kw}

    def test_sqlite_is_the_default_and_turso_needs_url_and_token(self):
        self.assertEqual(load_settings(self.env()).database_backend, "sqlite")
        with self.assertRaises(ConfigError) as e:
            load_settings(self.env(PREDICTAI_DATABASE_BACKEND="turso"))
        self.assertIn("TURSO_DATABASE_URL must be set", str(e.exception))
        self.assertIn("TURSO_AUTH_TOKEN must be set", str(e.exception))
        with self.assertRaises(ConfigError):
            load_settings(self.env(PREDICTAI_DATABASE_BACKEND="postgres"))

    def test_only_encrypted_urls_outside_development_and_no_credentials_in_the_url(self):
        ok = self.env(PREDICTAI_DATABASE_BACKEND="turso", TURSO_DATABASE_URL="libsql://db-org.turso.io", TURSO_AUTH_TOKEN=TOKEN)
        self.assertEqual(load_settings(ok).turso_database_url, "libsql://db-org.turso.io")
        for bad in ("http://db-org.turso.io", "libsql://db-org.turso.io?authToken=abc", "libsql://user:pw@db-org.turso.io"):
            with self.assertRaises(ConfigError, msg=bad):
                load_settings({**ok, "TURSO_DATABASE_URL": bad})
        local = {**ok, "TURSO_DATABASE_URL": "http://127.0.0.1:8080", "TURSO_AUTH_TOKEN": ""}
        self.assertEqual(load_settings(local).database_backend, "turso")                  # a local libSQL server, for development only
        with self.assertRaises(ConfigError):
            load_settings({**local, "PREDICTAI_ENV": "staging", "PREDICTAI_SECRET_KEY": "s" * 40, "PREDICTAI_FRONTEND_BASE_URL": "https://a.example"})

    def test_the_token_never_appears_in_errors_or_repr(self):
        bad_token = "abc def " + "q" * 30
        with self.assertRaises(ConfigError) as e:
            load_settings(self.env(PREDICTAI_DATABASE_BACKEND="turso", TURSO_DATABASE_URL="libsql://db-org.turso.io", TURSO_AUTH_TOKEN=bad_token))
        self.assertNotIn(bad_token, str(e.exception))
        s = load_settings(self.env(PREDICTAI_DATABASE_BACKEND="turso", TURSO_DATABASE_URL="libsql://db-org.turso.io", TURSO_AUTH_TOKEN=TOKEN))
        self.assertNotIn(TOKEN, repr(s))
        self.assertNotIn(TOKEN, repr(open_database(s)))
        self.assertNotIn(TOKEN, json.dumps(open_database(s).describe()))

    def test_sqlite_on_render_in_production_is_refused(self):
        env = {"PREDICTAI_ENV": "production", "PREDICTAI_SECRET_KEY": "s" * 48, "PREDICTAI_FRONTEND_BASE_URL": "https://app.example.com",
               "PREDICTAI_CORS_ALLOWED_ORIGINS": "https://app.example.com", "RENDER": "true"}
        try:
            load_settings(env)
        except ConfigError as e:
            self.assertIn("temporary disk", str(e))
        else:
            self.fail("SQLite on Render in production must be refused")

    def test_open_database_picks_the_backend(self):
        self.assertIsInstance(open_database(load_settings(self.env())), Database)
        s = load_settings(self.env(PREDICTAI_DATABASE_BACKEND="turso", TURSO_DATABASE_URL="libsql://db-org.turso.io", TURSO_AUTH_TOKEN=TOKEN, PREDICTAI_TURSO_MODE="replica"))
        d = open_database(s)
        self.assertIsInstance(d, TursoDatabase)
        self.assertEqual(d.describe(), {"backend": "turso", "mode": "replica", "host": "db-org.turso.io"})


class Adapter(Base):
    def setUp(self):
        super().setUp()
        self.d = self.db()
        self.conn = self.d.connect()
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, n INTEGER CHECK (n >= 0))")
        self.conn.execute("CREATE TABLE child (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id TEXT NOT NULL REFERENCES parent(id), note TEXT)")

    def test_rows_read_by_name_position_keys_and_dict(self):
        self.conn.execute("INSERT INTO parent (id, name, n) VALUES (?, ?, ?)", ("p1", "Alpha", 3))
        r = self.conn.execute("SELECT id, name AS Name, n FROM parent").fetchone()
        self.assertIsInstance(r, Row)
        self.assertEqual((r[0], r["name"], r["NAME"], r["n"]), ("p1", "Alpha", "Alpha", 3))       # case-insensitive like sqlite3.Row
        self.assertEqual(r.keys(), ["id", "Name", "n"])
        self.assertEqual(dict(r), {"id": "p1", "Name": "Alpha", "n": 3})
        self.assertEqual(list(r), ["p1", "Alpha", 3])
        self.assertEqual([x["id"] for x in self.conn.execute("SELECT id FROM parent")], ["p1"])
        self.assertIsNone(self.conn.execute("SELECT id FROM parent WHERE id = ?", ("nope",)).fetchone())
        with self.assertRaises(IndexError):
            r["missing"]

    def test_parameters_are_bound_not_interpolated(self):
        evil = "x'); DROP TABLE parent; --"
        self.conn.execute("INSERT INTO parent (id, name, n) VALUES (?, ?, ?)", ("p1", evil, 0))
        self.assertEqual(self.conn.execute("SELECT name FROM parent").fetchone()[0], evil)

    def test_insert_update_delete_with_rowcount_and_autocommit(self):
        self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p1', 'A', 1)")
        self.assertEqual(self.conn.execute("UPDATE parent SET n = 5 WHERE id = 'p1'").rowcount, 1)
        other = self.d.connect()                                       # a second connection sees it: lone statements were committed
        self.assertEqual(other.execute("SELECT n FROM parent").fetchone()[0], 5)
        self.assertEqual(self.conn.execute("DELETE FROM parent WHERE id = 'p1'").rowcount, 1)
        self.assertIsNone(other.execute("SELECT 1 FROM parent").fetchone())
        other.close()

    def test_transactions_commit_and_roll_back(self):
        with transaction(self.conn):
            self.assertTrue(self.conn.in_transaction)
            self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p1', 'A', 1)")
            self.conn.execute("INSERT INTO child (parent_id, note) VALUES ('p1', 'x')")
        self.assertFalse(self.conn.in_transaction)
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p2', 'B', 1)")
                raise RuntimeError("boom")
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM parent").fetchone()[0], 1)
        with transaction(self.conn):                                   # nested blocks join the outer transaction
            with transaction(self.conn):
                self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p3', 'C', 1)")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM parent").fetchone()[0], 2)

    def test_constraint_failures_raise_the_sqlite3_exceptions_the_app_catches(self):
        self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p1', 'A', 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p2', 'A', 1)")          # UNIQUE
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p3', 'B', -1)")         # CHECK
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO child (parent_id) VALUES ('ghost')")                  # FOREIGN KEY
        with self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("SELECT * FROM no_such_table")
        with self.assertRaises(sqlite3.IntegrityError):                # inside a transaction, and the transaction is still usable for ROLLBACK
            with transaction(self.conn):
                self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p4', 'D', 1)")
                self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p5', 'D', 1)")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM parent WHERE id = 'p4'").fetchone())

    def test_foreign_keys_are_on_and_a_connection_without_them_is_refused(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        fake_libsql.controls.fk_pragma_ignored = True
        with self.assertRaises(DatabaseConfigError):
            self.d.connect()

    def test_a_dropped_connection_is_retried_once_for_reads_and_never_for_writes(self):
        self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p1', 'A', 1)")
        fake_libsql.controls.fail_next_execute = 1
        self.assertEqual(self.conn.execute("SELECT name FROM parent").fetchone()[0], "A")      # reconnected and retried
        self.assertEqual(self.conn.reconnects, 1)
        fake_libsql.controls.fail_next_execute = 1
        with self.assertRaises(DatabaseUnavailable):
            self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p2', 'B', 1)")
        self.assertTrue(self.conn.broken)
        fresh = self.d.connect()
        self.assertIsNone(fresh.execute("SELECT 1 FROM parent WHERE id = 'p2'").fetchone())   # not silently applied twice or half-applied
        fresh.close()

    def test_a_drop_inside_a_transaction_is_reported_and_not_retried(self):
        self.conn.execute("BEGIN IMMEDIATE")
        fake_libsql.controls.fail_next_execute = 1
        with self.assertRaises(DatabaseUnavailable):
            self.conn.execute("SELECT 1")
        self.assertFalse(self.conn.in_transaction)
        self.assertTrue(self.conn.broken)

    def test_with_conn_commits_or_rolls_back_and_does_not_close_like_sqlite3(self):
        with self.conn:                                                 # 31 places in the app use this form
            self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p1', 'A', 1)")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM parent").fetchone()[0], 1)     # still open
        with self.assertRaises(RuntimeError):
            with self.conn:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute("INSERT INTO parent (id, name, n) VALUES ('p2', 'B', 1)")
                raise RuntimeError("undo")
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM parent").fetchone()[0], 1)
        cur = self.conn.execute("SELECT 1")
        self.assertIs(cur.connection, self.conn)

    def test_the_apps_own_trigger_errors_are_integrity_errors(self):
        e = map_error(Exception("rw_sources owner must match its workspace owner"))
        self.assertIsInstance(e, sqlite3.IntegrityError)
        self.assertIsInstance(map_error(Exception("something else entirely")), sqlite3.DatabaseError)

    def test_read_only_detection(self):
        for sql in ("SELECT 1", "  select * from x", "PRAGMA foreign_keys", "PRAGMA table_info(users)", "PRAGMA foreign_key_check"):
            self.assertTrue(is_read_only(sql), sql)
        for sql in ("INSERT INTO x VALUES (1)", "PRAGMA foreign_keys = ON", "WITH a AS (SELECT 1) DELETE FROM x", "BEGIN", "UPDATE x SET y=1"):
            self.assertFalse(is_read_only(sql), sql)


class Connecting(Base):
    def test_a_wrong_token_fails_at_once_with_a_clear_message_and_no_secret(self):
        bad = "wrong-token-" + "w" * 30
        d = self.db(token=bad)
        with self.assertRaises(DatabaseAuthError) as e:
            d.connect()
        self.assertIn("TURSO_AUTH_TOKEN", str(e.exception))
        self.assertNotIn(bad, str(e.exception))
        self.assertEqual(fake_libsql.controls.connects, 0)

    def test_a_temporary_network_failure_is_retried_and_a_lasting_one_is_reported(self):
        fake_libsql.controls.fail_connects = 2
        d = self.db()
        d.connect().close()                                            # third attempt works
        fake_libsql.controls.fail_connects = 5
        with self.assertRaises(DatabaseUnavailable):
            d.connect()

    def test_redaction_of_driver_messages(self):
        e = map_error(Exception(f"401 Unauthorized for token {TOKEN}"), [TOKEN])
        self.assertIsInstance(e, DatabaseAuthError)
        self.assertNotIn(TOKEN, str(e))

    def test_pooled_sessions_reuse_connections_and_never_hand_over_an_open_transaction(self):
        d = self.db()
        with d.session() as c:
            c.execute("CREATE TABLE t (x INTEGER)")
        with d.session() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO t VALUES (1)")                     # the request "forgot" to commit
        self.assertEqual(fake_libsql.controls.connects, 1)
        with d.session() as c:
            self.assertFalse(c.in_transaction)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)   # rolled back on return to the pool
        self.assertEqual(fake_libsql.controls.connects, 1)

    def test_replica_mode_syncs_on_open_and_after_writes(self):
        d = self.db(mode="replica", replica_path=f"{self.tmp.name}/replica/local.db")
        c = d.connect()
        self.assertEqual(fake_libsql.controls.syncs, 1)
        self.assertTrue(fake_libsql.controls.kwargs_seen[-1]["sync_url"])
        c.execute("CREATE TABLE t (x INTEGER)")
        c.execute("INSERT INTO t VALUES (1)")
        with transaction(c):
            c.execute("INSERT INTO t VALUES (2)")
        c.execute("SELECT * FROM t").fetchall()
        self.assertEqual(fake_libsql.controls.syncs, 4)                # open, CREATE, INSERT, COMMIT; not the read
        c.close()


class Migrations(Base):
    def test_every_migration_applies_through_the_adapter_and_runs_once(self):
        d = self.db()
        d.ensure_ready(auto_migrate=True)
        with d.session() as c:
            self.assertEqual(pending_versions(c), [])
            self.assertEqual(migrate(c), [])                           # repeatable: nothing to do the second time
            fts = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'ri_chunks_fts'").fetchone()[0]
            self.assertEqual(fts, 1)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_production_refuses_to_serve_an_unmigrated_database(self):
        from app.db import MigrationError
        with self.assertRaises(MigrationError):
            self.db().ensure_ready(auto_migrate=False)

    def test_two_processes_migrating_at_once_apply_each_migration_once(self):
        for kind in ("sqlite", "turso"):
            with self.subTest(kind=kind):
                path = f"{self.tmp.name}/race-{kind}.db"
                if kind == "turso":
                    url = f"libsql://race-{id(self)}.turso.io"
                    fake_libsql.register(url, path, TOKEN)
                    make = lambda: TursoDatabase(url, TOKEN, module=fake_libsql, sleep=lambda s: None)  # noqa: E731
                else:
                    make = lambda: Database(path)                      # noqa: E731
                results, errors = [], []

                def run():
                    try:
                        with make().session() as c:
                            results.append(migrate(c))
                    except Exception as e:                             # noqa: BLE001
                        errors.append(e)
                threads = [threading.Thread(target=run) for _ in range(4)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(120)
                self.assertEqual(errors, [])
                applied = sorted(v for r in results for v in r)
                self.assertEqual(applied, list(range(1, 17)))           # every migration exactly once across the four
                with make().session() as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 16)

    def test_a_failing_migration_names_itself_rolls_back_and_keeps_earlier_ones(self):
        mig = Path(self.tmp.name) / "migs"
        mig.mkdir()
        (mig / "0001_ok.sql").write_text("CREATE TABLE a (x INTEGER);\n")
        (mig / "0002_bad.sql").write_text("CREATE TABLE b (x INTEGER);\nINSERT INTO nowhere VALUES (1);\n")
        d = self.db()
        with d.session() as c:
            with self.assertRaises(sqlite3.OperationalError) as e:
                migrate(c, mig)
            self.assertIn("0002_bad failed at statement 2", str(e.exception))
            self.assertEqual([r[0] for r in c.execute("SELECT version FROM schema_migrations")], [1])
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'b'").fetchone())


class Import(Base):
    def make_source(self):
        """A local SQLite database with an owner, a person and a project, created through the app's own services."""
        import support
        from app.core.clock import ManualClock
        from app.modules.identity.container import build_services
        from app.modules.identity.context import RequestMeta
        from app.modules.identity.email import MemoryOutbox
        from app.modules.identity.passwords import PasswordHasher
        from app.modules.identity.provisioning import provision_owner
        src = f"{self.tmp.name}/local.db"
        sd = Database(src)
        sd.ensure_ready(auto_migrate=True)
        settings = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_STORAGE_DIR": f"{self.tmp.name}/storage"})
        with sd.session() as c:
            svc = build_services(settings=settings, conn=c, clock=ManualClock(), hasher=PasswordHasher(*settings.password_hash_params), sender=MemoryOutbox(),
                                 meta=RequestMeta(request_id="t", ip="203.0.113.1", device_label="x"))
            owner = provision_owner(svc.ctx, email="p1@example.com", password=support.GOOD_PASSWORD, allow_additional=True)
            svc.auth.register("p2@example.com", support.GOOD_PASSWORD, "Person 2")
            uid = c.execute("SELECT id FROM users WHERE email_normalized = 'p1@example.com'").fetchone()[0]
            c.execute("INSERT INTO projects (id, owner_user_id, name, status, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                      ("pr1", uid, "Project one", "active", "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        return src

    def sha(self, p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def test_dry_run_writes_nothing_and_execute_copies_with_checks_and_a_report(self):
        src = self.make_source()
        before = self.sha(src)
        target = self.db()
        dry = run_import(src, target, report_dir=f"{self.tmp.name}/reports")
        self.assertEqual(dry["result"], "ok", dry)
        self.assertEqual(dry["target_schema"], "would be created by the migrations")
        with target.session() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)         # truly nothing written
        out = run_import(src, target, execute=True, report_dir=f"{self.tmp.name}/reports")
        self.assertEqual(out["result"], "ok", out)
        users = next(t for t in out["tables"] if t["table"] == "users")
        self.assertEqual((users["source_rows"], users["inserted"], users["target_rows_after"]), (2, 2, 2))
        self.assertEqual(out["foreign_key_violations"], 0)
        self.assertEqual(self.sha(src), before)                        # the source was not changed
        self.assertTrue(Path(src).exists())
        md = Path(out["report_files"][1]).read_text()
        self.assertIn("| users | 2 | 2 |", md)
        blob = Path(out["report_files"][0]).read_text() + md
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("Project one", blob)                          # no row content in the report

    def test_people_can_sign_in_after_the_import(self):
        src = self.make_source()
        target = self.db()
        run_import(src, target, execute=True)
        from app.core.clock import ManualClock
        from app.modules.identity.container import build_services
        from app.modules.identity.context import RequestMeta
        from app.modules.identity.email import MemoryOutbox
        from app.modules.identity.passwords import PasswordHasher
        import support
        settings = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_STORAGE_DIR": f"{self.tmp.name}/storage"})
        with target.session() as c:
            s = build_services(settings=settings, conn=c, clock=ManualClock(), hasher=PasswordHasher(*settings.password_hash_params), sender=MemoryOutbox(),
                               meta=RequestMeta(request_id="t", ip="203.0.113.1", device_label="x"))
            out = s.auth.login("p2@example.com", support.GOOD_PASSWORD)
            self.assertTrue(out)

    def test_a_target_with_people_is_refused_and_a_rerun_never_overwrites(self):
        src = self.make_source()
        target = self.db()
        run_import(src, target, execute=True)
        again = run_import(src, target, execute=True)
        self.assertEqual(again["result"], "refused")
        self.assertIn("--allow-existing", again["refused_because"])
        with target.session() as c:
            c.execute("UPDATE projects SET name = 'Changed in Turso' WHERE id = 'pr1'")
        merged = run_import(src, target, execute=True, allow_existing=True)
        projects = next(t for t in merged["tables"] if t["table"] == "projects")
        users = next(t for t in merged["tables"] if t["table"] == "users")
        self.assertEqual((projects["inserted"], projects["conflicts"], projects["conflict_keys"]), (0, 1, [["pr1"]]))
        self.assertEqual((users["inserted"], users["already_present"]), (0, 2))
        self.assertEqual(merged["result"], "incomplete")               # conflicts are reported, not hidden
        with target.session() as c:
            self.assertEqual(c.execute("SELECT name FROM projects WHERE id = 'pr1'").fetchone()[0], "Changed in Turso")

    def test_an_unmigrated_source_is_refused_without_touching_it(self):
        src = f"{self.tmp.name}/old.db"
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, 'x', 'y', 'z')")
        conn.commit()
        conn.close()
        before = self.sha(src)
        out = run_import(src, self.db(), execute=True)
        self.assertEqual(out["result"], "refused")
        self.assertIn("run `python -m app.cli migrate` against the copy", out["refused_because"])
        self.assertEqual(self.sha(src), before)

    def test_the_cli_command_requires_the_turso_backend(self):
        from app.cli import main
        lines = []
        code = main(["import-to-turso", "--source", "x.db"], settings=load_settings({"PREDICTAI_ENV": "test"}), out=lines.append)
        self.assertEqual(code, 2)
        self.assertIn("PREDICTAI_DATABASE_BACKEND=turso", lines[0])


class AppOnTurso(Base):
    """The real app running on the adapter: registration, sign-in, sessions surviving a restart, projects, owner and special access, usage."""

    def test_accounts_projects_and_owner_access_survive_an_application_restart(self):
        import support
        from fastapi.testclient import TestClient
        from app.main import create_app
        settings = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_STORAGE_DIR": f"{self.tmp.name}/storage", "PREDICTAI_EMAIL_OUTBOX_DIR": f"{self.tmp.name}/out"})
        d = self.db()
        d.ensure_ready(auto_migrate=True)
        from app.core.clock import ManualClock
        from app.modules.identity.email import MemoryOutbox
        from app.modules.identity.provisioning import provision_owner
        from app.modules.identity.container import build_services
        from app.modules.identity.context import RequestMeta
        from app.modules.identity.passwords import PasswordHasher
        clock, outbox = ManualClock(), MemoryOutbox()
        with d.session() as c:
            ctx = build_services(settings=settings, conn=c, clock=clock, hasher=PasswordHasher(*settings.password_hash_params), sender=outbox, meta=RequestMeta(request_id="t")).ctx
            provision_owner(ctx, email="owner@example.com", password=support.GOOD_PASSWORD, allow_additional=True)

        def client(db):
            app = create_app(settings, clock=clock, sender=outbox, database=db)
            return app, TestClient(app, raise_server_exceptions=False)
        origin = {"Origin": settings.frontend_base_url}
        app, c = client(d)
        r = c.post("/api/v1/auth/register", json={"email": "ana@example.com", "password": support.GOOD_PASSWORD, "display_name": "Ana"}, headers=origin)
        self.assertIn(r.status_code, (200, 201, 202), r.text)
        r = c.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": support.GOOD_PASSWORD}, headers=origin)
        self.assertEqual(r.status_code, 200, r.text)
        csrf = r.json()["data"]["csrf_token"]
        r = c.post("/api/v1/projects", json={"name": "Survives restarts"}, headers={**origin, "X-CSRF-Token": csrf})
        self.assertIn(r.status_code, (200, 201), r.text)
        self.assertEqual(c.get("/api/v1/owner/users").status_code, 403)              # ordinary person: no owner access
        self.assertEqual(c.get("/api/v1/health/ready").json()["data"]["checks"]["database_backend"], "turso")
        cookies = dict(c.cookies)
        runner = getattr(app.state, "ml_runner", None)
        if runner:
            runner.shutdown()
        d.close_all()
        d2 = self.db()                                                  # "restart": a new process, new pool, same Turso database
        app2, c2 = client(d2)
        c2.cookies.update(cookies)
        names = [p["name"] for p in c2.get("/api/v1/projects").json()["data"]["items"]]
        self.assertIn("Survives restarts", names)                       # the session and the project both survived
        owner = TestClient(app2, raise_server_exceptions=False)
        r = owner.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": support.GOOD_PASSWORD}, headers=origin)
        self.assertEqual(r.status_code, 200)
        owner_csrf = r.json()["data"]["csrf_token"]
        self.assertEqual(owner.get("/api/v1/owner/users").status_code, 200)           # owner access intact after the restart
        code = owner.post("/api/v1/owner/access-codes", json={"label": "for Ana", "expires_in_days": 7}, headers={**origin, "X-CSRF-Token": owner_csrf})
        self.assertEqual(code.status_code, 201, code.text)
        r = c2.post("/api/v1/access-codes/activate", json={"code": code.json()["data"]["code"]}, headers={**origin, "X-CSRF-Token": csrf})
        self.assertEqual(r.json()["data"]["tier"], "special")                          # special access is stored and granted
        self.assertIn("uploads.unlimited", c2.get("/api/v1/me").json()["data"]["permissions"])
        runner = getattr(app2.state, "ml_runner", None)
        if runner:
            runner.shutdown()

    def test_concurrent_requests_each_get_their_own_connection(self):
        d = self.db()
        d.ensure_ready(auto_migrate=True)
        with d.session() as c:
            c.execute("CREATE TABLE hits (n INTEGER)")
        errors = []

        def work():
            try:
                for _ in range(20):
                    with d.session() as c:
                        with transaction(c):
                            c.execute("INSERT INTO hits VALUES (1)")
            except Exception as e:                                     # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=work) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        self.assertEqual(errors, [])
        with d.session() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM hits").fetchone()[0], 120)

    def test_readiness_fails_cleanly_when_turso_is_unreachable(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        settings = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_STORAGE_DIR": f"{self.tmp.name}/storage"})
        fake_libsql.controls.fail_connects = 100
        app = create_app(settings, database=self.db())
        r = TestClient(app, raise_server_exceptions=False).get("/api/v1/health/ready")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(TOKEN, r.text)
        self.assertNotIn("turso.io", r.text)


if __name__ == "__main__":
    unittest.main()
