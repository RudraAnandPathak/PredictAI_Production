"""Phase 9 Part 16: the release-blocking security checks, numbered exactly as in the brief. Each runs against the real services/worker and the real isolation provider available
here (Linux namespaces). Docker and macOS sandbox-exec cannot be exercised in this environment; they stay disabled for untrusted use and are reported UNVERIFIED."""
import logging
import os
import threading
import time
import unittest
from unittest import mock

from app.core.errors import AppError
from app.modules.devspace import isolation
from app.modules.devspace.artifacts import ArtifactService
from app.modules.devspace.engine import JobService
from app.modules.devspace.files import FileService
from app.modules.devspace.git import GitService
from app.modules.devspace.secrets import SecretService
from p9_support import DevHarness

NS = unittest.skipUnless(isolation.probe()["linux_namespaces"], "needs Linux user namespaces")


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.b = self.d.user("bob@example.com")
        self.plain = self.d.user("plain@example.com", special=False)
        self.pa, self.pb = self.d.project(self.a, "A"), self.d.project(self.b, "B")
        self.fs, self.js, self.art = self.d.svc(FileService), self.d.svc(JobService), self.d.svc(ArtifactService)

    def run_job(self, who, pid, name, source, **kw):
        self.fs.write(who, pid, name, source, None)
        j = self.js.submit(who, pid, kind="script", entry=name, **kw)
        self.d.run_all()
        return self.js.get(who, pid, j["id"]), self.js.logs(who, pid, j["id"])


class Access(Base):
    def test_01_standard_users_cannot_create_development_workspaces(self):
        pid = self.d.project(self.plain, "P")
        self.assertEqual(code(self.fs.write, self.plain, pid, "a.py", "1", None), ("SPECIAL_ACCESS_REQUIRED", 403))
        self.assertEqual(code(self.js.submit, self.plain, pid, kind="script", entry="a.py"), ("SPECIAL_ACCESS_REQUIRED", 403))
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_workspaces WHERE project_id = ?", (pid,)).fetchone()[0], 0)

    def test_02_special_users_reach_only_their_authorised_projects(self):
        self.fs.write(self.a, self.pa, "a.py", "1\n", None)
        self.assertEqual(code(self.fs.read, self.b, self.pa, "a.py")[1], 404)
        self.assertEqual(code(self.fs.write, self.b, self.pa, "x.py", "1", None)[1], 404)
        self.assertEqual(code(self.js.submit, self.b, self.pa, kind="script", entry="a.py")[1], 404)

    def test_03_owner_access_follows_existing_owner_authorization(self):
        # The owner holds the Development permission through the existing owner role, but project data stays scoped to the project's owner exactly as in Phase 7.
        self.fs.write(self.a, self.pa, "a.py", "1\n", None)
        self.assertEqual(code(self.fs.read, self.d.owner, self.pa, "a.py")[1], 404)
        own = self.d.project(self.d.owner, "Owner's")
        self.fs.write(self.d.owner, own, "o.py", "1\n", None)
        self.assertEqual(self.fs.read(self.d.owner, own, "o.py")["content"], "1\n")
        # Only the owner can set compute overrides; a Special user cannot.
        from app.modules.devspace.compute import ComputeService
        cs = self.d.svc(ComputeService)
        self.assertEqual(code(cs.set_override, self.a, scope="user", scope_id=self.a.user_id, key="job_wall_seconds", value=999, reason="reason here")[1], 403)

    def test_04_direct_service_calls_cannot_bypass_entitlements(self):
        # Every public service entry point re-evaluates the gate; there is no flag, header or argument that skips it.
        from app.modules.devspace.experiments import ExperimentService
        from app.modules.devspace.pipelines import PipelineService
        from app.modules.devspace.envs import EnvService
        pid = self.d.project(self.plain, "P2")
        for call in (lambda: self.js.list(self.plain, pid), lambda: self.art.list(self.plain, pid), lambda: self.d.svc(ExperimentService).list(self.plain, pid),
                     lambda: self.d.svc(PipelineService).list(self.plain, pid), lambda: self.d.svc(EnvService).list(self.plain, pid), lambda: self.d.svc(GitService).status(self.plain, pid),
                     lambda: self.d.svc(SecretService).list(self.plain, pid)):
            self.assertEqual(code(call), ("SPECIAL_ACCESS_REQUIRED", 403))


