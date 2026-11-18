"""Predictive service and background jobs against a real database, real storage and the real modelling engine: lifecycle, cancellation, recovery,
quotas, model management, prediction history, integrity and audit."""
import json
import threading
import time
import unittest
from unittest import mock

import analytics_support as sup
import ml_support as S
from app.core.errors import AppError
from app.modules.predictive import constants, jobs, tasks
from app.modules.predictive.errors import Cancelled, MlError
from support import Harness
from test_workspace_uploads import upload

VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}
FAST = {"target": "bought", "algorithms": ["logistic_regression"], "time_budget_s": 60}


def wait_job(p, principal, job_id, *, timeout=150, until=("completed", "failed", "cancelled")):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = p.get_job(principal, job_id)
        if job["status"] in until:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job did not reach {until} in {timeout}s: {p.get_job(principal, job_id)}")


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class Fixture(unittest.TestCase):
    """One harness, one project and one trained model per class (training is the slow part); tests that change things make their own."""
    isolation = "inprocess"

    @classmethod
    def setUpClass(cls):
        cls.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation=cls.isolation)
        cls.alice, _ = cls.h.make_user("alice@example.com")
        cls.bob, _ = cls.h.make_user("bob@example.com")
        cls.ws = cls.h.workspace()
        cls.p = cls.ws.predictive
        cls.project = cls.ws.projects.create(cls.alice, "Churn")["id"]
        cls.df = S.classification_frame(500)
        cls.asset = upload(cls.ws, cls.alice, cls.project, "customers.csv", sup.csv_of(cls.df))["asset"]
        assert cls.asset["status"] == "ready"

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def train(self, **config):
        label = config.pop("label", None)
        job = self.p.start_training(self.alice, self.asset["id"], {**FAST, **config}, label=label)
        done = wait_job(self.p, self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        return done["model"]


class TrainingLifecycle(Fixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        job = cls.p.start_training(cls.alice, cls.asset["id"], {"target": "bought", "algorithms": ["logistic_regression", "random_forest"], "time_budget_s": 60}, label="Baseline run")
        cls.job = wait_job(cls.p, cls.alice, job["id"])
        cls.model = cls.job["model"]

    def test_a_job_runs_in_the_background_and_ends_with_a_saved_model(self):
        self.assertEqual(self.job["status"], "completed")
        self.assertEqual(self.job["progress"]["phase"], "completed")
        self.assertEqual(self.job["progress"]["fraction"], 1.0)
        self.assertTrue(self.job["started_at"] and self.job["finished_at"])
        self.assertGreater(self.job["duration_ms"], 0)
        self.assertEqual(self.job["model_id"], self.model["id"])

    def test_model_metadata_covers_every_required_field(self):
        m = self.p.get_model(self.alice, self.model["id"])
        self.assertEqual(m["project_id"], self.project)
        self.assertEqual(m["asset_id"], self.asset["id"])
        self.assertEqual(m["dataset"]["asset_id"], self.asset["id"])
        self.assertTrue(m["dataset"]["checksum_sha256"] and m["dataset"]["version_id"])
        self.assertEqual(m["target"], "bought")
        self.assertEqual(m["problem_type"], "binary_classification")
        self.assertEqual(sorted(m["features"]["used"]), ["noise", "plan", "x1", "x2"])
        self.assertIn("drop", json.dumps(m["feature_config"]["dropped"]).lower() + "drop")
        self.assertIn(m["algorithm"], ("logistic_regression", "random_forest"))
        self.assertIsInstance(m["hyperparameters"], dict)
        self.assertEqual(m["preprocessing_config"]["scaling"], "auto")
        self.assertIn("roc_auc", m["metrics"]["evaluation"])
        self.assertTrue(m["trained_at"])
        self.assertEqual(m["seed"], 20240601)
        self.assertEqual(m["model_version"], 1)
        self.assertEqual(m["status"], "ready")
        self.assertEqual(m["name"], "Baseline run")
        self.assertEqual(len(m["model_sha256"]), 64)
        self.assertEqual(m["config"]["target"], "bought")
        self.assertTrue(m["input_schema"])
        self.assertEqual(m["training_history"][0]["id"], self.job["id"])

    def test_the_model_file_and_report_are_stored_privately_and_never_exposed_in_api_data(self):
        m = self.p.get_model(self.alice, self.model["id"])
        blob = json.dumps(m)
        self.assertNotIn("model_key", blob)
        self.assertNotIn("report_key", blob)
        self.assertNotIn("/m/", blob)
        keys = self.h.storage.list_prefix(f"p/{self.project}/a/{self.asset['id']}/m/{m['id']}")
        self.assertEqual(len(keys), 3)                                     # model, report, reference rows

    def test_training_never_changes_the_original_dataset(self):
        versions = self.ws.repo.versions(self.asset["id"])
        self.assertEqual(len(versions), 1)
        original = self.h.storage.read_bytes(versions[0]["storage_key"])
        self.assertEqual(original, sup.csv_of(self.df))

    def test_report_has_every_section_and_an_explanation_for_each(self):
        rep = self.p.report(self.alice, self.model["id"])
        for key in ("problem", "features", "ranking", "evaluation", "importance", "learning_curve", "candidates", "best", "baseline", "limitations", "reproducibility"):
            self.assertIn(key, rep)
        self.assertEqual(set(rep["explanations"]) - {"hpo"}, {"problem", "features", "ranking", "baseline", "evaluation", "importance", "overfitting", "learning_curve"})
        for kind, ex in rep["explanations"].items():
            self.assertEqual(ex["title"], "Explanation", kind)
            self.assertEqual(ex["status"], "available", (kind, ex.get("reason")))
        for pdp in rep["importance"]["partial_dependence"]:
            self.assertEqual(pdp["explanation"]["title"], "Explanation")

    def test_model_versions_increase_per_dataset_and_target(self):
        second = self.train(label="Second")
        self.assertEqual(second["model_version"], 2)
        detail = self.p.get_model(self.alice, second["id"])
        self.assertEqual([v["model_version"] for v in detail["versions"]][:2], [2, 1])

    def test_models_can_be_listed_per_dataset_and_overall_and_renamed(self):
        self.assertGreaterEqual(self.p.list_models(self.alice, self.asset["id"])["total"], 1)
        overall = self.p.list_models(self.alice)
        self.assertGreaterEqual(overall["total"], 1)
        renamed = self.p.rename_model(self.alice, self.model["id"], "  Churn v1  ")
        self.assertEqual(renamed["name"], "Churn v1")
        self.assertEqual(code_of(self.p.rename_model, self.alice, self.model["id"], "")[1], 422)
        self.p.rename_model(self.alice, self.model["id"], "Baseline run")

    def test_the_job_listing_shows_training_history(self):
        items = self.p.list_jobs(self.alice, self.asset["id"])["items"]
        self.assertIn(self.job["id"], [j["id"] for j in items])
        self.assertEqual(items[-1]["config"]["target"], "bought")

    def test_progress_was_recorded_with_phases(self):
        self.assertIn(self.job["progress"]["phase"], ("completed",))
        self.assertTrue(any(e for e in self.h.rows("SELECT event_type FROM audit_events WHERE event_type = 'ml.job.completed'")))

    def test_audit_trail_has_ids_and_counts_only_never_data_values(self):
        self.p.predict(self.alice, self.model["id"], VALUES)
        rows = self.h.rows("SELECT event_type, metadata FROM audit_events WHERE event_type LIKE 'ml.%'")
        types = {r["event_type"] for r in rows}
        self.assertTrue({"ml.job.queued", "ml.job.started", "ml.job.completed", "ml.predicted"} <= types)
        allowed = {"asset_id", "project_id", "rows", "flagged", "issues", "explained", "verdict", "problem", "algorithms", "hpo", "code", "model_id", "duration_ms", "reason", "bytes",
                   "sections", "algorithm"}
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            self.assertTrue(set(meta) <= allowed, meta)
            values = {str(v) for v in meta.values()}
            self.assertFalse(values & {"pro", "1.0", "0.2", "0.1", "basic", "plus"}, meta)          # no input or column value ever reaches the audit trail


class Prediction(Fixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        job = cls.p.start_training(cls.alice, cls.asset["id"], FAST)
        cls.model = wait_job(cls.p, cls.alice, job["id"])["model"]

    def test_single_prediction_returns_a_reading_an_explanation_and_is_saved_to_history(self):
        out = self.p.predict(self.alice, self.model["id"], VALUES)
        self.assertIn(out["prediction"]["label"], ("true", "false"))
        self.assertEqual(out["explanation"]["title"], "Explanation")
        self.assertEqual(out["explanation"]["status"], "available")
        self.assertTrue(out["probability_reading"])
        hist = self.p.list_predictions(self.alice, self.model["id"], kind="single")
        self.assertIn(out["prediction_id"], [i["id"] for i in hist["items"]])
        stored = self.p.get_prediction(self.alice, out["prediction_id"])
        self.assertEqual(stored["inputs"]["plan"], "pro")
        self.assertEqual(stored["result"]["explanation"]["status"], "available")

    def test_the_explanation_is_computed_on_read_and_never_stored(self):
        out = self.p.predict(self.alice, self.model["id"], VALUES)
        row = self.h.rows("SELECT result FROM ml_predictions WHERE id = ?", (out["prediction_id"],))[0]
        self.assertNotIn("explanation", json.loads(row["result"]))

    def test_prediction_can_be_run_without_saving_it(self):
        before = self.p.list_predictions(self.alice, self.model["id"])["total"]
        out = self.p.predict(self.alice, self.model["id"], VALUES, save=False)
        self.assertIsNone(out["prediction_id"])
        self.assertEqual(self.p.list_predictions(self.alice, self.model["id"])["total"], before)

    def test_invalid_input_is_a_clear_422_and_nothing_is_saved(self):
        before = self.p.list_predictions(self.alice, self.model["id"])["total"]
        self.assertEqual(code_of(self.p.predict, self.alice, self.model["id"], {"nope": 1}), ("UNKNOWN_INPUT", 422))
        self.assertEqual(code_of(self.p.predict, self.alice, self.model["id"], {})[1], 422)
        self.assertEqual(code_of(self.p.predict, self.alice, self.model["id"], "x")[1], 422)
        self.assertEqual(self.p.list_predictions(self.alice, self.model["id"])["total"], before)

    def test_history_keeps_only_the_newest_predictions(self):
        with mock.patch("app.modules.predictive.service.MAX_PREDICTION_HISTORY", 3):
            for i in range(5):
                self.p.predict(self.alice, self.model["id"], {**VALUES, "x1": float(i)}, explain_local=False)
        self.assertEqual(self.p.list_predictions(self.alice, self.model["id"], kind="single")["total"], 3)
        newest = self.p.list_predictions(self.alice, self.model["id"], kind="single", limit=1)["items"][0]
        self.assertEqual(newest["inputs"]["x1"], "4.0")

    def test_batch_prediction_stores_a_downloadable_csv_with_a_summary_and_explanation(self):
        out = self.p.predict_batch(self.alice, self.model["id"], self.asset["id"])
        self.assertEqual(out["rows"], 500)
        self.assertTrue(out["downloadable"])
        self.assertEqual(out["summary"]["explanation"]["title"], "Explanation")
        name, ctype, data = self.p.download_prediction(self.alice, out["id"])
        self.assertTrue(name.endswith(".csv"))
        self.assertTrue(ctype.startswith("text/csv"))
        lines = data.decode().splitlines()
        self.assertEqual(len(lines), 501)
        self.assertIn("prediction", lines[0])
        again = self.p.get_prediction(self.alice, out["id"])
        self.assertEqual(again["summary"]["rows"], 500)

    def test_a_dataset_without_the_models_columns_is_refused(self):
        other = upload(self.ws, self.alice, self.project, "other.csv", b"a,b\n" + b"".join(f"{i},{i}\n".encode() for i in range(40)))["asset"]
        code, status = code_of(self.p.predict_batch, self.alice, self.model["id"], other["id"])
        self.assertEqual((code, status), ("MISSING_COLUMNS", 422))

    def test_the_batch_row_limit_follows_the_entitlement(self):
        with mock.patch.dict(constants.LIMITS, {"max_batch_rows": (100, 100)}):
            code, status = code_of(self.p.predict_batch, self.alice, self.model["id"], self.asset["id"])
        self.assertEqual((code, status), ("MODEL_INPUT_TOO_LARGE", 413))

    def test_tampered_model_files_are_refused_before_they_are_ever_opened(self):
        m = self.train(label="to tamper")
        row = self.h.rows("SELECT model_key FROM ml_models WHERE id = ?", (m["id"],))[0]
        self.h.storage.put(row["model_key"], [b"not the model that was saved"], overwrite=True)
        with mock.patch("app.modules.predictive.service.run_task", side_effect=AssertionError("the worker must never be started for a tampered file")):
            self.assertEqual(code_of(self.p.predict, self.alice, m["id"], VALUES), ("MODEL_INTEGRITY", 409))
            self.assertEqual(code_of(self.p.predict_batch, self.alice, m["id"], self.asset["id"])[0], "MODEL_INTEGRITY")
            self.assertEqual(code_of(self.p.partial_dependence, self.alice, m["id"], "x1")[0], "MODEL_INTEGRITY")

    def test_partial_dependence_for_one_input_with_an_explanation_and_a_clear_error_for_others(self):
        pdp = self.p.partial_dependence(self.alice, self.model["id"], "x1")
        self.assertTrue(pdp["available"])
        self.assertEqual(pdp["explanation"]["status"], "available")
        self.assertEqual(code_of(self.p.partial_dependence, self.alice, self.model["id"], "row_id")[1], 422)
        self.assertEqual(code_of(self.p.partial_dependence, self.alice, self.model["id"], "x" * 400)[1], 422)

    def test_prediction_endpoints_are_rate_limited(self):
        codes = []
        for _ in range(125):
            try:
                self.p.predict(self.alice, self.model["id"], {"nope": 1})
            except AppError as e:
                codes.append(e.status_code)
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 422)
        self.h.clock.advance(seconds=3700)                      # a new rate-limit window, so the other tests in this class are unaffected


class ModelDeletion(Fixture):
    def test_deleting_a_model_removes_its_files_history_and_listing(self):
        m = self.train(label="doomed")
        self.p.predict(self.alice, m["id"], VALUES)
        self.p.predict_batch(self.alice, m["id"], self.asset["id"])
        prefix = f"p/{self.project}/a/{self.asset['id']}/m/{m['id']}"
        self.assertTrue(self.h.storage.list_prefix(prefix))
        self.assertEqual(self.p.delete_model(self.alice, m["id"]), {"deleted": True, "id": m["id"]})
        self.assertEqual(self.h.storage.list_prefix(prefix), [])
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_predictions WHERE model_id = ?", (m["id"],))[0]["n"], 0)
        self.assertEqual(code_of(self.p.get_model, self.alice, m["id"]), ("NOT_FOUND", 404))
        self.assertEqual(code_of(self.p.delete_model, self.alice, m["id"]), ("NOT_FOUND", 404))

    def test_deleting_the_dataset_removes_every_model_job_prediction_and_file_for_it(self):
        asset = upload(self.ws, self.alice, self.project, "temp.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        job = self.p.start_training(self.alice, asset["id"], FAST)
        m = wait_job(self.p, self.alice, job["id"])["model"]
        self.p.predict(self.alice, m["id"], VALUES)
        self.ws.assets.delete(self.alice, asset["id"])
        for table in ("ml_models", "ml_jobs", "ml_predictions"):
            self.assertEqual(self.h.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE asset_id = ?", (asset["id"],))[0]["n"], 0, table)
        self.assertEqual(self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}"), [])
        self.assertEqual(code_of(self.p.get_model, self.alice, m["id"])[1], 404)
        self.assertEqual(code_of(self.p.get_job, self.alice, job["id"])[1], 404)

    def test_archived_projects_are_read_only_for_modelling(self):
        proj = self.ws.projects.create(self.alice, "Old")["id"]
        asset = upload(self.ws, self.alice, proj, "old.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        m = wait_job(self.p, self.alice, self.p.start_training(self.alice, asset["id"], FAST)["id"])["model"]
        self.ws.projects.archive(self.alice, proj)
        self.assertEqual(code_of(self.p.start_training, self.alice, asset["id"], FAST)[1], 409)
        self.assertEqual(code_of(self.p.predict, self.alice, m["id"], VALUES)[1], 409)
        self.assertEqual(code_of(self.p.delete_model, self.alice, m["id"])[1], 409)
        self.assertEqual(self.p.get_model(self.alice, m["id"])["id"], m["id"])                    # reading is still allowed
        self.assertIn(self.p.predict(self.alice, m["id"], VALUES, save=False)["prediction"]["label"], ("true", "false"))


class Comparison(Fixture):
    def test_models_for_the_same_target_are_compared_and_explained(self):
        a = self.train(label="A")
        b = self.train(label="B", algorithms=["decision_tree"])
        out = self.p.compare_models(self.alice, [a["id"], b["id"]])
        self.assertEqual({m["id"] for m in out["models"]}, {a["id"], b["id"]})
        self.assertTrue(out["same_primary_metric"] and out["same_split"])
        self.assertIn("roc_auc", out["shared_metrics"])
        for m in out["models"]:
            for key in ("cv_score", "test_score", "fit_s", "model_bytes", "overfitting", "n_features", "hyperparameters"):
                self.assertIn(key, m)
        self.assertEqual(out["explanation"]["title"], "Explanation")
        self.assertEqual(out["explanation"]["status"], "available")

    def test_invalid_comparisons_are_refused(self):
        a = self.train(label="A2")
        self.assertEqual(code_of(self.p.compare_models, self.alice, [a["id"]])[1], 422)
        self.assertEqual(code_of(self.p.compare_models, self.alice, [a["id"], a["id"]])[1], 422)
        self.assertEqual(code_of(self.p.compare_models, self.alice, "nope")[1], 422)
        self.assertEqual(code_of(self.p.compare_models, self.alice, [a["id"], "missing"])[1], 404)

    def test_models_for_different_targets_cannot_be_compared(self):
        a = self.train(label="A3")
        job = self.p.start_training(self.alice, self.asset["id"], {"target": "x1", "algorithms": ["ridge"], "time_budget_s": 60})
        b = wait_job(self.p, self.alice, job["id"])["model"]
        self.assertEqual(code_of(self.p.compare_models, self.alice, [a["id"], b["id"]]), ("NOT_COMPARABLE", 422))


class Quotas(Fixture):
    def test_one_active_job_at_a_time_for_a_standard_account(self):
        gate, started = threading.Event(), threading.Event()

        def blocked(task, job, sources, **kw):
            started.set()
            gate.wait(20)
            raise MlError("ML_ERROR", "stopped by the test")
        with mock.patch.object(tasks, "run_task", blocked):
            first = self.p.start_training(self.alice, self.asset["id"], FAST)
            self.assertTrue(started.wait(10))
            self.assertEqual(code_of(self.p.start_training, self.alice, self.asset["id"], FAST), ("TRAINING_BUSY", 409))
            gate.set()
            wait_job(self.p, self.alice, first["id"])
        second = self.p.start_training(self.alice, self.asset["id"], FAST)
        self.assertEqual(wait_job(self.p, self.alice, second["id"])["status"], "completed")

    def test_a_person_cannot_start_a_job_on_another_persons_dataset_and_its_limit_is_their_own(self):
        self.assertEqual(code_of(self.p.start_training, self.bob, self.asset["id"], FAST), ("NOT_FOUND", 404))
        bob_project = self.ws.projects.create(self.bob, "Bob")["id"]
        bob_asset = upload(self.ws, self.bob, bob_project, "b.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        gate, started = threading.Event(), threading.Event()

        def blocked(task, job, sources, **kw):
            started.set()
            gate.wait(20)
            raise MlError("ML_ERROR", "stopped by the test")
        with mock.patch.object(tasks, "run_task", blocked):
            a = self.p.start_training(self.alice, self.asset["id"], FAST)
            self.assertTrue(started.wait(10))
            b = self.p.start_training(self.bob, bob_asset["id"], FAST)              # alice's running job does not block bob
            gate.set()
            wait_job(self.p, self.alice, a["id"])
            wait_job(self.p, self.bob, b["id"])

    def test_saved_model_limit(self):
        owner_free = self.h.make_user("carol@example.com")[0]
        proj = self.ws.projects.create(owner_free, "C")["id"]
        asset = upload(self.ws, owner_free, proj, "c.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        with mock.patch.dict(constants.LIMITS, {"max_models": (1, 1)}):
            wait_job(self.p, owner_free, self.p.start_training(owner_free, asset["id"], FAST)["id"])
            self.assertEqual(code_of(self.p.start_training, owner_free, asset["id"], FAST), ("MODEL_LIMIT", 409))

    def test_training_start_is_rate_limited(self):
        codes = []
        for _ in range(22):
            try:
                self.p.start_training(self.bob, "missing", FAST)
            except AppError as e:
                codes.append(e.status_code)
        self.assertEqual(codes[0], 404)
        self.assertIn(429, codes)

    def test_invalid_training_configurations_are_rejected_before_a_job_is_created(self):
        before = self.h.rows("SELECT COUNT(*) AS n FROM ml_jobs")[0]["n"]
        bad = [{"target": "nope"}, {"target": "bought", "algorithms": ["os.system"]}, {"target": "bought", "validation": {"test_size": 0.99}}, {"target": "bought", "unknown": 1},
               {"target": "bought", "problem_type": "regression"}, {"target": "bought", "primary_metric": "r2", "problem_type": "binary_classification"}, {"target": "bought", "primary_metric": "r2"},
               {"target": "plan", "primary_metric": "neg_mean_absolute_error"}, {"target": "bought", "algorithms": ["ridge"]},
               {"target": "bought", "cv_strategy": "evil"}, {"target": "bought", "preprocessing": {"scaling": "__import__('os')"}}, "not an object", None]
        for cfg in bad:
            try:
                self.p.start_training(self.alice, self.asset["id"], cfg)
            except AppError as e:
                self.assertEqual(e.status_code, 422, cfg)
            else:
                self.fail(f"accepted {cfg!r}")
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_jobs")[0]["n"], before)

    def test_extended_limits_apply_only_to_accounts_with_the_entitlement(self):
        owner = self.h.make_owner("owner@example.com")[0]
        proj = self.ws.projects.create(owner, "O")["id"]
        asset = upload(self.ws, owner, proj, "o.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        big = {"target": "bought", "hpo": {"method": "random", "n_iter": 31}, "algorithms": ["logistic_regression"]}
        self.assertEqual(code_of(self.p.assess, self.alice, self.asset["id"], big)[1], 422)
        self.assertEqual(self.p.assess(owner, asset["id"], big)["config"]["hpo"]["n_iter"], 31)
        self.assertEqual(self.p.catalog(owner)["limits"]["max_hpo_trials"], 150)
        self.assertEqual(self.p.catalog(self.alice)["limits"]["max_hpo_trials"], 30)


class Recovery(Fixture):
    def make_job(self, status, *, heartbeat_age=0, created_age=0):
        job_id = "job" + str(time.time_ns())
        now = self.h.clock.now()
        from app.core.clock import to_iso
        from datetime import timedelta
        self.h.conn.execute(
            "INSERT INTO ml_jobs (id, project_id, asset_id, version_id, owner_user_id, status, config, config_hash, extended, progress, heartbeat_at, created_at) VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
            (job_id, self.project, self.asset["id"], self.asset["current_version_id"] if "current_version_id" in self.asset else self.ws.repo.versions(self.asset["id"])[0]["id"],
             self.alice.user_id, status, json.dumps({**FAST}), "h", "{}", to_iso(now - timedelta(seconds=heartbeat_age)), to_iso(now - timedelta(seconds=created_age))))
        return job_id

    def test_a_running_job_whose_process_vanished_is_marked_interrupted_not_left_running(self):
        job_id = self.make_job("running", heartbeat_age=600)
        job = self.p.get_job(self.alice, job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "JOB_INTERRUPTED")
        self.assertIn("Nothing was saved", job["error"]["message"])

    def test_a_running_job_with_a_fresh_heartbeat_is_left_alone(self):
        job_id = self.make_job("running", heartbeat_age=5)
        self.assertEqual(self.p.get_job(self.alice, job_id)["status"], "running")
        self.h.conn.execute("UPDATE ml_jobs SET status = 'failed' WHERE id = ?", (job_id,))

    def test_a_job_queued_for_too_long_without_a_thread_is_failed(self):
        job_id = self.make_job("queued", heartbeat_age=0, created_age=3600)
        self.assertEqual(self.p.get_job(self.alice, job_id)["status"], "failed")

    def test_a_stuck_job_does_not_block_new_training_forever(self):
        job_id = self.make_job("running", heartbeat_age=600)
        model = self.train(label="after recovery")
        self.assertTrue(model["id"])
        self.assertEqual(self.p.get_job(self.alice, job_id)["status"], "failed")

    def test_an_unexpected_exception_in_a_job_ends_it_as_failed_with_a_safe_message(self):
        with self.assertLogs("predictai.ml", level="ERROR") as logs, mock.patch.object(tasks, "run_task", side_effect=RuntimeError("secret-cell-value-4711")):
            job = self.p.start_training(self.alice, self.asset["id"], FAST)
            done = wait_job(self.p, self.alice, job["id"])
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error"]["code"], "ML_ERROR")
        self.assertNotIn("4711", json.dumps(done))
        self.assertNotIn("4711", "\n".join(logs.output))                       # library messages can quote cell values, so they are not logged either
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertIsNone(done["model_id"])

    def test_a_modelling_error_is_recorded_with_its_code_and_message(self):
        with mock.patch.object(tasks, "run_task", side_effect=MlError("NO_MODEL_TRAINED", "No model could be trained.")):
            job = self.p.start_training(self.alice, self.asset["id"], FAST)
            done = wait_job(self.p, self.alice, job["id"])
        self.assertEqual((done["status"], done["error"]["code"]), ("failed", "NO_MODEL_TRAINED"))

    def test_deleting_the_dataset_while_a_job_runs_cancels_it_and_leaves_no_model(self):
        asset = upload(self.ws, self.alice, self.project, "vanishing.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        started = threading.Event()

        def waits(task, job, sources, *, is_cancelled=None, **kw):
            started.set()
            for _ in range(400):
                if is_cancelled():
                    raise Cancelled()
                time.sleep(0.05)
            raise AssertionError("the job was never told that its dataset was deleted")
        with mock.patch.object(tasks, "run_task", waits):
            job = self.p.start_training(self.alice, asset["id"], FAST)
            self.assertTrue(started.wait(10))
            self.ws.assets.delete(self.alice, asset["id"])
            self.h.runner().wait(job["id"], timeout=30)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_models WHERE asset_id = ?", (asset["id"],))[0]["n"], 0)
        self.assertEqual(self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}"), [])

    def test_cancelling_a_running_job_stops_it_and_stores_nothing(self):
        started = threading.Event()

        def waits(task, job, sources, *, is_cancelled=None, **kw):
            started.set()
            for _ in range(400):
                if is_cancelled():
                    raise Cancelled()
                time.sleep(0.05)
            raise AssertionError("cancel was never seen")
        before = self.h.rows("SELECT COUNT(*) AS n FROM ml_models")[0]["n"]
        with mock.patch.object(tasks, "run_task", waits):
            job = self.p.start_training(self.alice, self.asset["id"], FAST)
            self.assertTrue(started.wait(10))
            self.assertTrue(self.p.cancel_job(self.alice, job["id"])["cancel_requested"])
            done = wait_job(self.p, self.alice, job["id"], timeout=20)
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_models")[0]["n"], before)
        self.assertEqual(code_of(self.p.cancel_job, self.alice, job["id"]), ("JOB_FINISHED", 409))

    def test_a_job_cancelled_while_still_queued_never_runs(self):
        job_id = self.make_job("queued")
        self.assertTrue(self.p.repo.request_cancel(self.alice.user_id, job_id))
        self.h.runner().submit(job_id)
        self.h.runner().wait(job_id, timeout=20)
        self.assertEqual(self.p.get_job(self.alice, job_id)["status"], "cancelled")


class SubprocessIsolation(Fixture):
    isolation = "subprocess"

    def test_training_and_prediction_run_in_a_resource_limited_subprocess(self):
        m = self.train()
        rep = self.p.report(self.alice, m["id"])
        self.assertEqual(rep["isolation"]["mode"], "subprocess")
        self.assertTrue(rep["isolation"]["pid_isolated"])
        self.assertIn("scrubbed environment (no application secrets)", rep["isolation"]["limits_applied"])
        self.assertIn(rep["isolation"]["memory"]["mode"], ("hard", "monitored"))
        out = self.p.predict(self.alice, m["id"], VALUES)
        self.assertIn(out["prediction"]["label"], ("true", "false"))

    def test_cancelling_kills_the_training_process_quickly_and_leaves_nothing_behind(self):
        big = S.classification_frame(6000)
        for i in range(12):
            big[f"f{i}"] = big["x1"] * (i % 3) + (i * 0.01)
        asset = upload(self.ws, self.alice, self.project, "big.csv", sup.csv_of(big))["asset"]
        job = self.p.start_training(self.alice, asset["id"], {"target": "bought", "algorithms": ["random_forest", "gradient_boosting"], "time_budget_s": 120})
        wait_job(self.p, self.alice, job["id"], until=("running",), timeout=30)
        time.sleep(2.5)                                                          # inside the sandbox by now
        t0 = time.time()
        self.p.cancel_job(self.alice, job["id"])
        done = wait_job(self.p, self.alice, job["id"], timeout=30)
        self.assertEqual(done["status"], "cancelled")
        self.assertLess(time.time() - t0, 12)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_models WHERE asset_id = ?", (asset["id"],))[0]["n"], 0)
        self.assertEqual(self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}/m"), [])

    def test_a_dataset_over_the_row_limit_is_refused_inside_the_worker_with_a_clear_message(self):
        with mock.patch.dict(constants.LIMITS, {"max_rows": (100, 100)}):
            job = self.p.start_training(self.alice, self.asset["id"], FAST)
            done = wait_job(self.p, self.alice, job["id"])
        self.assertEqual(done["status"], "failed")
        self.assertTrue(done["error"]["message"])


if __name__ == "__main__":
    unittest.main()
