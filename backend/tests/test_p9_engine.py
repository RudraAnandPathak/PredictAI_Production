"""Phase 9: the isolated execution engine. Real child processes, run through the real worker, against the real isolation provider available here (Linux namespaces).
What this cannot show (Docker, macOS sandbox-exec) is covered only by command-construction tests and is reported as unverified."""
import json
import os
import subprocess
import threading
import time
import unittest
from dataclasses import replace
from unittest import mock

from app.core.errors import AppError
from app.modules.devspace import isolation
from app.modules.devspace.engine import JobService, TRANSITIONS, transition
from app.modules.devspace.files import FileService
from app.modules.devspace.secrets import SecretService
from p9_support import DevHarness


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class EngineBase(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.fs, self.js = self.d.svc(FileService), self.d.svc(JobService)

    def run_script(self, name, source, **kw):
        self.fs.write(self.a, self.pid, name, source, None)
        job = self.js.submit(self.a, self.pid, kind="script", entry=name, **kw)
        self.d.run_all()
        return self.js.get(self.a, self.pid, job["id"]), self.js.logs(self.a, self.pid, job["id"])


@unittest.skipUnless(isolation.probe()["linux_namespaces"], "needs Linux user namespaces for the no-network guarantee")
class Lifecycle(EngineBase):
    def test_success_records_everything_and_cleans_up(self):
        job, logs = self.run_script("ok.py", "import os\nprint('hello')\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/out.txt', 'w').write('result')\n")
        self.assertEqual((job["status"], job["exit_code"], job["error_class"]), ("succeeded", 0, None))
        self.assertEqual(logs["stdout"]["text"], "hello\n")
        self.assertTrue(job["tree_sha256"] and job["isolation"].startswith("local_trusted"))
        arts = self.js.artifacts(self.a, self.pid, job["id"])["items"]
        self.assertEqual([x["name"] for x in arts], ["out.txt"])
        row = self.d.h.conn.execute("SELECT * FROM dv_artifacts").fetchone()
        self.assertEqual(json.loads(row["provenance"])["job_id"], job["id"])
        self.assertFalse(os.path.exists(os.path.join(self.d.root, self.pid, "jobs", job["id"])))        # job folder removed

    def test_only_legal_state_transitions_exist(self):
        for frm, tos in TRANSITIONS.items():
            if frm in ("succeeded", "failed", "timed_out", "cancelled"):
                self.assertEqual(tos, set(), frm)
        self.assertNotIn("succeeded", TRANSITIONS["queued"])
        self.assertNotIn("cancelled", TRANSITIONS["running"])        # a running job must pass through cancelling
        job, _ = self.run_script("ok.py", "print(1)\n")
        self.assertFalse(transition(self.d.h.conn, job["id"], "running"))            # a finished job cannot move again

    def test_runtime_error_syntax_error_and_missing_dependency_are_classified(self):
        self.assertEqual(self.run_script("r.py", "raise ValueError('boom')\n")[0]["error_class"], "runtime_error")
        self.assertEqual(self.run_script("s.py", "def x(:\n")[0]["error_class"], "syntax_error")
        j, _ = self.run_script("i.py", "import definitely_not_installed_pkg\n")
        self.assertEqual(j["error_class"], "dependency_missing")
        self.assertIn("recovery", j)
        self.assertIn("edit_environment", j["recovery"]["actions"])

    def test_network_is_blocked(self):
        _, logs = self.run_script("n.py", "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    print('CONNECTED')\nexcept OSError:\n    print('blocked')\n")
        self.assertEqual(logs["stdout"]["text"].strip(), "blocked")

    def test_environment_is_scrubbed(self):
        os.environ["PREDICTAI_SECRET_KEY"] = "host-secret-should-never-leak"
        self.addCleanup(os.environ.pop, "PREDICTAI_SECRET_KEY", None)
        _, logs = self.run_script("e.py", "import os, json\nprint(json.dumps(sorted(os.environ)))\n")
        names = json.loads(logs["stdout"]["text"])
        self.assertNotIn("PREDICTAI_SECRET_KEY", names)
        self.assertEqual(sorted(set(names) - {"LC_CTYPE"}), sorted(["HOME", "LANG", "PATH", "PREDICTAI_DATA_DIR", "PREDICTAI_JOB_ID", "PREDICTAI_OUTPUT_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "TMPDIR"]))

    def test_secrets_reach_only_the_named_job_and_are_redacted_from_logs_and_artifacts(self):
        SecretService(self.d.ctx(), self.d.ws()).create(self.a, self.pid, "MY_TOKEN", "tok-1234567890-abcdef")
        job, logs = self.run_script("t.py", "import os\nv = os.environ['MY_TOKEN']\nprint('value=' + v)\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/o.txt', 'w').write(v)\n", secret_refs=["MY_TOKEN"])
        self.assertNotIn("tok-1234567890-abcdef", logs["stdout"]["text"])
        self.assertIn("***", logs["stdout"]["text"])
        art = self.d.h.conn.execute("SELECT sha256 FROM dv_artifacts").fetchone()[0]
        stored = open(os.path.join(self.d.root, self.pid, "objects", art[:2], art)).read()
        self.assertNotIn("tok-1234567890-abcdef", stored)
        _, other = self.run_script("t2.py", "import os\nprint(os.environ.get('MY_TOKEN'))\n")
        self.assertEqual(other["stdout"]["text"].strip(), "None")                                     # not named -> not given
        dump = " ".join(str(dict(r)) for r in self.d.h.conn.execute("SELECT * FROM dv_events").fetchall())
        self.assertNotIn("tok-1234567890-abcdef", dump)
        self.assertEqual(code(self.js.submit, self.a, self.pid, kind="script", entry="t.py", secret_refs=["NOPE"])[1], 404)

    def test_notebook_job_executes_cells_and_saves_outputs_back(self):
        nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [{"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": "x = 21\nprint(x * 2)"},
                                                                         {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": "x"}]}
        self.fs.write(self.a, self.pid, "n.ipynb", json.dumps(nb), None)
        job = self.js.submit(self.a, self.pid, kind="notebook", entry="n.ipynb")
        self.d.run_all()
        self.assertEqual(self.js.get(self.a, self.pid, job["id"])["status"], "succeeded")
        saved = json.loads(self.fs.read(self.a, self.pid, "n.ipynb")["content"])
        self.assertEqual("".join(saved["cells"][0]["outputs"][0]["text"]).strip(), "42")
        self.assertEqual(self.fs.read(self.a, self.pid, "n.ipynb")["version"], 2)

    def test_data_files_are_read_only_copies_of_the_projects_own_data_only(self):
        from test_workspace_uploads import upload
        ws = self.d.ws()
        bob = self.d.user("bob@example.com")
        bpid = ws.projects.create(bob, "Bobs")["id"]
        mine = upload(ws, self.a, self.pid, "mine.csv", b"a,b\n1,2\n")["asset"]
        upload(ws, bob, bpid, "bobs_secret.csv", b"x,y\n9,9\n")
        job, logs = self.run_script("d.py", "import os\nprint(sorted(os.listdir(os.environ['PREDICTAI_DATA_DIR'])))\n"
                                    "p = os.environ['PREDICTAI_DATA_DIR'] + '/mine.csv'\nprint(open(p).read().strip().replace(chr(10), '|'))\n"
                                    "try:\n    open(p, 'w').write('tampered')\n    print('WROTE')\nexcept OSError:\n    print('read-only')\n")
        out = logs["stdout"]["text"].splitlines()
        self.assertEqual(out[0], "['mine.csv']")                     # bob's file is not reachable
        self.assertEqual(out[1], "a,b|1,2")
        if os.geteuid() != 0:                                        # (as root, file modes do not bind, so this is only checkable for a normal user)
            self.assertEqual(out[2], "read-only")
        # whatever the job did to its COPY, the original in the Data Workspace is unchanged
        row = self.d.h.conn.execute("SELECT v.storage_key, v.checksum_sha256 FROM asset_versions v WHERE v.asset_id = ?", (mine["id"],)).fetchone()
        import hashlib
        self.assertEqual(hashlib.sha256(self.d.h.storage.local_path(row["storage_key"]).read_bytes()).hexdigest(), row["checksum_sha256"])

    def test_artifact_quota_stops_oversized_outputs(self):
        self.d.h.settings = replace(self.d.h.settings, dev_project_storage_mb=1)
        self.fs, self.js = self.d.svc(FileService), self.d.svc(JobService)
        job, _ = self.run_script("big.py", "import os\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/big.bin', 'wb').write(b'x' * 1_500_000)\n", limits={"wall_seconds": 20})
        self.assertEqual(self.js.artifacts(self.a, self.pid, job["id"])["items"], [])
        self.assertTrue(self.d.h.conn.execute("SELECT 1 FROM dv_events WHERE action = 'artifact.quota_denied'").fetchone())


@unittest.skipUnless(isolation.probe()["linux_namespaces"], "needs Linux user namespaces")
class Limits(EngineBase):
    def test_wall_clock_timeout_kills_the_job(self):
        t = time.time()
        job, _ = self.run_script("slow.py", "import time\ntime.sleep(60)\n", limits={"wall_seconds": 2})
        self.assertEqual((job["status"], job["error_class"]), ("timed_out", "wall_clock"))
        self.assertLess(time.time() - t, 15)

    def test_memory_limit(self):
        job, _ = self.run_script("mem.py", "x = bytearray(900 * 1024 * 1024)\n", limits={"memory_mb": 128})
        self.assertEqual((job["status"], job["error_class"]), ("failed", "memory_limit"))

    def test_output_flood_is_stopped_and_truncated(self):
        job, logs = self.run_script("flood.py", "while True:\n    print('x' * 1000)\n")
        self.assertEqual(job["error_class"], "output_limit")
        self.assertLessEqual(len(logs["stdout"]["text"]), 1024 * 1024)

    def test_process_limit_and_no_orphans(self):
        job, _ = self.run_script("fork.py", "import os, time\nfor _ in range(200):\n    if os.fork() == 0:\n        time.sleep(30)\n        os._exit(0)\ntime.sleep(30)\n")
        self.assertEqual(job["error_class"], "process_limit")
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", "fork.py"], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [])

    def test_cpu_limit(self):
        job, _ = self.run_script("cpu.py", "while True:\n    pass\n", limits={"cpu_seconds": 2, "wall_seconds": 30})
        self.assertEqual(job["error_class"], "cpu_limit")

    def test_requested_limits_can_only_lower_never_raise(self):
        job = self.js.submit(self.a, self.pid, kind="command", argv=["python", "-c", "print(1)"], limits={"wall_seconds": 99999, "memory_mb": 99999})
        self.assertEqual(job["limits"]["wall_seconds"], self.d.h.settings.dev_job_wall_seconds)
        self.assertEqual(job["limits"]["memory_mb"], self.d.h.settings.dev_job_memory_mb)

    def test_cancel_a_running_job_goes_through_cancelling(self):
        self.fs.write(self.a, self.pid, "long.py", "import time\ntime.sleep(60)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="long.py")
        w = self.d.worker()
        th = threading.Thread(target=w.tick)
        th.start()
        for _ in range(100):
            if self.js.get(self.a, self.pid, job["id"])["status"] == "running":
                break
            time.sleep(0.05)
        self.assertEqual(self.js.cancel(self.a, self.pid, job["id"])["status"], "cancelling")
        th.join(20)
        done = self.js.get(self.a, self.pid, job["id"])
        self.assertEqual((done["status"], done["error_class"]), ("cancelled", "cancelled"))
        self.assertEqual(code(self.js.cancel, self.a, self.pid, job["id"])[0], "JOB_NOT_CANCELLABLE")

    def test_cancel_a_queued_job_never_starts_it(self):
        self.fs.write(self.a, self.pid, "q.py", "print(1)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="q.py")
        self.assertEqual(self.js.cancel(self.a, self.pid, job["id"])["status"], "cancelled")
        self.assertEqual(self.d.run_all(), [])

    def test_revoking_access_stops_a_running_job_and_blocks_queued_ones(self):
        self.fs.write(self.a, self.pid, "long.py", "import time\ntime.sleep(60)\n", None)
        running = self.js.submit(self.a, self.pid, kind="script", entry="long.py")
        self.d.h.clock.advance(seconds=1)                          # the frozen test clock gives equal timestamps; order must be unambiguous
        queued = self.js.submit(self.a, self.pid, kind="script", entry="long.py")
        w = self.d.worker()
        th = threading.Thread(target=w.tick)
        th.start()
        for _ in range(100):
            if self.js.get(self.a, self.pid, running["id"])["status"] == "running":
                break
            time.sleep(0.05)
        c = self.d.h.db.connect()
        c.execute("UPDATE entitlements SET revoked_at = ? WHERE user_id = ?", (self.d.h.clock.iso(), self.a.user_id))
        c.execute("UPDATE users SET authz_version = authz_version + 1 WHERE id = ?", (self.a.user_id,))
        c.close()
        th.join(20)
        r = self.d.h.conn.execute("SELECT status, blocked_reason FROM dv_jobs WHERE id = ?", (running["id"],)).fetchone()
        self.assertEqual((r["status"], r["blocked_reason"]), ("cancelled", "entitlement_revoked"))
        self.d.worker().tick()
        q = self.d.h.conn.execute("SELECT status, blocked_reason FROM dv_jobs WHERE id = ?", (queued["id"],)).fetchone()
        self.assertEqual((q["status"], q["blocked_reason"]), ("blocked", "entitlement_revoked"))
        self.assertEqual(code(self.js.submit, self.d.refresh("alice@example.com"), self.pid, kind="script", entry="long.py")[0], "SPECIAL_ACCESS_REQUIRED")

    def test_a_dead_worker_is_recovered_and_a_bounded_retry_is_queued(self):
        self.fs.write(self.a, self.pid, "p.py", "print(1)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="p.py", max_attempts=2)
        c = self.d.h.conn
        c.execute("UPDATE dv_jobs SET status = 'running', attempts = 1, started_at = ?, heartbeat_at = ?, worker_pid = 99999999 WHERE id = ?", (self.d.h.clock.iso(), self.d.h.clock.iso(), job["id"]))
        self.d.h.clock.advance(minutes=5)
        self.d.worker().recover_stale()
        first = self.js.get(self.a, self.pid, job["id"])
        self.assertEqual((first["status"], first["error_class"]), ("failed", "worker_lost"))
        self.assertIn("safe to run it again", first["recovery"]["what_failed"])
        retry = c.execute("SELECT * FROM dv_jobs WHERE retry_of = ?", (job["id"],)).fetchone()
        self.assertEqual((retry["status"], retry["attempts"]), ("queued", 1))
        self.d.run_all()
        self.assertEqual(self.js.get(self.a, self.pid, retry["id"])["status"], "succeeded")

    def test_retry_is_bounded_and_exhausted_attempts_dead_letter(self):
        self.fs.write(self.a, self.pid, "f.py", "raise SystemExit(3)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="f.py")
        self.d.run_all()
        ids = [job["id"]]
        for _ in range(3):
            ids.append(self.js.retry(self.a, self.pid, ids[-1])["id"])
            self.d.run_all()
        self.assertEqual(code(self.js.retry, self.a, self.pid, ids[-1])[0], "RETRY_LIMIT")


class Admission(EngineBase):
    def test_queue_depth_and_daily_budget_are_enforced(self):
        self.d.h.settings = replace(self.d.h.settings, dev_queue_depth=2)
        js = self.d.svc(JobService)
        self.fs.write(self.a, self.pid, "q.py", "print(1)\n", None)
        js.submit(self.a, self.pid, kind="script", entry="q.py")
        js.submit(self.a, self.pid, kind="script", entry="q.py")
        self.assertEqual(code(js.submit, self.a, self.pid, kind="script", entry="q.py")[0], "QUEUE_FULL")
        self.d.h.settings = replace(self.d.h.settings, dev_queue_depth=20, dev_user_cpu_seconds_per_day=1)
        self.d.h.conn.execute("UPDATE dv_jobs SET cpu_ms = 5000, status = 'succeeded'")
        self.assertEqual(code(self.d.svc(JobService).submit, self.a, self.pid, kind="script", entry="q.py")[0], "COMPUTE_BUDGET_EXHAUSTED")

    def test_input_validation(self):
        self.fs.write(self.a, self.pid, "x.py", "1\n", None)
        for kw in ({"kind": "shell", "entry": "x.py"}, {"kind": "script", "entry": "../x.py"}, {"kind": "script", "entry": "missing.py"}, {"kind": "script", "entry": "x.ipynb"},
                   {"kind": "command", "argv": ["sh", "-c", "id"]}, {"kind": "command", "argv": ["python"]}, {"kind": "script", "entry": "x.py", "priority": 0},
                   {"kind": "script", "entry": "x.py", "env_id": "nope"}):
            self.assertIn(code(self.js.submit, self.a, self.pid, **kw)[1], (404, 422), kw)

    def test_execution_off_refuses_and_a_blocked_job_says_why(self):
        self.fs.write(self.a, self.pid, "x.py", "print(1)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="x.py")
        self.d.h.settings = replace(self.d.h.settings, dev_execution="off")
        self.assertEqual(code(self.d.svc(JobService).submit, self.a, self.pid, kind="script", entry="x.py")[0], "EXECUTION_DISABLED")
        w = self.d.worker()
        w.provider = None
        w.settings = self.d.h.settings
        w.tick()
        got = self.d.svc(JobService).get(self.a, self.pid, job["id"])
        self.assertEqual((got["status"], got["blocked_reason"]), ("blocked", "execution_disabled"))
        self.assertIn("switched off", got["recovery"]["what_failed"])

    def test_a_job_is_blocked_not_run_when_network_isolation_cannot_be_enforced(self):
        self.fs.write(self.a, self.pid, "x.py", "print('SHOULD NOT RUN')\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="x.py")
        w = self.d.worker()
        with mock.patch.object(type(w.provider), "network_isolation", lambda self: None):
            w.tick()
        got = self.js.get(self.a, self.pid, job["id"])
        self.assertEqual((got["status"], got["blocked_reason"]), ("blocked", "network_isolation_unavailable"))
        self.assertNotIn("SHOULD NOT RUN", self.js.logs(self.a, self.pid, job["id"])["stdout"]["text"])

    def test_fair_queue_prefers_the_person_with_fewer_running_jobs_and_priority(self):
        bob = self.d.user("bob@example.com")
        bpid = self.d.project(bob)
        fb = self.d.svc(FileService)
        fb.write(bob, bpid, "b.py", "print('b')\n", None)
        self.fs.write(self.a, self.pid, "a.py", "print('a')\n", None)
        first = self.js.submit(self.a, self.pid, kind="script", entry="a.py", priority=500)
        second = self.js.submit(bob, bpid, kind="script", entry="b.py", priority=100)
        order = self.d.run_all()
        self.assertEqual(order[0], second["id"])                      # lower number = higher priority
        self.assertEqual(order[1], first["id"])

    def test_one_users_jobs_are_invisible_to_another(self):
        bob = self.d.user("bob@example.com")
        self.fs.write(self.a, self.pid, "a.py", "print(1)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="a.py")
        self.d.run_all()
        jb = self.d.svc(JobService)
        for call in (lambda: jb.get(bob, self.pid, job["id"]), lambda: jb.logs(bob, self.pid, job["id"]), lambda: jb.cancel(bob, self.pid, job["id"]), lambda: jb.artifacts(bob, self.pid, job["id"])):
            self.assertEqual(code(call)[1], 404)


class IsolationHonesty(unittest.TestCase):
    def test_docker_command_never_mounts_the_socket_or_puts_secrets_in_argv(self):
        p = isolation.DockerProvider("python:3.12-slim")
        argv = p.wrap(["python", "/boot/_devboot.py", "script"], workdir="/w", env={"MY_TOKEN": "s3cr3t-value", "PREDICTAI_JOB_ID": "j", "PATH": "/x", "PREDICTAI_OUTPUT_DIR": "/o", "DOCKER_HOST": "unix:///x"},
                      limits={"max_procs": 32, "memory_mb": 256}, data_dir="/d", out_dir="/o", boot_dir="/b")
        joined = " ".join(argv)
        self.assertNotIn("docker.sock", joined)
        self.assertNotIn("s3cr3t-value", joined)
        for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges", "--pids-limit", "--memory", "--user"):
            self.assertIn(flag, argv)
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("DOCKER_HOST", joined)
        self.assertIn("/d:/data:ro", joined)
        self.assertEqual(argv[0:2], ["docker", "run"])

    def test_no_provider_claims_multi_user_safety(self):
        from app.core.config import load_settings
        for mode in ("off", "local_trusted", "docker"):
            s = replace(load_settings({"PREDICTAI_ENV": "test"}), dev_execution=mode)
            d = isolation.describe(s)
            self.assertFalse(d["multi_user_safe"], mode)



if __name__ == "__main__":
    unittest.main()
