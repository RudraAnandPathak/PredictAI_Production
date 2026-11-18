"""Governance: retention dates, deletion footprint, inventory, automatic deletion of derived data,
stuck-processing recovery, orphan sweeping, and the guarantee that logs/audit never carry user data."""
import json
import logging
import os
import unittest

from app.core.errors import AppError
from app.modules.storage import LocalStorage
from app.modules.workspace.container import build_workspace
from app.modules.workspace.processing import NullProcessor
from test_workspace_cleaning import DATA, CleaningBase
from test_workspace_extraction import Base
from test_workspace_uploads import code_of, upload


class RetentionTests(CleaningBase):
    def retention(self, value, who=None, asset=None):
        return self.ws.assets.set_retention(who or self.alice, asset or self.aid, value)

    def test_dates_are_normalised_validated_and_recorded(self):
        result = self.retention("2026-06-30")
        self.assertEqual(result["retain_until"], "2026-06-30T23:59:59.000000Z")
        self.assertEqual(self.ws.assets.get(self.alice, self.aid)["retain_until"], result["retain_until"])
        self.assertEqual(self.retention("2026-07-01T10:00:00+05:30")["retain_until"], "2026-07-01T04:30:00.000000Z")
        events = [e["event_type"] for e in self.ws.projects.activity(self.alice, self.project)["items"]]
        self.assertIn("asset.retention_set", events)

    def test_bad_dates_are_refused_with_a_reason(self):
        for bad in ("2020-01-01", "2026-01-01T12:00:00Z", "2099-01-01", "tomorrow", "x" * 50, 12345):
            self.assertEqual(code_of(self.retention, bad), "VALIDATION_ERROR", bad)

    def test_clearing_and_isolation(self):
        self.retention("2026-06-30")
        self.assertIsNone(self.retention(None)["retain_until"])
        self.assertIsNone(self.ws.assets.get(self.alice, self.aid)["retain_until"])
        self.assertEqual(code_of(self.retention, "2026-06-30", self.bob), "NOT_FOUND")
        self.assertEqual(code_of(self.ws.assets.footprint, self.bob, self.aid), "NOT_FOUND")

    def test_expiry_deletes_the_file_and_everything_derived_from_it(self):
        self.apply([{"op": "drop_duplicates"}])
        self.ws.profiles.generate(self.alice, self.aid)
        prefix = f"p/{self.project}/a/{self.aid}"
        self.assertTrue(self.h.storage.list_prefix(prefix))
        self.retention("2026-03-01")
        self.assertEqual(self.ws.governance.run_retention()["expired_assets"], 0)  # not yet due
        self.h.clock.advance(days=90)
        result = self.ws.governance.run_retention()
        self.assertEqual(result["expired_assets"], 1)
        self.assertEqual(self.h.storage.list_prefix(prefix), [])
        for table in ("assets", "asset_versions", "extractions", "profiles", "transformations"):
            self.assertEqual(self.h.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE {'id' if table == 'assets' else 'asset_id'} = ?", (self.aid,))[0]["n"], 0, table)
        record = self.ws.governance.list_deletions(self.alice)["items"][0]
        self.assertEqual((record["scope"], record["status"]), ("retention", "completed"))
        self.assertEqual(code_of(self.ws.assets.get, self.alice, self.aid), "NOT_FOUND")
        self.assertTrue(self.h.rows("SELECT * FROM audit_events WHERE event_type = 'retention.purged'"))


class FootprintAndInventoryTests(CleaningBase):
    def test_footprint_lists_what_deletion_would_remove(self):
        self.apply([{"op": "drop_duplicates"}])
        self.ws.profiles.generate(self.alice, self.aid)
        fp = self.ws.assets.footprint(self.alice, self.aid)
        kinds = {i["kind"]: i for i in fp["items"]}
        self.assertEqual(fp["versions"], 2)
        self.assertEqual(fp["derived_versions"], 1)
        self.assertEqual(kinds["original"]["objects"], 1)
        self.assertGreaterEqual(kinds["table"]["objects"], 2)
        self.assertIn("profiles", kinds)
        self.assertEqual(fp["objects"], len(self.h.storage.list_prefix(f"p/{self.project}/a/{self.aid}")))
        self.assertEqual(self.ws.assets.delete(self.alice, self.aid)["counts"]["objects"], fp["objects"])  # what was promised is what was removed

    def test_inventory_counts_only_the_callers_data_and_lists_upcoming_deletions(self):
        self.ws.assets.set_retention(self.alice, self.aid, "2026-01-10")
        inv = self.ws.assets.inventory(self.alice)
        self.assertEqual((inv["projects"], inv["assets"], inv["datasets"], inv["documents"]), (1, 1, 1, 0))
        self.assertEqual(inv["original_bytes"], len(DATA))
        self.assertEqual([u["asset_id"] for u in inv["upcoming_deletions"]], [self.aid])
        self.assertEqual(self.ws.assets.inventory(self.alice, 3)["upcoming_deletions"], [])  # outside a 3-day horizon
        empty = self.ws.assets.inventory(self.bob)
        self.assertEqual((empty["projects"], empty["assets"], empty["stored_bytes"], empty["upcoming_deletions"]), (0, 0, 0, []))


class AccountDeletionTests(CleaningBase):
    def test_account_deletion_purges_every_project_file_and_derived_object(self):
        self.apply([{"op": "drop_duplicates"}])
        other = self.ws.projects.create(self.alice, "Second")["id"]
        upload(self.ws, self.alice, other, "b.csv", b"x\n1\n")
        bobs = upload(self.ws, self.bob, self.ws.projects.create(self.bob, "Bob")["id"], "c.csv", b"q\n9\n")["asset"]
        totals = self.ws.governance.delete_all_for_user(self.alice.user_id, self.alice.user_id)
        self.assertEqual(totals["projects"], 2)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM assets WHERE owner_user_id = ?", (self.alice.user_id,))[0]["n"], 0)
        left = self.h.storage.list_prefix("p")
        self.assertTrue(left and all(f"/{bobs['id']}/" in k for k in left), left)  # only Bob's objects remain


class RecoveryTests(Base):
    def test_stuck_processing_becomes_a_clear_failure_and_nothing_else_changes(self):
        a = self.ok("a.csv", b"x,y\n1,2\n")
        b = self.ok("b.csv", b"x,y\n3,4\n")
        self.h.conn.execute("UPDATE assets SET status = 'processing' WHERE id = ?", (a["id"],))
        self.assertEqual(self.ws.governance.run_retention()["interrupted"], 0)  # still fresh
        self.h.clock.advance(minutes=45)
        self.assertEqual(self.ws.governance.run_retention()["interrupted"], 1)
        row = self.ws.assets.get(self.alice, a["id"])
        self.assertEqual((row["status"], row["status_code"]), ("failed", "PROCESSING_INTERRUPTED"))
        self.assertEqual(self.ws.assets.get(self.alice, b["id"])["status"], "ready")

    def test_orphaned_objects_are_swept_only_when_old_enough_and_real_data_is_never_touched(self):
        asset = self.ok("a.csv", b"x,y\n1,2\n")
        real = self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}")
        self.h.storage.put(f"p/{self.project}/a/{'f' * 32}/v/{'e' * 32}/original", [b"left behind"])
        self.h.storage.put(f"t/{'d' * 32}/0", [b"chunk"])
        self.assertEqual(self.ws.governance.sweep_orphans(), 0)  # brand new: could be an upload in flight
        self.assertEqual(self.ws.governance.sweep_orphans(min_age_seconds=0), 2)
        self.assertEqual(self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}"), real)
        self.assertEqual(self.h.storage.list_prefix("t"), [])

    def test_an_open_upload_session_is_not_an_orphan(self):
        started = self.ws.uploads.start(self.alice, self.project, "big.csv", 10, None)["upload"]
        self.ws.uploads.put_chunk(self.alice, started["id"], 0, b"0123456789")
        self.assertEqual(self.ws.governance.sweep_orphans(min_age_seconds=0), 0)
        self.assertEqual(len(self.h.storage.list_prefix(f"t/{started['id']}")), 1)


