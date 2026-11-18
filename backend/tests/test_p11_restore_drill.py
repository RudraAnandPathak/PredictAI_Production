"""Restore drill: a populated Research Intelligence database (sources, chunks, analysis, report with versions) is backed up, restored
into a NEW directory, and checked for internal consistency. The live data is never written."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from app.db import Database
from app.ops import backup as B
from test_p10_reports import Base as ReportBase


class Drill(ReportBase):
    __test__ = True
    def test_restored_copy_is_internally_consistent(self):
        r = self.make("academic")
        sections = self.rs().get(self.alice, self.wid, r["id"])["sections"]
        sections[0]["blocks"][0]["text"] = "My own opening."
        self.rs().update(self.alice, self.wid, r["id"], sections=sections)                 # creates a version row
        live = self.t.h.settings
        out, target = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "restored"
        self.addCleanup(shutil.rmtree, out, True); self.addCleanup(shutil.rmtree, target.parent, True)
        self.t.ctx().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        live_bytes = Path(live.database_path).read_bytes()
        m = B.create_backup(live.database_path, live.ri_root, str(out))
        self.assertGreater(m["counts"]["files"], 1)
        rep = B.restore_backup(str(out / m["backup_id"]), str(target))
        self.assertTrue(rep["ok"], rep["problems"])
        self.assertEqual(Path(live.database_path).read_bytes(), live_bytes, "restore must not touch the live database")

        d = Database(str(target / "predictai.db"))
        d.ensure_ready(auto_migrate=False)                                                   # schema is current: nothing pending
        with d.session() as c:
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_reports").fetchone()[0], 1)
            self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM ri_report_versions").fetchone()[0], 1)
            # user -> workspace -> source relationships survived
            self.assertEqual(c.execute("SELECT COUNT(*) FROM rw_workspaces w JOIN users u ON u.id = w.owner_user_id WHERE w.id = ?", (self.wid,)).fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_sources s JOIN rw_workspaces w ON w.id = s.workspace_id").fetchone()[0], 3)
            # evidence references: every quotation in the restored report is still an exact piece of a restored passage
            rep_row = c.execute("SELECT sections FROM ri_reports").fetchone()
            chunks = [x["text"].replace("\n", " ") for x in c.execute("SELECT text FROM ri_chunks")]
            quotes = [b["text"] for s in json.loads(rep_row["sections"]) for b in s["blocks"] if b["type"] == "quote"]
            self.assertTrue(quotes)
            for q in quotes:
                self.assertTrue(any(q in ch for ch in chunks), q[:60])
            # the author's edit is in the restored report
            self.assertIn("My own opening.", rep_row["sections"])


if __name__ == "__main__":
    unittest.main()
