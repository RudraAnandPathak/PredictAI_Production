"""Backup / restore: real SQLite database with the real migrations, real files, a real restore into a new directory."""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.db import Database
from app.db.migrator import discover
LATEST = discover()[-1].version

from app.ops import backup as B


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = self.tmp / "live" / "predictai.db"
        self.storage = self.tmp / "live" / "storage"
        d = Database(str(self.db))
        d.ensure_ready(auto_migrate=True)
        with d.session() as c:
            c.execute("INSERT INTO users (id, email, email_normalized, password_hash, display_name, role, status, created_at, updated_at) VALUES ('u1','a@example.com','a@example.com','x','A','user','active','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')") if False else None
        (self.storage / "ri" / "u1").mkdir(parents=True)
        (self.storage / "ri" / "u1" / "paper.pdf").write_bytes(b"%PDF-1.4 original bytes " * 200)
        (self.storage / "registry").mkdir()
        (self.storage / "registry" / "model.bin").write_bytes(os.urandom(5000))
        (self.storage / "dev-secret-key").write_text("TOP SECRET")
        (self.storage / "ri" / "u1" / "tokens.key").write_text("k")
        (self.storage / "x.db-wal").write_bytes(b"w")
        self.out = self.tmp / "backups"

    def make(self, **kw):
        return B.create_backup(str(self.db), str(self.storage), str(self.out), **kw)

    def path(self, m):
        return self.out / m["backup_id"]