class Boom(NullProcessor):
    def pages_for_quota(self, path, fmt):
        return None

    def process(self, principal, asset, version, path):
        raise ValueError("SECRET-CELL-VALUE-123 could not be parsed")


class PrivacyTests(Base):
    NAME, PROJECT = "SENTINELFILENAME.csv", "SENTINELPROJECTNAME"

    def test_logs_never_contain_exception_messages_or_data(self):
        ws = build_workspace(self.h.services().ctx, self.h.storage, Boom())
        project = ws.projects.create(self.alice, self.PROJECT)["id"]
        with self.assertLogs("predictai.workspace", level="DEBUG") as captured:
            asset = upload(ws, self.alice, project, self.NAME, b"a,b\nSECRET-CELL-VALUE-123,2\n")["asset"]
        self.assertEqual(asset["status_code"], "PROCESSING_ERROR")
        text = "\n".join(captured.output) + "\n".join(str(r.exc_info) + str(r.exc_text) + r.getMessage() for r in captured.records)
        self.assertNotIn("SECRET-CELL-VALUE", text)
        self.assertNotIn(self.NAME, text)
        self.assertIn("ValueError", text)  # the type and place are kept, so operators can still diagnose
        self.assertTrue(all(r.exc_info is None for r in captured.records))

    def test_audit_and_activity_records_hold_ids_and_counts_but_never_names_or_contents(self):
        project = self.ws.projects.create(self.alice, self.PROJECT)["id"]
        a = upload(self.ws, self.alice, project, self.NAME, b"a,b\nSECRET-CELL-VALUE-123,2\n")["asset"]
        self.ws.profiles.generate(self.alice, a["id"])
        self.ws.cleaning.preview(self.alice, a["id"], [{"op": "fill_missing", "column": "b", "strategy": "constant", "value": "VALUE-SENTINEL-9"}])
        self.ws.assets.delete(self.alice, a["id"])
        self.ws.projects.delete(self.alice, project, self.PROJECT)
        blob = "\n".join(repr(r) for r in self.h.rows("SELECT * FROM audit_events")) + "\n".join(
            repr(r) for r in self.h.rows("SELECT * FROM project_events")) + "\n".join(repr(r) for r in self.h.rows("SELECT * FROM deletion_records"))
        for secret in ("SECRET-CELL-VALUE", "SENTINELFILENAME", "VALUE-SENTINEL-9"):
            self.assertNotIn(secret, blob)


class CliPurgeTests(unittest.TestCase):
    def test_purge_command_also_runs_workspace_retention(self):
        from app import cli
        import inspect
        source = inspect.getsource(cli)
        self.assertIn("run_retention", source)


if __name__ == "__main__":
    unittest.main()
