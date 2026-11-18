"""Phase 9: Special-access gate, project isolation, the file tree (versions, concurrency, secrets, quota), revocation behaviour."""
import os
import unittest
from dataclasses import replace

from app.core.errors import AppError
from app.modules.devspace.files import FileService
from app.modules.devspace.overview import OverviewService
from app.modules.devspace.secrets import SecretService
from p9_support import DevHarness


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class FilesAccess(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.alice = self.d.user("alice@example.com")
        self.bob = self.d.user("bob@example.com")
        self.plain = self.d.user("plain@example.com", special=False)
        self.pid = self.d.project(self.alice)
        self.fs = self.d.svc(FileService)

    def test_special_access_gate_is_enforced_by_the_service(self):
        pid = self.d.project(self.plain)
        self.assertEqual(code(self.fs.list, self.plain, pid), ("SPECIAL_ACCESS_REQUIRED", 403))
        self.assertEqual(code(self.fs.write, self.plain, pid, "a.py", "x", None), ("SPECIAL_ACCESS_REQUIRED", 403))
        landing = self.d.svc(OverviewService).landing(self.plain)
        self.assertEqual(landing["access"]["allowed"], False)
        self.assertNotIn("counts", landing)                      # no data for people without access

    def test_landing_shows_real_zeros_then_real_counts(self):
        ov = self.d.svc(OverviewService)
        before = ov.landing(self.alice)
        self.assertEqual(before["counts"]["files"], 0)
        self.assertFalse(before["jobs"]["recent"])
        self.fs.write(self.alice, self.pid, "a.py", "print(1)\n", None)
        self.assertEqual(ov.landing(self.alice)["counts"]["files"], 1)
        self.assertEqual(ov.landing(self.alice)["execution"]["deploy_enabled"], False)

    def test_other_users_cannot_see_or_touch_a_project(self):
        self.fs.write(self.alice, self.pid, "a.py", "print(1)\n", None)
        for call in (lambda: self.fs.list(self.bob, self.pid), lambda: self.fs.read(self.bob, self.pid, "a.py"), lambda: self.fs.write(self.bob, self.pid, "b.py", "x", None),
                     lambda: self.fs.delete(self.bob, self.pid, "a.py", "a.py")):
            self.assertEqual(code(call)[1], 404)

    def test_versions_concurrency_restore_and_delete_confirmation(self):
        v1 = self.fs.write(self.alice, self.pid, "src/m.py", "v1\n", None)
        v2 = self.fs.write(self.alice, self.pid, "src/m.py", "v2\n", 1)
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        self.assertEqual(code(self.fs.write, self.alice, self.pid, "src/m.py", "stale\n", 1)[1], 409)       # someone saved in between
        self.assertEqual(self.fs.read(self.alice, self.pid, "src/m.py", 1)["content"], "v1\n")
        self.assertEqual(self.fs.restore(self.alice, self.pid, "src/m.py", 1)["version"], 3)
        self.assertEqual(self.fs.read(self.alice, self.pid, "src/m.py")["content"], "v1\n")
        self.assertEqual(len(self.fs.history(self.alice, self.pid, "src/m.py")["versions"]), 3)
        self.assertEqual(code(self.fs.delete, self.alice, self.pid, "src/m.py", "nope")[1], 409)
        self.fs.delete(self.alice, self.pid, "src/m.py", "src/m.py")
        self.assertEqual([f["path"] for f in self.fs.list(self.alice, self.pid)["items"]], [])
        self.assertTrue(os.path.exists(os.path.join(self.d.root, self.pid, "objects")))                    # earlier versions stay in the content store

    def test_unsafe_paths_and_credential_files_are_refused(self):
        for bad in ("/etc/passwd", "../x.py", "a/../../x.py", ".env", "src/.env.local", "keys/id_rsa", "x.pem", ".git/config", "a\\b.py", "~/x", "a\x00b.py", ""):
            self.assertEqual(code(self.fs.write, self.alice, self.pid, bad, "x", None)[1], 422, repr(bad))

    def test_credential_looking_content_is_refused(self):
        self.assertEqual(code(self.fs.write, self.alice, self.pid, "a.py", "TOKEN = 'ghp_" + "a" * 36 + "'\n", None), ("VALIDATION_ERROR", 422))
        self.assertEqual(code(self.fs.write, self.alice, self.pid, "k.txt", "-----BEGIN RSA PRIVATE KEY-----\nabc\n", None)[1], 422)

    def test_storage_quota_blocks_writes(self):
        self.d.h.settings = replace(self.d.h.settings, dev_project_storage_mb=1)
        fs = self.d.svc(FileService)
        fs.write(self.alice, self.pid, "big1.txt", "x" * 600_000, None)
        self.assertEqual(code(fs.write, self.alice, self.pid, "big2.txt", "x" * 600_000, None)[0], "STORAGE_QUOTA_EXCEEDED")
        self.assertEqual(code(fs.write, self.alice, self.pid, "huge.txt", "x" * 1_100_000, None)[0], "FILE_TOO_LARGE")

    def test_a_suspended_workspace_is_readable_but_not_writable_and_returns_when_access_returns(self):
        from app.modules.devspace.engine import Worker
        self.fs.write(self.alice, self.pid, "a.py", "1\n", None)
        self.d.h.services().codes  # codes service exists
        # revoke Special access
        ents = self.d.h.conn.execute("SELECT id FROM entitlements WHERE user_id = ? AND revoked_at IS NULL", (self.alice.user_id,)).fetchall()
        for e in ents:
            self.d.h.conn.execute("UPDATE entitlements SET revoked_at = ? WHERE id = ?", (self.d.h.clock.iso(), e["id"]))
        self.d.h.conn.execute("UPDATE users SET authz_version = authz_version + 1 WHERE id = ?", (self.alice.user_id,))
        alice_now = self.d.refresh("alice@example.com")
        self.assertEqual(code(self.fs.list, alice_now, self.pid), ("SPECIAL_ACCESS_REQUIRED", 403))
        counts = self.d.worker().reconcile()
        self.assertEqual(counts["suspended"], 1)
        self.assertEqual(self.d.h.conn.execute("SELECT status FROM dv_workspaces WHERE project_id = ?", (self.pid,)).fetchone()[0], "suspended")
        # restore access -> workspace comes back, files intact
        self.d.h.conn.execute("UPDATE entitlements SET revoked_at = NULL WHERE user_id = ?", (self.alice.user_id,))
        self.d.h.conn.execute("UPDATE users SET authz_version = authz_version + 1 WHERE id = ?", (self.alice.user_id,))
        back = self.d.refresh("alice@example.com")
        self.assertEqual(self.fs.read(back, self.pid, "a.py")["content"], "1\n")
        self.fs.write(back, self.pid, "b.py", "2\n", None)
        self.assertEqual(self.d.h.conn.execute("SELECT status FROM dv_workspaces WHERE project_id = ?", (self.pid,)).fetchone()[0], "active")

    def test_secrets_are_encrypted_write_only_and_never_in_history(self):
        sec = self.d.svc(SecretService)
        made = sec.create(self.alice, self.pid, "API_KEY", "super-secret-value-123")
        self.assertNotIn("value", made)
        self.assertNotIn("super-secret-value-123", str(sec.list(self.alice, self.pid)))
        raw = self.d.h.conn.execute("SELECT ciphertext FROM dv_secrets").fetchone()[0]
        self.assertNotIn("super-secret-value-123", raw)
        dump = " ".join(str(dict(r)) for r in self.d.h.conn.execute("SELECT * FROM dv_events").fetchall())
        self.assertNotIn("super-secret-value-123", dump)
        for bad in ("PATH", "HOME", "PREDICTAI_X", "1abc", "has space", ""):
            self.assertEqual(code(sec.create, self.alice, self.pid, bad, "v")[1], 422, bad)
        self.assertEqual(code(sec.list, self.bob, self.pid)[1], 404)
        sec.revoke(self.alice, self.pid, made["id"])
        self.assertEqual(self.d.h.conn.execute("SELECT ciphertext FROM dv_secrets").fetchone()[0], "")


if __name__ == "__main__":
    unittest.main()
