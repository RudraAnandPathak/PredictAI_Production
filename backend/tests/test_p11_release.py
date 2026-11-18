"""Release and rollback drills on real databases: fresh install, upgrade with data, compatibility with the previous version, interrupted migration,
rollback by restoring the pre-upgrade backup, release manifest."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, "../scripts")
from app.db import Database
from app.db.migrator import MIGRATIONS_DIR, MigrationError, applied, discover, migrate
from app.ops import backup as B
from app.ops import release as R

LATEST = discover()[-1].version


def db_at(path: Path, version: int) -> None:
    tmp = Path(tempfile.mkdtemp())
    for m in discover():
        if m.version <= version:
            (tmp / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None)
    migrate(c, tmp)
    c.close()
    shutil.rmtree(tmp, True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)


class Migrations(Base):
    def test_fresh_install_reaches_the_latest_version(self):
        p = self.tmp / "fresh.db"
        d = Database(str(p)); d.ensure_ready(auto_migrate=True)
        with d.session() as c:
            self.assertEqual(max(applied(c)), LATEST)

    def test_every_migration_is_additive_so_the_previous_app_version_still_works(self):
        rep = R.compatibility_report()
        self.assertEqual(len(rep), LATEST)
        self.assertEqual([r for r in rep if not r["additive"]], [], "a migration dropped or retyped something the previous version used")

    def test_the_additive_check_really_detects_removals(self):
        old = {"t": {"a": "TEXT", "b": "INTEGER"}, "gone": {"x": "TEXT"}}
        new = {"t": {"a": "INTEGER"}}
        d = R.additive_diff(old, new)
        self.assertEqual(d["dropped_tables"], ["gone"]); self.assertEqual(d["dropped_columns"], ["t.b"]); self.assertEqual(d["changed_types"], ["t.a: TEXT -> INTEGER"])

    def test_interrupted_migration_leaves_the_database_at_the_old_version(self):
        p = self.tmp / "m.db"; db_at(p, LATEST)
        mig = self.tmp / "migs"; mig.mkdir()
        for m in discover():
            (mig / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
        (mig / f"{LATEST + 1:04d}_broken.sql").write_text("CREATE TABLE half_done (id TEXT PRIMARY KEY);\nINSERT INTO no_such_table VALUES (1);")
        c = sqlite3.connect(p, isolation_level=None)
        with self.assertRaises(sqlite3.Error):
            migrate(c, mig)
        self.assertEqual(max(applied(c)), LATEST)
        self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='half_done'").fetchone())
        c.close()

    def test_a_changed_applied_migration_is_refused(self):
        p = self.tmp / "m.db"; db_at(p, LATEST)
        mig = self.tmp / "migs"; mig.mkdir()
        for m in discover():
            (mig / f"{m.version:04d}_{m.name}.sql").write_text(m.sql + ("\n-- edited" if m.version == 3 else ""))
        c = sqlite3.connect(p, isolation_level=None)
        with self.assertRaises(MigrationError):
            migrate(c, mig)
        c.close()


class RollbackDrill(Base):
    def test_upgrade_with_data_then_roll_back_by_restoring_the_pre_upgrade_backup(self):
        live = self.tmp / "live" / "predictai.db"; storage = self.tmp / "live" / "storage"
        db_at(live, LATEST - 1)                                           # an installation one release behind
        c = sqlite3.connect(live, isolation_level=None)
        c.execute("INSERT INTO users (id, email, email_normalized, password_hash, role, status, created_at, updated_at) VALUES ('u1','a@example.com','a@example.com','h','user','active','2026-01-01','2026-01-01')") if _users_ok(c) else None
        before_tables = R.schema_snapshot(c)
        c.close()
        (storage / "ri").mkdir(parents=True); (storage / "ri" / "f.txt").write_text("original")
        out = self.tmp / "backups"
        pre = B.create_backup(str(live), str(storage), str(out), note="pre-upgrade")          # step 1 of every upgrade
        self.assertEqual(pre["schema_version"], LATEST - 1)

        d = Database(str(live)); d.ensure_ready(auto_migrate=True)                            # step 2: upgrade
        with d.session() as c:
            self.assertEqual(max(applied(c)), LATEST)
            after = R.schema_snapshot(c)
            # the previous application's own queries still run against the upgraded schema
            for t, cols in before_tables.items():
                c.execute(f'SELECT {", ".join(chr(34) + x + chr(34) for x in cols)} FROM "{t}" LIMIT 1').fetchall()
        self.assertFalse(any(R.additive_diff(before_tables, after).values()))

        target = self.tmp / "rolled_back"                                                      # step 3: rollback = restore into a new directory, then switch over
        rep = B.restore_backup(str(out / pre["backup_id"]), str(target))
        self.assertTrue(rep["ok"], rep["problems"]); self.assertEqual(rep["schema_version"], LATEST - 1)
        self.assertEqual((target / "storage/ri/f.txt").read_text(), "original")
        # the rolled-back database can be upgraded again later (the failed upgrade left no trace in it)
        d2 = Database(str(target / "predictai.db")); d2.ensure_ready(auto_migrate=True)
        with d2.session() as c:
            self.assertEqual(max(applied(c)), LATEST)
        # and the upgraded live database was not modified by the restore
        with d.session() as c:
            self.assertEqual(max(applied(c)), LATEST)


def _users_ok(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    return {"id", "email", "email_normalized", "password_hash", "role", "status", "created_at", "updated_at"} <= cols


class Manifest(Base):
    def make_zip(self, extra=None):
        z = self.tmp / "app.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("PredictAI/README.md", "x"); f.writestr("PredictAI/config/env.example", "A=1")
            for n, data in (extra or {}).items():
                f.writestr(n, data)
        return z

    def test_manifest_ties_the_archive_to_its_contents(self):
        z = self.make_zip()
        m = R.manifest(str(z), commit="abc1234", tests={"backend": "1901 passed"})
        self.assertTrue(m["ok"]); self.assertEqual(m["commit"], "abc1234")
        self.assertEqual(m["archive"]["sha256"], R._sha(z)); self.assertEqual(m["database"]["latest_migration"], LATEST)
        self.assertEqual(len(m["database"]["migrations"]), LATEST)
        self.assertIn("requirements.txt", m["dependencies"]); self.assertIn("env.example", m["configuration"])
        self.assertEqual(m["tests"], {"backend": "1901 passed"})

    def test_commit_is_never_invented(self):
        self.assertIn("not recorded", R.manifest(str(self.make_zip()))["commit"])

    def test_an_archive_carrying_a_secret_or_a_database_fails_the_manifest(self):
        for bad in ("PredictAI/.env", "PredictAI/data/predictai.db", "PredictAI/frontend/node_modules/x.js", "PredictAI/a/server.pem"):
            m = R.manifest(str(self.make_zip({bad: "secret"})))
            self.assertFalse(m["ok"], bad); self.assertIn(bad, m["archive"]["forbidden_entries"])

    def test_the_real_packager_output_passes(self):
        import package_release
        z = self.tmp / "real.zip"
        n = package_release.build_archive(package_release.ROOT, z)
        self.assertGreater(n, 100)
        m = R.manifest(str(z))
        self.assertTrue(m["ok"], m["archive"]["forbidden_entries"])


if __name__ == "__main__":
    unittest.main()
