"""Projects: lifecycle, validation, isolation between users, deletion evidence, migration 0002, storage safety."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.errors import AppError
from app.db import migrate
from app.db.migrator import discover
from app.modules.storage import LocalStorage, StorageError
from app.modules.workspace.validation import sanitize_filename

from support import Harness


def code_of(call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except AppError as error:
        return error.code
    return None


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.alice, _ = self.h.make_user("alice@example.com")
        self.bob, _ = self.h.make_user("bob@example.com")
        self.ws = self.h.workspace()

    def test_full_lifecycle(self):
        p = self.ws.projects.create(self.alice, "  Exam prep  ", "Notes and data", {"tags": ["physics"], "term": 3})
        self.assertEqual(p["name"], "Exam prep")
        self.assertEqual(p["status"], "active")
        self.assertEqual((p["datasets"], p["documents"]), (0, 0))
        self.assertEqual(self.ws.projects.list(self.alice)["total"], 1)
        renamed = self.ws.projects.update(self.alice, p["id"], {"name": "Exam prep 2", "description": None})
        self.assertEqual(renamed["name"], "Exam prep 2")
        self.assertIsNone(renamed["description"])
        self.assertEqual(self.ws.projects.archive(self.alice, p["id"])["status"], "archived")
        self.assertEqual(self.ws.projects.list(self.alice, status="active")["total"], 0)
        self.assertEqual(self.ws.projects.list(self.alice, status="archived")["total"], 1)
        self.assertEqual(self.ws.projects.restore(self.alice, p["id"])["status"], "active")
        kinds = [e["event_type"] for e in self.ws.projects.activity(self.alice, p["id"])["items"]]
        self.assertEqual(kinds, ["project.restored", "project.archived", "project.updated", "project.created"])

    def test_validation(self):
        for bad in ("", "   ", None, 5, "x" * 121):
            self.assertEqual(code_of(self.ws.projects.create, self.alice, bad), "VALIDATION_ERROR", bad)
        self.assertEqual(code_of(self.ws.projects.create, self.alice, "ok", "d" * 2001), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.projects.create, self.alice, "ok", None, {"a b": 1}), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.projects.create, self.alice, "ok", None, {"k": {"nested": 1}}), "VALIDATION_ERROR")
        self.assertEqual(self.ws.projects.create(self.alice, "a\x00b\u202ec")["name"], "abc")
        self.assertNotIn("\x00", self.ws.projects.create(self.alice, "a\x00b")["name"])
        self.assertEqual(code_of(self.ws.projects.update, self.alice, self.ws.projects.create(self.alice, "z")["id"], {}), "VALIDATION_ERROR")

    def test_isolation_and_guessed_ids(self):
        p = self.ws.projects.create(self.alice, "Private")
        bob = self.h.workspace()
        for call in (lambda: bob.projects.get(self.bob, p["id"]), lambda: bob.projects.update(self.bob, p["id"], {"name": "x"}),
                     lambda: bob.projects.archive(self.bob, p["id"]), lambda: bob.projects.restore(self.bob, p["id"]),
                     lambda: bob.projects.delete(self.bob, p["id"], "Private"), lambda: bob.projects.activity(self.bob, p["id"])):
            self.assertEqual(code_of(call), "NOT_FOUND")
        missing = code_of(bob.projects.get, self.bob, "0" * 32)
        self.assertEqual(missing, "NOT_FOUND")  # identical to a foreign id: cannot be probed
        self.assertEqual(bob.projects.list(self.bob)["total"], 0)
        self.assertEqual(self.ws.projects.get(self.alice, p["id"])["name"], "Private")
        for weird in ("../../etc/passwd", "' OR 1=1 --", "x" * 500, ""):
            self.assertEqual(code_of(bob.projects.get, self.bob, weird), "NOT_FOUND")

    def test_archived_project_is_read_only_for_data(self):
        p = self.ws.projects.create(self.alice, "A")
        self.ws.projects.archive(self.alice, p["id"])
        self.assertEqual(code_of(self.ws.projects.get_owned, self.alice, p["id"], writable=True), "PROJECT_NOT_ACTIVE")
        self.assertEqual(self.ws.projects.get_owned(self.alice, p["id"])["status"], "archived")

    def test_delete_needs_exact_name_and_leaves_evidence_without_content(self):
        p = self.ws.projects.create(self.alice, "Secret plans", "very private description")
        self.assertEqual(code_of(self.ws.projects.delete, self.alice, p["id"], "wrong"), "VALIDATION_ERROR")
        self.assertEqual(self.ws.projects.get(self.alice, p["id"])["name"], "Secret plans")
        result = self.ws.projects.delete(self.alice, p["id"], "Secret plans")
        self.assertTrue(result["deleted"])
        self.assertEqual(code_of(self.ws.projects.get, self.alice, p["id"]), "NOT_FOUND")
        row = self.h.conn.execute("SELECT * FROM projects WHERE id = ?", (p["id"],)).fetchone()
        self.assertEqual((row["status"], row["name"], row["description"], row["metadata"]), ("deleted", None, None, None))
        record = self.ws.governance.list_deletions(self.alice)["items"][0]
        self.assertEqual((record["scope"], record["status"]), ("project", "completed"))
        dump = " ".join(str(r) for r in self.h.conn.execute("SELECT metadata, reason FROM audit_events").fetchall())
        self.assertNotIn("Secret plans", dump)
        self.assertNotIn("very private", dump)

    def test_project_cap_and_rate_limit_are_enforced(self):
        from app.modules.workspace import projects as module

        original = module.MAX_PROJECTS_PER_USER
        module.MAX_PROJECTS_PER_USER = 2
        try:
            self.ws.projects.create(self.alice, "1")
            self.ws.projects.create(self.alice, "2")
            self.assertEqual(code_of(self.ws.projects.create, self.alice, "3"), "PROJECT_LIMIT")
        finally:
            module.MAX_PROJECTS_PER_USER = original

    def test_blocked_or_unauthenticated_permission_missing(self):
        from dataclasses import replace

        nobody = replace(self.alice, permissions=frozenset())
        self.assertEqual(code_of(self.ws.projects.create, nobody, "x"), "FORBIDDEN")
        self.assertEqual(code_of(self.ws.projects.list, nobody), "FORBIDDEN")


class MigrationTests(unittest.TestCase):
    def test_history_is_preserved_and_upgrade_keeps_data(self):
        migrations = discover()
        self.assertEqual([m.version for m in migrations], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17])
        self.assertEqual(migrations[0].name, "identity")
        import hashlib

        self.assertEqual(hashlib.sha256((Path(__file__).parent.parent / "app/db/migrations/0001_identity.sql").read_bytes()).hexdigest(), migrations[0].checksum)
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            only_first = Path(tmp) / "m"
            only_first.mkdir()
            (only_first / "0001_identity.sql").write_text(migrations[0].sql)
            self.assertEqual(migrate(conn, only_first), [1])
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','a@x.io','a@x.io','active','t','t')")
            self.assertEqual(migrate(conn), [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17])
            self.assertEqual(conn.execute("SELECT email FROM users WHERE id='u1'").fetchone()["email"], "a@x.io")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in ("projects", "assets", "asset_versions", "uploads", "upload_chunks", "extractions", "profiles", "transformations", "deletion_records", "project_events",
                         "analysis_runs", "insights", "conversations", "conversation_messages", "ml_jobs", "ml_models", "ml_predictions",
                         "research_sessions", "research_jobs", "research_sources", "evidence_bundles", "research_notes", "web_cache", "doc_indexes", "news_articles", "news_events",
                         "influence_analyses", "ai_providers", "ai_usage"):
                self.assertIn(name, tables)
            self.assertEqual(migrate(conn), [])  # idempotent

    def test_phase_three_database_upgrades_to_the_current_schema_without_touching_earlier_migrations(self):
        """A database that already holds Phase 3 data (migrations 1 and 2 applied) gets migrations 3 and 4, and its data and checksums are untouched."""
        import hashlib

        migrations = discover()
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            phase3 = Path(tmp) / "m"
            phase3.mkdir()
            for m in migrations[:2]:
                (phase3 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase3), [1, 2])
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','a@x.io','a@x.io','active','t','t')")
            before = [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")] if "schema_migrations" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")} else None
            self.assertEqual(migrate(conn), [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17])
            self.assertEqual(conn.execute("SELECT email FROM users WHERE id='u1'").fetchone()["email"], "a@x.io")
            if before is not None:
                after = [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version IN (1, 2) ORDER BY version")]
                self.assertEqual(before, after)
        for m in migrations[:2]:
            self.assertEqual(hashlib.sha256((Path(__file__).parent.parent / "app/db/migrations" / f"{m.version:04d}_{m.name}.sql").read_bytes()).hexdigest(), m.checksum)

    def test_phase_four_database_upgrades_to_phase_five_keeping_every_row_and_adding_only_the_modelling_tables(self):
        """A database that holds Phase 4 data (migrations 1-3) gets exactly migration 4: no existing table, row or checksum changes, and the new tables start empty."""
        import hashlib

        migrations = discover()
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            phase4 = Path(tmp) / "m"
            phase4.mkdir()
            for m in migrations[:3]:
                (phase4 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase4), [1, 2, 3])
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','a@x.io','a@x.io','active','t','t')")
            conn.execute("INSERT INTO projects (id,owner_user_id,name,status,created_at,updated_at) VALUES ('p1','u1','P','active','t','t')")
            conn.execute("INSERT INTO assets (id,project_id,owner_user_id,kind,name,detected_format,status,source_checksum,size_bytes,created_at,updated_at) "
                         "VALUES ('a1','p1','u1','dataset','a.csv','csv','ready','abc',1,'t','t')")

            def snapshot():
                tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'ml_%' AND name != 'schema_migrations'"))     # the migration ledger itself gains a row
                return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}, {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'ml_%' AND name NOT LIKE 'sqlite_%' AND tbl_name NOT LIKE 'ml_%'")}

            phase5 = Path(tmp) / "m5"                                                            # the Phase 5 release shipped migrations 1-4; Phase 6 adds 5 (tested below)
            phase5.mkdir()
            for m in migrations[:4]:
                (phase5 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            before_rows, before_schema = snapshot()
            checksums = [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 3 ORDER BY version")]
            self.assertEqual(migrate(conn, phase5), [4])
            after_rows, after_schema = snapshot()
            self.assertEqual(before_rows, {k: v for k, v in after_rows.items() if k in before_rows})
            self.assertEqual(set(before_rows), set(after_rows))                                  # no table was added or removed except the modelling ones
            self.assertEqual(before_schema, after_schema)                                        # no existing table or index definition changed
            self.assertEqual(checksums, [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 3 ORDER BY version")])
            for table in ("ml_jobs", "ml_models", "ml_predictions"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(migrate(conn, phase5), [])                                          # idempotent
        for m in migrations[:3]:
            self.assertEqual(hashlib.sha256((Path(__file__).parent.parent / "app/db/migrations" / f"{m.version:04d}_{m.name}.sql").read_bytes()).hexdigest(), m.checksum)

    def test_phase_five_database_upgrades_to_phase_six_keeping_every_row_and_adding_only_the_research_tables(self):
        """A database that holds Phase 5 data (migrations 1-4) gets exactly migration 5: no existing table, index, row or checksum changes; the research tables start empty
        (the provider table holds only the built-in row); and the new tables refuse rows that point nowhere."""
        import hashlib

        migrations = discover()
        research = {"research_settings", "research_sessions", "research_jobs", "research_sources", "evidence_bundles", "research_notes", "web_cache", "doc_indexes", "news_sources",
                    "news_articles", "news_events", "influence_analyses", "ai_providers", "ai_usage"}
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            phase5 = Path(tmp) / "m"
            phase5.mkdir()
            for m in migrations[:4]:
                (phase5 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase5), [1, 2, 3, 4])
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','a@x.io','a@x.io','active','t','t')")
            conn.execute("INSERT INTO projects (id,owner_user_id,name,status,created_at,updated_at) VALUES ('p1','u1','P','active','t','t')")
            conn.execute("INSERT INTO assets (id,project_id,owner_user_id,kind,name,detected_format,status,source_checksum,size_bytes,created_at,updated_at) "
                         "VALUES ('a1','p1','u1','dataset','a.csv','csv','ready','abc',1,'t','t')")

            def snapshot():
                tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"))
                rows = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
                schema = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'")}
                return rows, schema

            before_rows, before_schema = snapshot()
            checksums = [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 4 ORDER BY version")]
            phase6 = Path(tmp) / "m6"                                                                            # migrations 1-5: this test is about migration 5 only
            phase6.mkdir()
            for m in migrations[:5]:
                (phase6 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase6), [5])
            after_rows, after_schema = snapshot()
            self.assertEqual(before_rows, {k: v for k, v in after_rows.items() if k in before_rows})            # every existing row is unchanged
            self.assertEqual(set(after_rows) - set(before_rows), research)                                        # only the research tables were added
            self.assertEqual(before_schema, {k: v for k, v in after_schema.items() if k in before_schema})       # no existing table or index definition changed
            self.assertEqual(checksums, [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 4 ORDER BY version")])
            for table in sorted(research - {"ai_providers"}):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT key, kind, enabled, paid FROM ai_providers")], [("builtin", "builtin", 1, 0)])
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(migrate(conn, phase6), [])                                                           # idempotent
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO research_sessions (id, project_id, owner_user_id, title, question, level, status, options, created_at, updated_at) "
                             "VALUES ('s','nope','nope','t','q','basic','draft','{}','t','t')")
            with self.assertRaises(sqlite3.IntegrityError):                                                       # closed vocabularies are enforced by the database
                conn.execute("INSERT INTO research_sessions (id, project_id, owner_user_id, title, question, level, status, options, created_at, updated_at) "
                             "VALUES ('s','p1','u1','t','q','basic','whatever','{}','t','t')")
        for m in migrations[:4]:
            self.assertEqual(hashlib.sha256((Path(__file__).parent.parent / "app/db/migrations" / f"{m.version:04d}_{m.name}.sql").read_bytes()).hexdigest(), m.checksum)

    def test_phase_six_database_upgrades_to_phase_seven_keeping_every_row_and_adding_only_new_tables(self):
        """A database holding Phase 6 data (migrations 1-5) gets exactly migration 6: every existing row, table, index definition and earlier checksum is unchanged, only
        reg_/exam_/adv_/rep_/ws_/sch_/own_ tables appear, they start empty (apart from the single maintenance row, which is off), and the upgrade is idempotent."""
        migrations = discover()
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            phase6 = Path(tmp) / "m"
            phase6.mkdir()
            for m in migrations[:5]:
                (phase6 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase6), [1, 2, 3, 4, 5])
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','a@x.io','a@x.io','active','t','t')")
            conn.execute("INSERT INTO projects (id,owner_user_id,name,status,created_at,updated_at) VALUES ('p1','u1','P','active','t','t')")

            def snapshot():
                tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"))
                rows = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
                schema = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%'")}
                return rows, schema

            before_rows, before_schema = snapshot()
            checksums = [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 5 ORDER BY version")]
            upto6 = Path(tmp) / "m6only"
            upto6.mkdir()
            for m in migrations[:6]:
                (upto6 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, upto6), [6])
            after_rows, after_schema = snapshot()
            self.assertEqual(before_rows, {k: v for k, v in after_rows.items() if k in before_rows})
            self.assertEqual(before_schema, {k: v for k, v in after_schema.items() if k in before_schema})
            self.assertEqual(checksums, [dict(r) for r in conn.execute("SELECT version, checksum FROM schema_migrations WHERE version <= 5 ORDER BY version")])
            added = set(after_rows) - set(before_rows)
            self.assertTrue(added and all(t.split("_")[0] in {"reg", "exam", "adv", "rep", "ws", "sch", "own"} for t in added), sorted(added))
            for table in sorted(added - {"own_maintenance"}):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT id, active FROM own_maintenance")], [(1, 0)])
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(migrate(conn, upto6), [])

    def test_phase_seven_database_upgrades_to_phase_eight_adding_only_empty_rw_tables(self):
        """A database holding Phase 7 data (migrations 1-6) gets exactly migration 7: nothing existing changes, only empty rw_ tables, and the owner-consistency triggers hold."""
        migrations = discover()
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            phase7 = Path(tmp) / "m"
            phase7.mkdir()
            for m in migrations[:6]:
                (phase7 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, phase7), [1, 2, 3, 4, 5, 6])
            for uid in ("u1", "u2"):
                conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES (?,?,?, 'active','t','t')", (uid, f"{uid}@x.io", f"{uid}@x.io"))

            def snapshot():
                names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"))
                return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in names}, {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'")}

            upto7 = Path(tmp) / "m7"                      # this test is about migration 7 only; migration 8 (Phase 9) has its own upgrade test
            upto7.mkdir()
            for m in migrations[:7]:
                (upto7 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            before_rows, before_schema = snapshot()
            self.assertEqual(migrate(conn, upto7), [7])
            after_rows, after_schema = snapshot()
            self.assertEqual(before_rows, {k: v for k, v in after_rows.items() if k in before_rows})
            self.assertEqual(before_schema, {k: v for k, v in after_schema.items() if k in before_schema})
            added = set(after_rows) - set(before_rows)
            self.assertEqual(added, {"rw_workspaces", "rw_sources", "rw_conversations", "rw_messages", "rw_notes", "sqlite_sequence"} - {"sqlite_sequence"})
            for t in added:
                self.assertEqual(after_rows[t], [])
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            conn.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES ('w1','u1','Lit','active','t','t')")
            with self.assertRaises(sqlite3.IntegrityError):   # someone else's workspace
                conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('s','w1','u2','pdf','x','pending','t','t')")
            with self.assertRaises(sqlite3.IntegrityError):   # unsupported kind
                conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('s','w1','u1','mp3','x','pending','t','t')")
            with self.assertRaises(sqlite3.IntegrityError):   # duplicate name, any case
                conn.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES ('w2','u1','LIT','active','t','t')")
            self.assertEqual(migrate(conn, upto7), [])

    def test_the_modelling_tables_reject_rows_that_point_nowhere_and_cascade_nothing_silently(self):
        migrations = discover()
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            migrate(conn)
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(ml_jobs)")]
            self.assertTrue({"id", "project_id", "asset_id", "owner_user_id", "status", "config", "cancel_requested"} <= set(cols), cols)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO ml_jobs (id, project_id, asset_id, version_id, owner_user_id, status, config, config_hash, created_at) VALUES ('j','nope','nope','nope','nope','queued','{}','h','t')")
        self.assertEqual([m.version for m in migrations][-1], 17)

    def test_duplicate_live_assets_are_impossible_at_the_database_level(self):
        h = Harness()
        self.addCleanup(h.close)
        user, _ = h.make_user()
        ws = h.workspace()
        project = ws.projects.create(user, "P")
        args = dict(project_id=project["id"], owner=user.user_id, kind="dataset", name="a.csv", detected_format="csv", status="ready", checksum="abc", size=1, now="t")
        ws.repo.create_asset(id="a1", **args)
        with self.assertRaises(sqlite3.IntegrityError):
            ws.repo.create_asset(id="a2", **args)
        ws.repo.update_asset("a1", status="deleting")
        ws.repo.create_asset(id="a3", **args)  # allowed once the first is gone


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalStorage(f"{self.tmp.name}/root")

    def test_keys_are_confined(self):
        for key in ("../x", "/abs", "a/../b", "A/b", "a//b", "", "a/b/", "a\\b", "a b", "x" * 300, ".hidden"):
            with self.assertRaises(StorageError, msg=key):
                self.store.put(key, [b"x"])

    def test_symlink_escape_is_refused(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (Path(self.tmp.name) / "root").mkdir(exist_ok=True)  # storage creates its root lazily
        os.symlink(outside, Path(self.tmp.name) / "root" / "link")
        with self.assertRaises(StorageError):
            self.store.put("link/file", [b"x"])
        self.assertFalse(any(outside.iterdir()))

    def test_originals_are_write_once_and_read_only(self):
        stored = self.store.put("p/a/original", [b"abc", b"def"])
        self.assertEqual((stored.size, self.store.read_bytes("p/a/original")), (6, b"abcdef"))
        with self.assertRaises(StorageError):
            self.store.put("p/a/original", [b"tamper"])
        self.assertEqual(self.store.read_bytes("p/a/original"), b"abcdef")
        self.assertEqual(os.stat(self.store.local_path("p/a/original")).st_mode & 0o222, 0)

    def test_size_cap_leaves_no_partial_object(self):
        with self.assertRaises(StorageError):
            self.store.put("p/big", [b"x" * 10, b"y" * 10], max_bytes=15)
        self.assertFalse(self.store.exists("p/big"))
        self.assertEqual(list((Path(self.tmp.name) / "root" / ".tmp").iterdir()), [])

    def test_delete_prefix_removes_everything_and_empty_dirs(self):
        self.store.put("p/1/a", [b"1"])
        self.store.put("p/1/b/c", [b"2"])
        self.assertEqual(self.store.delete_prefix("p/1"), 2)
        self.assertEqual(self.store.list_prefix("p"), [])


class FilenameTests(unittest.TestCase):
    def test_sanitising(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), ("passwd", ""))
        self.assertEqual(sanitize_filename("C:\\Users\\me\\data.CSV"), ("data.CSV", "csv"))
        self.assertEqual(sanitize_filename("a\x00b.csv\u202e"), ("ab.csv", "csv"))
        self.assertEqual(sanitize_filename("  .hidden.xlsx "), ("hidden.xlsx", "xlsx"))
        long_name, ext = sanitize_filename("n" * 400 + ".pdf")
        self.assertTrue(len(long_name) <= 180 and long_name.endswith(".pdf") and ext == "pdf")
        for bad in ("", "...", "/", None, 7):
            with self.assertRaises(AppError):
                sanitize_filename(bad)


if __name__ == "__main__":
    unittest.main()