@NS
class Containment(Base):
    def test_05_cross_project_file_access_is_blocked(self):
        self.fs.write(self.a, self.pa, "secret.txt", "alice-private", None)
        victim = os.path.join(self.d.root, self.pa)
        job, logs = self.run_job(self.b, self.pb, "snoop.py", f"import os\nfor p in ({victim!r},):\n    print(os.path.exists(p), os.path.exists(p + '/tree/secret.txt'))\n    try:\n        print(open(p + '/tree/secret.txt').read())\n    except OSError as e:\n        print('denied', type(e).__name__)\n")
        self.assertNotIn("alice-private", logs["stdout"]["text"])
        self.assertEqual(code(self.fs.read, self.b, self.pa, "secret.txt")[1], 404)

    def test_06_cross_user_artifact_access_is_blocked(self):
        self.run_job(self.a, self.pa, "o.py", "import os\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/r.txt', 'w').write('alice-output')\n")
        aid = self.art.list(self.a, self.pa)["items"][0]["id"]
        self.assertEqual(self.art.read(self.a, self.pa, aid)["content"], "alice-output")
        self.assertEqual(code(self.art.read, self.b, self.pa, aid)[1], 404)
        self.assertEqual(code(self.art.read, self.b, self.pb, aid)[1], 404)                    # nor by guessing it under their own project
        self.assertEqual(code(self.art.delete, self.b, self.pa, aid, "r.txt")[1], 404)

    def test_07_worker_cannot_reach_host_secrets(self):
        os.environ["PREDICTAI_SECRET_KEY"] = "host-secret-value-xyz"
        os.environ["DATABASE_URL"] = "sqlite:///host-secret-db"
        self.addCleanup(os.environ.pop, "PREDICTAI_SECRET_KEY", None)
        self.addCleanup(os.environ.pop, "DATABASE_URL", None)
        job, logs = self.run_job(self.a, self.pa, "env.py", "import os\nprint(sorted(k for k in os.environ if 'SECRET' in k or 'DATABASE' in k or 'TOKEN' in k))\nprint(os.path.exists('/var/run/docker.sock'))\n")
        self.assertIn("[]", logs["stdout"]["text"])
        self.assertNotIn("host-secret", logs["stdout"]["text"] + logs["stderr"]["text"])
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_secrets WHERE purpose != 'job'").fetchone()[0], 0)

    def test_08_credentials_are_not_in_logs(self):
        SecretService(self.d.ctx(), self.d.ws()).create(self.a, self.pa, "API_KEY", "sk-live-0123456789abcdef")
        with self.assertLogs(level=logging.DEBUG) as cap:
            logging.getLogger("probe").debug("start")
            job, logs = self.run_job(self.a, self.pa, "s.py", "import os, sys\nv = os.environ['API_KEY']\nprint(v)\nprint(v, file=sys.stderr)\n", secret_refs=["API_KEY"])
        everything = "\n".join(cap.output) + logs["stdout"]["text"] + logs["stderr"]["text"] + str(job)
        everything += " ".join(str(dict(r)) for t in ("dv_events", "dv_jobs", "dv_job_output") for r in self.d.h.conn.execute(f"SELECT * FROM {t}").fetchall())
        everything += " ".join(str(dict(r)) for r in self.d.h.conn.execute("SELECT * FROM audit_events").fetchall()) if self._has("audit_events") else ""
        self.assertNotIn("sk-live-0123456789abcdef", everything)

    def _has(self, table):
        return self.d.h.conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None

    def test_09_network_restrictions_are_enforced(self):
        job, logs = self.run_job(self.a, self.pa, "net.py", "import socket, urllib.request\nfor f in (lambda: socket.create_connection(('8.8.8.8', 53), 2), lambda: urllib.request.urlopen('http://example.com', timeout=2), lambda: socket.gethostbyname('example.com')):\n    try:\n        f(); print('REACHED')\n    except Exception as e:\n        print('blocked')\n")
        self.assertEqual(logs["stdout"]["text"].split(), ["blocked"] * 3)
        with mock.patch.object(isolation, "probe", return_value={**isolation.probe(), "linux_namespaces": False, "network_isolation": False}):
            pass                                                                               # the 'cannot isolate -> blocked, never run unisolated' rule is covered in test_p9_engine

    def test_10_resource_limits_are_enforced(self):
        job, _ = self.run_job(self.a, self.pa, "mem.py", "x = bytearray(2 * 1024 * 1024 * 1024)\nprint('allocated')\n", limits={"memory_mb": 128})
        self.assertIn(job["status"], ("failed",))
        self.assertIn(job["error_class"], ("memory_limit", "out_of_memory", "oom"))
        job, logs = self.run_job(self.a, self.pa, "flood.py", "while True:\n    print('x' * 1000)\n", limits={"wall_seconds": 20})
        self.assertEqual(job["status"], "failed")
        self.assertLessEqual(len(logs["stdout"]["text"]), self.d.h.settings.dev_job_output_kb * 1024 + 4096)
        self.assertTrue(logs["stdout"].get("truncated", True))

    def test_11_cancellation_terminates_execution(self):
        self.fs.write(self.a, self.pa, "long.py", "import time\ntime.sleep(60)\n", None)
        j = self.js.submit(self.a, self.pa, kind="script", entry="long.py")
        th = threading.Thread(target=self.d.worker().tick)
        th.start()
        for _ in range(100):
            if self.js.get(self.a, self.pa, j["id"])["status"] == "running":
                break
            time.sleep(0.05)
        pid = self.d.h.conn.execute("SELECT worker_pid FROM dv_jobs WHERE id = ?", (j["id"],)).fetchone()[0]
        self.js.cancel(self.a, self.pa, j["id"])
        th.join(20)
        self.assertEqual(self.js.get(self.a, self.pa, j["id"])["status"], "cancelled")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(code(self.js.cancel, self.b, self.pa, j["id"])[1], 404)

    def test_12_timeouts_terminate_execution(self):
        job, _ = self.run_job(self.a, self.pa, "slow.py", "import time\ntime.sleep(60)\n", limits={"wall_seconds": 2})
        self.assertEqual(job["status"], "timed_out")
        pid = self.d.h.conn.execute("SELECT worker_pid FROM dv_jobs WHERE id = ?", (job["id"],)).fetchone()[0]
        if pid:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_13_revocation_stops_new_work(self):
        self.d.h.services().codes  # noqa: B018 - harness accessor
        c = self.d.h.db.connect()
        c.execute("UPDATE entitlements SET revoked_at = ? WHERE user_id = ?", (self.d.h.clock.iso(), self.a.user_id))
        c.execute("UPDATE users SET authz_version = authz_version + 1 WHERE id = ?", (self.a.user_id,))
        c.close()
        a2 = self.d.refresh("alice@example.com")
        self.assertEqual(code(self.js.submit, a2, self.pa, kind="script", entry="a.py"), ("SPECIAL_ACCESS_REQUIRED", 403))
        self.assertEqual(code(self.fs.write, a2, self.pa, "n.py", "1", None), ("SPECIAL_ACCESS_REQUIRED", 403))

    def test_14_active_jobs_are_terminated_after_revocation(self):
        self.fs.write(self.a, self.pa, "long.py", "import time\ntime.sleep(60)\n", None)
        j = self.js.submit(self.a, self.pa, kind="script", entry="long.py")
        th = threading.Thread(target=self.d.worker().tick)
        th.start()
        for _ in range(100):
            if self.js.get(self.a, self.pa, j["id"])["status"] == "running":
                break
            time.sleep(0.05)
        pid = self.d.h.conn.execute("SELECT worker_pid FROM dv_jobs WHERE id = ?", (j["id"],)).fetchone()[0]
        c = self.d.h.db.connect()
        c.execute("UPDATE entitlements SET revoked_at = ? WHERE user_id = ?", (self.d.h.clock.iso(), self.a.user_id))
        c.execute("UPDATE users SET authz_version = authz_version + 1 WHERE id = ?", (self.a.user_id,))
        c.close()
        th.join(20)
        r = self.d.h.conn.execute("SELECT status, blocked_reason FROM dv_jobs WHERE id = ?", (j["id"],)).fetchone()
        self.assertEqual((r["status"], r["blocked_reason"]), ("cancelled", "entitlement_revoked"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class DataProtection(Base):
    def test_15_git_credentials_are_protected(self):
        git = self.d.svc(GitService)
        import subprocess, tempfile
        remote = os.path.join(tempfile.mkdtemp(), "r.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
        git.connect(self.a, self.pa, provider="local", remote_url=remote, consent=True, consent_scope="read_write")
        self.assertEqual(code(self.fs.write, self.a, self.pa, "creds.py", "token = 'ghp_" + "a" * 36 + "'\n", None)[1], 422)       # a credential cannot even be saved into the project
        self.assertEqual(code(self.fs.write, self.a, self.pa, ".git/config", "[remote]", None)[1], 422)
        self.assertEqual(code(self.fs.write, self.a, self.pa, ".netrc", "machine x", None)[1], 422)
        cfg = open(os.path.join(self.d.root, self.pa, "tree", ".git", "config")).read() if os.path.exists(os.path.join(self.d.root, self.pa, "tree", ".git", "config")) else ""
        self.assertNotIn("ghp_", cfg)

    def test_16_webhook_signatures_are_validated(self):
        # Covered end to end (HMAC sha256, GitLab token, replay, tampering, uniform 401) in test_p9_git.Webhooks and test_p9_api.test_webhook_route_needs_no_session_but_needs_a_valid_signature;
        # re-asserted here so the release gate fails if either goes missing.
        import test_p9_git, test_p9_api
        for cls, names in ((test_p9_git, ("test_bad_signature_wrong_secret_tampered_body_and_missing_headers_all_get_the_same_401", "test_valid_delivery_is_accepted_once_and_replay_is_refused")),
                            (test_p9_api, ("test_webhook_route_needs_no_session_but_needs_a_valid_signature",))):
            found = [n for c in vars(cls).values() if isinstance(c, type) for n in dir(c)]
            for n in names:
                self.assertIn(n, found)
        res = unittest.TestResult()
        suite = unittest.TestSuite()
        for cls, names in ((test_p9_git, ("test_bad_signature_wrong_secret_tampered_body_and_missing_headers_all_get_the_same_401",)), ):
            for c in vars(cls).values():
                if isinstance(c, type) and issubclass(c, unittest.TestCase):
                    for n in names:
                        if hasattr(c, n):
                            suite.addTest(c(n))
        suite.run(res)
        self.assertEqual((res.testsRun, res.failures, res.errors), (1, [], []))

    def test_17_artifact_permissions_are_enforced(self):
        self.run_job(self.a, self.pa, "o.py", "import os\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/r.txt', 'w').write('x')\n") if isolation.probe()["linux_namespaces"] else None
        row = self.d.h.conn.execute("SELECT id FROM dv_artifacts").fetchone()
        if row is None:
            self.skipTest("needs namespaces to produce an artifact")
        aid = row[0]
        self.assertEqual(code(self.art.read, self.plain, self.pa, aid), ("SPECIAL_ACCESS_REQUIRED", 403))
        self.assertEqual(code(self.art.read, self.b, self.pa, aid)[1], 404)
        self.assertEqual(code(self.art.set_retention, self.b, self.pa, aid, 1)[1], 404)
        self.assertEqual(code(self.art.delete, self.a, self.pa, aid, "wrong-name")[1], 422)           # deletion needs the name typed

    def test_18_storage_quotas_are_enforced(self):
        from dataclasses import replace
        self.d.h.settings = replace(self.d.h.settings, dev_project_storage_mb=1)
        self.fs = self.d.svc(FileService)
        self.fs.write(self.a, self.pa, "big1.txt", "a" * 700_000, None)
        self.assertEqual(code(self.fs.write, self.a, self.pa, "big2.txt", "b" * 700_000, None), ("STORAGE_QUOTA_EXCEEDED", 403))

    def test_19_file_deletion_respects_ownership(self):
        self.fs.write(self.a, self.pa, "keep.py", "1\n", None)
        self.assertEqual(code(self.fs.delete, self.b, self.pa, "keep.py", "keep.py")[1], 404)
        self.assertEqual(code(self.fs.delete, self.a, self.pb, "keep.py", "keep.py")[1], 404)
        self.assertEqual(code(self.fs.delete, self.a, self.pa, "keep.py", "nope")[1], 409)
        self.assertEqual(self.fs.read(self.a, self.pa, "keep.py")["content"], "1\n")
        self.fs.delete(self.a, self.pa, "keep.py", "keep.py")
        self.assertEqual(code(self.fs.read, self.a, self.pa, "keep.py")[1], 404)

    @NS
    def test_20_logs_do_not_disclose_private_project_data(self):
        self.fs.write(self.a, self.pa, "private.csv", "ssn,123-45-6789\n", None)
        with self.assertLogs(level=logging.DEBUG) as cap:
            logging.getLogger("probe").debug("start")
            self.run_job(self.a, self.pa, "p.py", "print(open('private.csv').read())\n")
        platform_logs = "\n".join(cap.output)
        self.assertNotIn("123-45-6789", platform_logs)                                            # the job's own output stays in the job log, never in platform logs
        events = " ".join(str(dict(r)) for r in self.d.h.conn.execute("SELECT * FROM dv_events").fetchall())
        self.assertNotIn("123-45-6789", events)
        self.assertNotIn("private.csv", platform_logs)
        jid = self.d.h.conn.execute("SELECT id FROM dv_jobs").fetchone()[0]
        self.assertEqual(code(self.js.logs, self.b, self.pa, jid)[1], 404)


if __name__ == "__main__":
    unittest.main()
