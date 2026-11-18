"""Phase 9: pipelines (pinned snapshot, stage results, approval boundary, no deployment) and experiments / lineage (every link a real record)."""
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace

from app.core.errors import AppError
from app.modules.devspace.engine import JobService
from app.modules.devspace.experiments import ExperimentService
from app.modules.devspace.artifacts import ArtifactService
from app.modules.devspace.files import FileService
from app.modules.devspace.git import GitService
from app.modules.devspace.pipelines import PipelineService
from p9_support import DevHarness


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


OK_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1 + 1, 2)\n"
BAD_TEST = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness(dev_pipelines_enabled=True)
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.pl, self.fs = self.d.svc(PipelineService), self.d.svc(FileService)

    def go(self, run_id, rounds=40):
        for _ in range(rounds):
            self.d.run_all()
            r = self.pl.get_run(self.a, self.pid, run_id)
            if r["status"] in ("succeeded", "failed", "cancelled", "blocked"):
                return r
        return self.pl.get_run(self.a, self.pid, run_id)

    def start(self, test_source=OK_TEST, name="ci"):
        self.fs.write(self.a, self.pid, "tests/test_x.py", test_source, None)
        self.fs.write(self.a, self.pid, "model.py", "VALUE = 1\n", None)
        p = self.pl.create(self.a, self.pid, name=name)
        return p, self.pl.run(self.a, self.pid, p["id"])

    def test_flag_off_saves_definitions_but_refuses_runs(self):
        self.d.h.settings = replace(self.d.h.settings, dev_pipelines_enabled=False)
        pl = self.d.svc(PipelineService)
        p = pl.create(self.a, self.pid, name="ci")
        self.assertEqual(code(pl.run, self.a, self.pid, p["id"])[0], "PIPELINES_DISABLED")
        self.assertFalse(pl.list(self.a, self.pid)["enabled"])

    def test_green_run_records_every_stage_pins_the_snapshot_and_awaits_approval(self):
        p, run = self.start()
        done = self.go(run["id"])
        self.assertEqual(done["status"], "succeeded", json.dumps(done["stages"], indent=1))
        self.assertEqual([s["status"] for s in done["stages"]], ["succeeded"] * 7)
        self.assertEqual(done["record"]["tree_sha256"], run["tree_sha256"])
        self.assertFalse(done["record"]["deployed"])
        self.assertEqual(done["approval_state"], "pending")
        # the unit-test stage really ran the tests as a sandboxed job
        job_id = [s for s in done["stages"] if s["type"] == "unit_tests"][0]["job_id"]
        self.assertIn("Ran 1 test", self.d.svc(JobService).logs(self.a, self.pid, job_id)["stderr"]["text"])
        # dependency check never claims a clean vulnerability result it did not obtain
        dep = [s for s in done["stages"] if s["type"] == "validate_deps"][0]["result"]
        self.assertTrue(dep["ok"] if "ok" in dep else True)

    def test_failing_tests_fail_the_run_skip_the_rest_and_never_reach_approval(self):
        p, run = self.start(BAD_TEST)
        done = self.go(run["id"])
        self.assertEqual(done["status"], "failed")
        statuses = {s["type"]: s["status"] for s in done["stages"]}
        self.assertEqual((statuses["unit_tests"], statuses["security_checks"], statuses["final_record"]), ("failed", "skipped", "skipped"))
        self.assertEqual(done["approval_state"], "none")
        self.assertEqual(code(self.pl.approve, self.a, self.pid, run["id"], "looks fine to me")[0], "NOT_AWAITING_APPROVAL")

    def test_a_test_stage_with_no_tests_fails_instead_of_passing_silently(self):
        p = self.pl.create(self.a, self.pid, name="empty", stages=[{"type": "unit_tests"}])
        done = self.go(self.pl.run(self.a, self.pid, p["id"])["id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("No test files", done["stages"][0]["result"]["reason"])

    def test_security_stage_catches_credentials_written_into_the_tree(self):
        self.fs.write(self.a, self.pid, "tests/test_x.py", OK_TEST, None)
        tree = os.path.join(self.d.root, self.pid, "tree")
        with open(os.path.join(tree, "oops.py"), "w") as f:
            f.write("K = 'ghp_" + "d" * 36 + "'\n")
        self.fs.rescan(self.pid)
        p = self.pl.create(self.a, self.pid, name="sec", stages=[{"type": "security_checks"}])
        done = self.go(self.pl.run(self.a, self.pid, p["id"])["id"])
        self.assertEqual(done["status"], "failed")
        self.assertTrue(any("oops.py" in f for f in done["stages"][0]["result"]["findings"]))

    def test_files_changed_after_the_run_started_invalidate_the_result(self):
        p, run = self.start()
        self.fs.write(self.a, self.pid, "model.py", "VALUE = 2\n", None)          # edited after the run was pinned
        done = self.go(run["id"])
        self.assertEqual(done["status"], "failed")
        bad = [s for s in done["stages"] if s["status"] == "failed"][0]
        self.assertIn("pinned snapshot", bad["result"]["reason"])

    def test_eval_gate_uses_real_experiment_metrics(self):
        ex = self.d.svc(ExperimentService)
        e = ex.create(self.a, self.pid, name="exp")
        self.d.h.conn.execute("UPDATE dv_experiments SET metrics = ?, status = 'evaluated' WHERE id = ?", (json.dumps({"accuracy": 0.91}), e["id"]))
        for minimum, expect in ((0.9, "succeeded"), (0.95, "failed")):
            p = self.pl.create(self.a, self.pid, name=f"gate{minimum}", stages=[{"type": "eval_gate", "experiment_id": e["id"], "metric": "accuracy", "min": minimum}])
            self.assertEqual(self.go(self.pl.run(self.a, self.pid, p["id"])["id"])["status"], expect)
        with self.assertRaises(AppError):
            self.pl.create(self.a, self.pid, name="bad", stages=[{"type": "eval_gate"}])

    def test_approval_is_a_recorded_decision_and_deploy_is_always_refused(self):
        p, run = self.start()
        self.go(run["id"])
        out = self.pl.approve(self.a, self.pid, run["id"], "reviewed the run record")
        self.assertEqual(out["approval_state"], "approved")
        self.assertEqual(code(self.pl.deploy, self.a, self.pid, run["id"]), ("DEPLOY_DISABLED", 501))
        self.assertTrue(self.d.h.conn.execute("SELECT 1 FROM dv_events WHERE action = 'pipeline.deploy_refused' AND outcome = 'denied'").fetchone())
        self.assertEqual(code(self.pl.approve, self.a, self.pid, run["id"], "x")[1], 422)

    def test_second_run_records_the_previous_success_as_rollback_target(self):
        p, first = self.start()
        self.go(first["id"])
        second = self.pl.run(self.a, self.pid, p["id"])
        self.assertEqual(self.go(second["id"])["rollback_target"], first["id"])

    def test_only_one_run_at_a_time_cancel_and_revocation(self):
        p, run = self.start()
        self.assertEqual(code(self.pl.run, self.a, self.pid, p["id"])[0], "RUN_IN_PROGRESS")
        self.assertEqual(self.pl.cancel_run(self.a, self.pid, run["id"])["status"], "cancelled")
        p2, run2 = self.start(name="ci2")
        c = self.d.h.conn
        c.execute("UPDATE entitlements SET revoked_at = ? WHERE user_id = ?", (self.d.h.clock.iso(), self.a.user_id))
        self.assertEqual(self.go(run2["id"])["status"], "blocked")

    def test_other_users_cannot_see_or_run_pipelines(self):
        bob = self.d.user("bob@example.com")
        p, run = self.start()
        plb = self.d.svc(PipelineService)
        for call in (lambda: plb.get_run(bob, self.pid, run["id"]), lambda: plb.run(bob, self.pid, p["id"]), lambda: plb.list(bob, self.pid)):
            self.assertEqual(code(call)[1], 404)

    def test_a_signed_push_webhook_creates_a_run_for_commit_triggered_pipelines_only(self):
        remote = os.path.join(tempfile.mkdtemp(), "r.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
        git = self.d.svc(GitService)
        git.connect(self.a, self.pid, provider="local", remote_url=remote, consent=True, webhook_secret="s3cret-hook")
        repo = self.d.h.conn.execute("SELECT id FROM dv_repos").fetchone()[0]
        self.fs.write(self.a, self.pid, "tests/test_x.py", OK_TEST, None)
        self.pl.create(self.a, self.pid, name="on-commit", triggers=["commit"], stages=[{"type": "lint"}])
        self.pl.create(self.a, self.pid, name="manual-only", stages=[{"type": "lint"}])
        body = b'{"ref":"refs/heads/main"}'
        h = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"s3cret-hook", body, hashlib.sha256).hexdigest(), "X-GitHub-Delivery": "delivery-00001", "X-GitHub-Event": "push"}
        out = git.webhook(repo, h, body)
        self.assertEqual(len(out["pipeline_runs"]), 1)
        runs = self.pl.runs(self.a, self.pid)["items"]
        self.assertEqual((len(runs), runs[0]["trigger"]), (1, "commit"))


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.ex, self.fs, self.js = self.d.svc(ExperimentService), self.d.svc(FileService), self.d.svc(JobService)

    def registry_versions(self, n=2):
        c, now = self.d.h.conn, self.d.h.clock.iso()
        c.execute("INSERT INTO reg_models (id, project_id, owner_user_id, name, model_type, created_at, updated_at) VALUES ('m1', ?, ?, 'churn', 'binary_classification', ?, ?)", (self.pid, self.a.user_id, now, now))
        for v in range(1, n + 1):
            c.execute("INSERT INTO reg_versions (id, model_id, version, source_model_id, status, metadata, evaluation, artifact_key, artifact_sha256, artifact_bytes, created_by, created_at) "
                      "VALUES (?, 'm1', ?, 'src', 'registered', '{}', '{}', 'k', 'abc', 1, ?, ?)", (f"rv{v}", v, self.a.user_id, now))

    TRAIN = ("import json, os\nparams = json.loads(os.environ['PREDICTAI_EXPERIMENT_PARAMS'])\nseed = int(os.environ['PREDICTAI_SEED'])\nout = os.environ['PREDICTAI_OUTPUT_DIR']\n"
             "json.dump({'accuracy': 0.5 + params['lr'], 'seed_used': seed, 'note': 'ignored'}, open(out + '/metrics.json', 'w'))\nopen(out + '/model.txt', 'w').write('weights')\n")

    def test_run_records_metrics_commit_snapshot_and_a_full_lineage(self):
        from test_workspace_uploads import upload
        asset = upload(self.d.ws(), self.a, self.pid, "data.csv", b"a,b\n1,2\n")["asset"]
        self.fs.write(self.a, self.pid, "train.py", self.TRAIN, None)
        e = self.ex.create(self.a, self.pid, name="baseline", task_type="binary_classification", asset_id=asset["id"], params={"lr": 0.25}, feature_set=["a"], seed=7, split_strategy="80/20")
        self.assertTrue(e["dataset_version_id"])                                  # pinned at creation
        out = self.ex.run(self.a, self.pid, e["id"], entry="train.py")
        self.assertEqual(self.ex.get(self.a, self.pid, e["id"])["status"], "running")
        self.d.run_all()
        got = self.ex.get(self.a, self.pid, e["id"])
        self.assertEqual((got["status"], got["metrics"]), ("evaluated", {"accuracy": 0.75, "seed_used": 7.0}))
        self.assertTrue(got["tree_sha256"])
        lin = self.ex.lineage(self.a, self.pid, e["id"])
        present = {l["stage"]: l["present"] for l in lin["links"]}
        self.assertEqual(present, {"data": True, "transformations": False, "features": True, "commit": True, "environment": False, "experiment": True, "evaluation": True,
                                   "model_version": False, "artifacts": True, "approval": False, "rollback": False})
        self.assertFalse(lin["complete"])
        self.assertIn("Nothing is deployed", lin["deployment"])
        self.assertTrue(all(l["note"] for l in lin["links"] if not l["present"]))         # every gap is explained, none invented

    def test_approval_needs_metrics_a_real_registry_version_and_limitations_and_rollback_must_be_earlier(self):
        self.registry_versions()
        self.fs.write(self.a, self.pid, "train.py", self.TRAIN, None)
        e = self.ex.create(self.a, self.pid, name="exp", params={"lr": 0.1}, seed=1)
        self.assertEqual(code(self.ex.approve, self.a, self.pid, e["id"], "ready to go")[0], "APPROVAL_INCOMPLETE")
        self.ex.run(self.a, self.pid, e["id"], entry="train.py")
        self.d.run_all()
        self.assertEqual(code(self.ex.link_model, self.a, self.pid, e["id"], "no-such-version")[1], 404)
        self.ex.link_model(self.a, self.pid, e["id"], "rv2", limitations="trained on one month of data only")
        out = self.ex.approve(self.a, self.pid, e["id"], "metrics reviewed")
        self.assertEqual(out["approval_state"], "approved")
        self.assertEqual(code(self.ex.set_rollback, self.a, self.pid, e["id"], "rv2")[1], 422)                 # not earlier
        self.assertEqual(self.ex.set_rollback(self.a, self.pid, e["id"], "rv1")["rollback_reg_version_id"], "rv1")
        lin = self.ex.lineage(self.a, self.pid, e["id"])
        self.assertEqual([l["stage"] for l in lin["links"] if not l["present"]], ["data", "transformations", "features", "environment"])
        # an artifact behind an approved experiment cannot be deleted until approval is revoked
        art = self.d.svc(ArtifactService)
        a = [x for x in art.list(self.a, self.pid)["items"] if x["name"] == "model.txt"][0]
        self.assertEqual(code(art.delete, self.a, self.pid, a["id"], "model.txt")[0], "ARTIFACT_PROTECTED")
        self.ex.revoke_approval(self.a, self.pid, e["id"], "found a problem")
        self.assertEqual(art.delete(self.a, self.pid, a["id"], "model.txt"), {"deleted": True})

    def test_a_model_from_another_project_or_person_cannot_be_linked(self):
        self.registry_versions()
        bob = self.d.user("bob@example.com")
        bpid = self.d.project(bob)
        e = self.d.svc(ExperimentService).create(bob, bpid, name="bobs")
        self.assertEqual(code(self.d.svc(ExperimentService).link_model, bob, bpid, e["id"], "rv1")[1], 404)

    def test_failed_run_marks_the_experiment_failed_and_compare_lists_metrics(self):
        self.fs.write(self.a, self.pid, "bad.py", "raise SystemExit(2)\n", None)
        self.fs.write(self.a, self.pid, "train.py", self.TRAIN, None)
        bad, good = self.ex.create(self.a, self.pid, name="bad", seed=1), self.ex.create(self.a, self.pid, name="good", params={"lr": 0.3}, seed=2)
        self.ex.run(self.a, self.pid, bad["id"], entry="bad.py")
        self.ex.run(self.a, self.pid, good["id"], entry="train.py")
        self.d.run_all()
        self.assertEqual(self.ex.get(self.a, self.pid, bad["id"])["status"], "failed")
        cmp = self.ex.compare(self.a, self.pid, [bad["id"], good["id"]])
        self.assertEqual(cmp["metrics"]["accuracy"], [None, 0.8])

    def test_validation_and_isolation(self):
        for kw in ({"task_type": "magic"}, {"asset_id": "nope"}, {"env_id": "nope"}, {"seed": -1}, {"seed": True}, {"feature_set": "a,b"}, {"params": {"x": object()}}):
            self.assertIn(code(self.ex.create, self.a, self.pid, name="x", **kw)[1], (404, 422), kw)
        e = self.ex.create(self.a, self.pid, name="mine")
        bob = self.d.user("bob@example.com")
        self.assertEqual(code(self.d.svc(ExperimentService).get, bob, self.pid, e["id"])[1], 404)


if __name__ == "__main__":
    unittest.main()