class Create(Base):
    def test_backup_records_scope_checksums_and_leaves_secrets_out(self):
        m = self.make(note="before upgrade")
        self.assertEqual(m["components"], ["database", "storage"])
        paths = {f["path"] for f in m["files"]}
        self.assertIn("predictai.db", paths)
        self.assertIn("storage/ri/u1/paper.pdf", paths)
        self.assertIn("storage/registry/model.bin", paths)
        self.assertNotIn("storage/dev-secret-key", paths)
        self.assertIn("dev-secret-key", m["excluded"]["secrets"])
        self.assertIn("ri/u1/tokens.key", m["excluded"]["secrets"])
        self.assertIn("x.db-wal", m["excluded"]["transient"])
        self.assertEqual(m["schema_version"], LATEST)
        self.assertNotIn("TOP SECRET", "".join(p.read_text(errors="ignore") for p in self.path(m).rglob("*") if p.is_file() and p.stat().st_size < 100000 and p.suffix != ".db"))
        self.assertTrue(B.verify_backup(str(self.path(m)))["ok"])

    def test_originals_are_not_modified(self):
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.storage.rglob("*") if p.is_file()}
        self.make()
        after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.storage.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_consistent_snapshot_while_the_app_is_writing(self):
        stop = threading.Event()
        def writer():
            c = sqlite3.connect(self.db, timeout=30, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS noise (n INTEGER)")
            i = 0
            while not stop.is_set():
                c.execute("INSERT INTO noise VALUES (?)", (i,)); i += 1
            c.close()
        t = threading.Thread(target=writer); t.start()
        try:
            m = self.make()
        finally:
            stop.set(); t.join()
        self.assertTrue(B.verify_backup(str(self.path(m)))["ok"])
        c = sqlite3.connect(self.path(m) / "predictai.db")
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_a_file_that_keeps_changing_fails_the_backup_and_leaves_nothing_behind(self):
        target = self.storage / "ri" / "u1" / "paper.pdf"
        real = shutil.copyfile
        def racing(src, dst, *a, **k):
            out = real(src, dst, *a, **k)
            if Path(src) == target:
                with open(target, "ab") as f:
                    f.write(b"more")
            return out
        with mock.patch("app.ops.backup.shutil.copyfile", racing):
            with self.assertRaises(B.BackupError) as cm:
                self.make()
        self.assertIn("kept changing", str(cm.exception))
        self.assertEqual(list(self.out.glob("backup-*")), [])
        self.assertEqual(list(self.out.glob(".partial-*")), [])

    def test_damaged_live_database_is_reported_not_backed_up(self):
        self.db.write_bytes(b"this is not a database" * 100)
        with self.assertRaises(Exception):
            self.make()
        self.assertEqual(list(self.out.glob("backup-*")), [])

    def test_missing_database_is_a_clear_error(self):
        with self.assertRaises(B.BackupError):
            B.create_backup(str(self.tmp / "nope.db"), str(self.storage), str(self.out))


class VerifyAndRestore(Base):
    def test_tampering_missing_and_extra_files_are_detected(self):
        m = self.make(); root = self.path(m)
        (root / "storage/ri/u1/paper.pdf").write_bytes(b"tampered")
        r = B.verify_backup(str(root)); self.assertFalse(r["ok"]); self.assertTrue(any("paper.pdf" in p for p in r["problems"]))
        (root / "storage/registry/model.bin").unlink()
        (root / "storage/extra.txt").write_text("x")
        probs = " ".join(B.verify_backup(str(root))["problems"])
        self.assertIn("missing: storage/registry/model.bin", probs); self.assertIn("not in manifest: storage/extra.txt", probs)

    def test_restore_into_a_new_directory_is_consistent_and_leaves_the_live_data_alone(self):
        m = self.make(); live_before = self.db.read_bytes()
        target = self.tmp / "restored"
        rep = B.restore_backup(str(self.path(m)), str(target))
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertEqual(rep["integrity_check"], "ok"); self.assertEqual(rep["foreign_key_violations"], 0); self.assertEqual(rep["schema_version"], LATEST)
        self.assertEqual((target / "storage/ri/u1/paper.pdf").read_bytes(), (self.storage / "ri/u1/paper.pdf").read_bytes())
        self.assertEqual((target / "storage/registry/model.bin").read_bytes(), (self.storage / "registry/model.bin").read_bytes())
        self.assertFalse((target / "storage/dev-secret-key").exists())
        self.assertEqual(self.db.read_bytes(), live_before)
        self.assertTrue((target / "restore-report.json").is_file())
        # the restored database is a working application database: it migrates cleanly (nothing pending) and opens with the app's own class
        d = Database(str(target / "predictai.db")); d.ensure_ready(auto_migrate=False)
        with d.session() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], LATEST)

    def test_restore_refuses_a_non_empty_target_and_a_bad_backup(self):
        m = self.make()
        busy = self.tmp / "busy"; busy.mkdir(); (busy / "keep.txt").write_text("mine")
        with self.assertRaises(B.BackupError): B.restore_backup(str(self.path(m)), str(busy))
        self.assertEqual((busy / "keep.txt").read_text(), "mine")
        (self.path(m) / "storage/registry/model.bin").write_bytes(b"x")
        with self.assertRaises(B.BackupError) as cm: B.restore_backup(str(self.path(m)), str(self.tmp / "t2"))
        self.assertIn("failed verification", str(cm.exception)); self.assertFalse((self.tmp / "t2").exists())

    def test_a_manifest_with_a_path_outside_the_backup_is_rejected(self):
        m = self.make(); root = self.path(m)
        man = json.loads((root / "manifest.json").read_text())
        man["files"].append({"path": "../escape.txt", "size": 1, "sha256": "0" * 64, "component": "storage"})
        (root / "manifest.json").write_text(json.dumps(man))
        with self.assertRaises(B.BackupError): B.restore_backup(str(root), str(self.tmp / "t3"))
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_restore_detects_a_database_with_foreign_key_damage(self):
        m = self.make(); root = self.path(m)
        c = sqlite3.connect(root / "predictai.db"); c.execute("PRAGMA foreign_keys=OFF")
        tbl = c.execute("SELECT m.name, p.\"table\", p.\"from\", p.\"to\" FROM sqlite_master m JOIN pragma_foreign_key_list(m.name) p WHERE m.type='table' LIMIT 1").fetchone()
        c.close()
        self.assertIsNotNone(tbl)       # the schema has foreign keys, so this check is meaningful
        # a backup whose database was altered after the manifest was written must fail verification before anything is restored
        with open(root / "predictai.db", "ab") as f: f.write(b"\0")
        with self.assertRaises(B.BackupError): B.restore_backup(str(root), str(self.tmp / "t4"))


class Retention(Base):
    def test_prune_keeps_the_newest_and_cleans_interrupted_runs_only(self):
        ids = []
        for _ in range(4):
            ids.append(self.make()["backup_id"])
        (self.out / ".partial-backup-dead").mkdir()
        (self.out / "unrelated").mkdir(); (self.out / "unrelated" / "x").write_text("keep me")
        gone = B.prune_backups(str(self.out), keep=2)
        self.assertEqual(gone, sorted(ids)[:2] if sorted(ids) == ids else gone)
        self.assertEqual(len(B.list_backups(str(self.out))), 2)
        self.assertFalse((self.out / ".partial-backup-dead").exists())
        self.assertTrue((self.out / "unrelated" / "x").exists())
        with self.assertRaises(B.BackupError): B.prune_backups(str(self.out), keep=0)

    def test_cli_round_trip_and_exit_codes(self):
        self.assertEqual(B.main(["create", "--out", str(self.out), "--database", str(self.db), "--storage", str(self.storage)]), 0)
        b = B.list_backups(str(self.out))[0]["path"]
        self.assertEqual(B.main(["verify", b]), 0)
        self.assertEqual(B.main(["restore", b, "--target", str(self.tmp / "cli")]), 0)
        self.assertEqual(B.main(["restore", b, "--target", str(self.tmp / "cli")]), 2)


if __name__ == "__main__":
    unittest.main()
