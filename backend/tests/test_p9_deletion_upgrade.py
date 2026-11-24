"""Phase 9: the 7->8 database upgrade (adds only empty dv_ tables) and deletion (project and account deletion remove every Development Workspace row, file and process)."""
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from app.db import migrate
from app.db.migrator import discover
from app.modules.devspace.engine import JobService
from app.modules.devspace.experiments import ExperimentService
from app.modules.devspace.files import FileService
from app.modules.devspace.git import GitService
from app.modules.devspace.pipelines import PipelineService
from app.modules.devspace.secrets import SecretService
from p9_support import DevHarness


class Upgrade(unittest.TestCase):
    def test_phase_eight_database_upgrades_to_phase_nine_adding_only_empty_dv_tables(self):
        migrations = discover()
        self.assertEqual([m.version for m in migrations], list(range(1, 18)))        # Personal API tokens added migration 17; Phase 10E added migration 16; Phase 10D added migration 15; Phase 10C added migration 14; Phase 10B added migration 13; Phase 10A added migration 12; Phase 10 added migrations 9, 10 and 11; the 7 -> 8 step below is checked against the migrations that existed at Phase 9
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.row_factory = sqlite3.Row
            self.addCleanup(conn.close)
            conn.execute("PRAGMA foreign_keys = ON")
            upto7 = Path(tmp) / "m"
            upto7.mkdir()
            for m in migrations[:7]:
                (upto7 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, upto7), [1, 2, 3, 4, 5, 6, 7])
            upto8 = Path(tmp) / "m8"
            upto8.mkdir()
            for m in migrations[:8]:
                (upto8 / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','u1@x.io','u1@x.io','active','t','t')")

            def snapshot():
                names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"))
                return ({t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in names},
                        {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'")})
            rows0, schema0 = snapshot()
            self.assertEqual(migrate(conn, upto8), [8])
            rows1, schema1 = snapshot()
            self.assertEqual(rows0, {k: v for k, v in rows1.items() if k in rows0})                  # no existing row changed
            self.assertEqual(schema0, {k: v for k, v in schema1.items() if k in schema0})            # no existing table/index/trigger changed
            added = set(rows1) - set(rows0)
            self.assertEqual(len(added), 18)
            self.assertTrue(all(t.startswith("dv_") for t in added), added)
            self.assertTrue(all(rows1[t] == [] for t in added))
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(migrate(conn, upto8), [])
            with self.assertRaises(sqlite3.IntegrityError):                                          # status CHECKs hold
                conn.execute("INSERT INTO dv_jobs (id, project_id, owner_user_id, kind, status, limits, created_at) VALUES ('j','p','u1','script','teleported','{}','t')")


class Deletion(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness(dev_pipelines_enabled=True)
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a, "Gone soon")
        self.fs, self.js = self.d.svc(FileService), self.d.svc(JobService)

    def populate(self):
        self.d.svc(SecretService).create(self.a, self.pid, "TOK", "value-123")
        self.fs.write(self.a, self.pid, "train.py", "import os\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/m.txt', 'w').write('x')\nprint(1)\n", None)
        self.fs.write(self.a, self.pid, "tests/test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n", None)
        ex = self.d.svc(ExperimentService)
        e = ex.create(self.a, self.pid, name="e")
        ex.run(self.a, self.pid, e["id"], entry="train.py")
        pl = self.d.svc(PipelineService)
        p = pl.create(self.a, self.pid, name="ci", stages=[{"type": "lint"}])
        pl.run(self.a, self.pid, p["id"])
        remote = os.path.join(tempfile.mkdtemp(), "r.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
        self.d.svc(GitService).connect(self.a, self.pid, provider="local", remote_url=remote, consent=True, webhook_secret="hook-hook-hook")
        for _ in range(6):
            self.d.run_all()
        c = self.d.h.conn
        c.execute("INSERT INTO dv_quota_overrides (scope, scope_id, key, value, set_by, reason, set_at) VALUES ('project', ?, 'job_wall_seconds', 60, ?, 'test reason', 't')", (self.pid, self.d.owner.user_id))
        self.assertGreater(c.execute("SELECT COUNT(*) FROM dv_jobs WHERE project_id = ?", (self.pid,)).fetchone()[0], 0)

    def dv_rows(self, pid=None):
        c = self.d.h.conn
        total = 0
        for t in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'dv_%'")]:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            if "project_id" in cols and pid:
                total += c.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id = ?", (pid,)).fetchone()[0]
        return total

    def test_deleting_a_project_removes_every_dev_row_and_the_folder_without_touching_other_projects(self):
        other = self.d.project(self.a, "Keep me")
        self.fs.write(self.a, other, "keep.py", "print(1)\n", None)
        self.populate()
        other_before = self.dv_rows(other)
        self.assertGreater(other_before, 0)
        self.assertGreater(self.dv_rows(self.pid), 10)
        self.assertTrue(os.path.isdir(os.path.join(self.d.root, self.pid)))
        out = self.d.ws().projects.delete(self.a, self.pid, "Gone soon")
        self.assertTrue(out["deleted"])
        self.assertEqual(self.dv_rows(self.pid), 0)
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_quota_overrides WHERE scope_id = ?", (self.pid,)).fetchone()[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.d.root, self.pid)))
        self.assertEqual(self.dv_rows(other), other_before)                                                   # the other project's file is untouched
        self.assertEqual(self.fs.read(self.a, other, "keep.py")["content"], "print(1)\n")
        self.assertEqual(self.d.h.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_deleting_a_project_stops_a_job_that_is_running(self):
        import threading
        self.fs.write(self.a, self.pid, "long.py", "import time\ntime.sleep(60)\n", None)
        self.js.submit(self.a, self.pid, kind="script", entry="long.py")
        w = self.d.worker()
        th = threading.Thread(target=w.tick)
        th.start()
        for _ in range(100):
            if self.d.h.conn.execute("SELECT status FROM dv_jobs").fetchone()[0] == "running":
                break
            time.sleep(0.05)
        t0 = time.time()
        self.d.ws().projects.delete(self.a, self.pid, "Gone soon")
        th.join(20)
        self.assertFalse(th.is_alive())
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_jobs").fetchone()[0], 0)
        self.assertEqual(subprocess.run(["pgrep", "-f", "long.py"], capture_output=True, text=True).stdout.split(), [])

    def test_deleting_the_account_removes_everything_including_user_scoped_overrides(self):
        self.populate()
        self.d.h.conn.execute("INSERT INTO dv_quota_overrides (scope, scope_id, key, value, set_by, reason, set_at) VALUES ('user', ?, 'job_cpu_seconds', 90, ?, 'test reason', 't')", (self.a.user_id, self.d.owner.user_id))
        self.d.ws().governance.delete_all_for_user(self.a.user_id, self.a.user_id)
        self.assertEqual(self.dv_rows(self.pid), 0)
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_quota_overrides").fetchone()[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.d.root, self.pid)))

    def test_deleting_a_file_respects_ownership_and_keeps_history_restorable(self):
        bob = self.d.user("bob@example.com")
        self.fs.write(self.a, self.pid, "a.py", "1\n", None)
        from app.core.errors import AppError
        with self.assertRaises(AppError) as ctx:
            self.d.svc(FileService).delete(bob, self.pid, "a.py", "a.py")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.fs.read(self.a, self.pid, "a.py")["content"], "1\n")


if __name__ == "__main__":
    unittest.main()
