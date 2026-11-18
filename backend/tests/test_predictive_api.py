"""HTTP tests for the predictive-modelling API: authentication, CSRF, the standard envelope, the full dataset -> job -> model -> report -> prediction ->
download -> comparison -> deletion flow, validation, and 404-only isolation between users (FastAPI TestClient; needs fastapi and httpx)."""
import hashlib
import json
import time
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

import analytics_support as sup
import ml_support as S
from support import GOOD_PASSWORD

FAST = {"target": "bought", "algorithms": ["logistic_regression"], "time_budget_s": 60}
VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}


def browser(api, email):
    """A separate browser (cookie jar + CSRF token) signed in as `email`."""
    client = api.browser()
    api.register(email)
    response = api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
    assert response.status_code == 200, response.text
    token = response.json()["data"]["csrf_token"]

    def call(method, path, json=None, params=None, headers=None, content=None):
        merged = {"X-CSRF-Token": token, **(headers or {})}
        return api.call(method, path, json=json, params=params, csrf=False, client=client, headers=merged, content=content)

    return call


def upload(call, pid, name, data):
    started = call("POST", f"/projects/{pid}/uploads", {"filename": name, "size": len(data), "checksum_sha256": hashlib.sha256(data).hexdigest()})
    info = started.json()["data"]["upload"]
    for i in range(info["total_chunks"]):
        call("POST", f"/uploads/{info['id']}/chunks/{i}", content=data[i * info["chunk_size"]:(i + 1) * info["chunk_size"]], headers={"Content-Type": "application/octet-stream"})
    done = call("POST", f"/uploads/{info['id']}/complete")
    assert done.status_code == 200, done.text
    return done.json()["data"]["asset"]["id"]


def wait(call, job_id, *, timeout=150):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = call("GET", f"/ml/jobs/{job_id}").json()["data"]
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("training did not finish")


class PredictiveApiBase(unittest.TestCase):
    """One app, two people, one project, one dataset and one trained model for the whole class: training is the slow part."""

    @classmethod
    def setUpClass(cls):
        cls.api = base.Api(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation="inprocess")
        alice, bob = browser(cls.api, "alice@example.com"), browser(cls.api, "bob@example.com")
        cls.alice, cls.bob = staticmethod(alice), staticmethod(bob)       # staticmethod: a plain function stored on the class would be bound to the test instance
        cls.pid = alice("POST", "/projects", {"name": "Churn"}).json()["data"]["id"]
        cls.df = S.classification_frame(400)
        cls.asset = upload(alice, cls.pid, "customers.csv", sup.csv_of(cls.df))
        started = alice("POST", f"/assets/{cls.asset}/ml/jobs", {"config": FAST, "label": "first model"})
        assert started.status_code == 201, started.text
        cls.job = wait(alice, started.json()["data"]["id"])
        assert cls.job["status"] == "completed", cls.job
        cls.model = cls.job["model"]

    @classmethod
    def tearDownClass(cls):
        cls.api.close()

    def err(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()["error"]["code"]


class AccessControl(PredictiveApiBase):
    def routes(self):
        a, m = self.asset, self.model["id"]
        return [("GET", "/ml/catalog"), ("GET", f"/assets/{a}/ml/schema"), ("POST", f"/assets/{a}/ml/assess"), ("POST", f"/assets/{a}/ml/jobs"),
                ("GET", f"/assets/{a}/ml/jobs"), ("GET", "/ml/jobs/x"), ("POST", "/ml/jobs/x/cancel"), ("GET", "/ml/models"), ("POST", "/ml/models/compare"),
                ("GET", f"/ml/models/{m}"), ("PATCH", f"/ml/models/{m}"), ("DELETE", f"/ml/models/{m}"), ("GET", f"/ml/models/{m}/report"),
                ("POST", f"/ml/models/{m}/partial-dependence"), ("POST", f"/ml/models/{m}/predict"), ("POST", f"/ml/models/{m}/predict-batch"),
                ("GET", f"/ml/models/{m}/predictions"), ("GET", "/ml/predictions/x"), ("GET", "/ml/predictions/x/download")]

    def test_every_route_requires_a_session(self):
        for method, path in self.routes():
            self.assertEqual(self.api.call(method, path, csrf=False).status_code, 401, f"{method} {path}")

    def test_changes_require_a_csrf_token_and_a_trusted_origin(self):
        other = base.Api()
        self.addCleanup(other.close)
        other.signed_in()
        for method, path in self.routes():
            if method == "GET":
                continue
            self.assertEqual(self.err(other.call(method, path, {}, csrf=False), 403), "CSRF_FAILED", f"{method} {path}")
            self.assertEqual(self.err(other.call(method, path, {}, origin="https://evil.example"), 403), "CSRF_FAILED", f"{method} {path}")

    def test_the_download_route_is_read_only_so_it_needs_no_csrf_token_but_still_a_session(self):
        self.assertEqual(self.api.call("GET", "/ml/predictions/x/download", csrf=False).status_code, 401)

    def test_another_person_sees_nothing_and_every_answer_is_the_same_404(self):
        batch = self.alice("POST", f"/ml/models/{self.model['id']}/predict-batch", {"dataset_asset_id": self.asset}).json()["data"]
        single = self.alice("POST", f"/ml/models/{self.model['id']}/predict", {"values": VALUES}).json()["data"]
        m = self.model["id"]
        probes = [("GET", f"/assets/{self.asset}/ml/schema", None), ("POST", f"/assets/{self.asset}/ml/assess", {"config": FAST}), ("POST", f"/assets/{self.asset}/ml/jobs", {"config": FAST}),
                  ("GET", f"/assets/{self.asset}/ml/jobs", None), ("GET", f"/ml/jobs/{self.job['id']}", None), ("POST", f"/ml/jobs/{self.job['id']}/cancel", None),
                  ("GET", f"/ml/models?asset_id={self.asset}", None), ("GET", f"/ml/models/{m}", None), ("PATCH", f"/ml/models/{m}", {"name": "stolen"}), ("DELETE", f"/ml/models/{m}", None),
                  ("GET", f"/ml/models/{m}/report", None), ("POST", f"/ml/models/{m}/partial-dependence", {"feature": "x1"}), ("POST", f"/ml/models/{m}/predict", {"values": VALUES}),
                  ("POST", f"/ml/models/{m}/predict-batch", {"dataset_asset_id": self.asset}), ("GET", f"/ml/models/{m}/predictions", None),
                  ("GET", f"/ml/predictions/{single['prediction_id']}", None), ("GET", f"/ml/predictions/{batch['id']}", None), ("GET", f"/ml/predictions/{batch['id']}/download", None),
                  ("POST", "/ml/models/compare", {"ids": [m, "0" * 32]}), ("GET", "/ml/jobs/" + "0" * 32, None), ("GET", "/ml/models/" + "0" * 32, None)]
        seen = set()
        for method, path, body in probes:
            r = self.bob(method, path, body)
            seen.add((r.status_code, r.json()["error"]["code"]))
            for secret in ("customers.csv", "bought", "first model", "logistic"):
                self.assertNotIn(secret, r.text, f"{method} {path} leaked {secret!r}")
        self.assertEqual(seen, {(404, "NOT_FOUND")})
        self.assertEqual(self.bob("GET", "/ml/models").json()["data"]["total"], 0)
        self.assertEqual(self.alice("GET", f"/ml/models/{m}").json()["data"]["name"], self.model["name"])          # and nothing was changed

    def test_a_model_cannot_be_applied_to_another_persons_dataset(self):
        mine = upload(self.bob, self.bob("POST", "/projects", {"name": "Bob"}).json()["data"]["id"], "b.csv", sup.csv_of(S.classification_frame(60, seed=2)))
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{self.model['id']}/predict-batch", {"dataset_asset_id": mine}), 404), "NOT_FOUND")

    def test_a_dataset_from_another_project_of_the_same_person_is_refused_too(self):
        other = self.alice("POST", "/projects", {"name": "Elsewhere"}).json()["data"]["id"]
        asset = upload(self.alice, other, "x.csv", sup.csv_of(S.classification_frame(60, seed=4)))
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{self.model['id']}/predict-batch", {"dataset_asset_id": asset}), 404), "NOT_FOUND")


class Envelope(PredictiveApiBase):
    def test_success_and_error_envelopes(self):
        ok = self.alice("GET", "/ml/catalog")
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertTrue(body["meta"]["request_id"])
        self.assertNotIn("error", body)
        bad = self.alice("GET", "/ml/models/" + "f" * 32)
        self.assertEqual(set(bad.json()), {"error"})
        self.assertEqual(bad.json()["error"]["code"], "NOT_FOUND")
        self.assertTrue(bad.json()["error"]["message"])
        self.assertTrue(bad.json()["error"]["request_id"])

    def test_catalog_lists_every_required_algorithm_and_metric_and_the_limits(self):
        data = self.alice("GET", "/ml/catalog").json()["data"]
        cls = {a["key"] for a in data["algorithms"] if "classification" in a["problems"]}
        reg = {a["key"] for a in data["algorithms"] if "regression" in a["problems"]}
        self.assertTrue({"logistic_regression", "decision_tree", "random_forest", "extra_trees", "gradient_boosting", "hist_gradient_boosting", "svm", "knn", "naive_bayes"} <= cls)
        self.assertTrue({"linear_regression", "ridge", "lasso", "decision_tree", "random_forest", "extra_trees", "gradient_boosting", "hist_gradient_boosting", "svm", "knn"} <= reg)     # "svm" covers SVC and SVR
        self.assertTrue(data["limits"])
        self.assertIs(data["explainability"]["shap"], False)                  # SHAP is not installed here, so it must not be claimed
        self.assertTrue(data["explainability"]["shap_note"])

    def test_schema_marks_what_can_be_predicted(self):
        data = self.alice("GET", f"/assets/{self.asset}/ml/schema").json()["data"]
        names = [c["name"] for c in data["columns"]]
        self.assertIn("bought", names)
        self.assertIn("plan", names)

    def test_nothing_internal_ever_appears_in_a_response(self):
        self.alice("GET", f"/ml/models/{self.model['id']}")
        self.alice("GET", f"/ml/models/{self.model['id']}/report")
        everything = "\n".join(self.api.bodies)
        for forbidden in ("storage_key", "canonical_key", "report_key", "model_key", "output_key", "projects/", "Traceback", "model.joblib", "report.json", "password_hash", "secret"):
            self.assertNotIn(forbidden, everything, forbidden)


class Workflow(PredictiveApiBase):
    def test_assessment_changes_nothing_and_carries_an_explanation(self):
        before = self.alice("GET", f"/assets/{self.asset}/ml/jobs").json()["data"]
        r = self.alice("POST", f"/assets/{self.asset}/ml/assess", {"config": {"target": "bought"}})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual((data["problem"]["recommended"], data["problem"]["chosen"]), ("binary_classification", "binary_classification"))
        self.assertEqual(data["explanation"]["title"], "Explanation")
        self.assertEqual(self.alice("GET", f"/assets/{self.asset}/ml/jobs").json()["data"], before)
        self.assertEqual(self.alice("GET", f"/assets/{self.asset}").json()["data"]["status"], "ready")

    def test_training_job_describes_itself_and_the_model_is_complete(self):
        job = self.alice("GET", f"/ml/jobs/{self.job['id']}").json()["data"]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["config"]["target"], "bought")
        self.assertEqual(job["label"], "first model")
        m = job["model"]
        self.assertEqual((m["target"], m["problem_type"], m["algorithm"], m["status"]), ("bought", "binary_classification", "logistic_regression", "ready"))
        self.assertIsNotNone(m["cv_score"])
        self.assertIsNotNone(m["test_score"])
        self.assertTrue(m["features"]["used"])
        self.assertIn(job["id"], [j["id"] for j in self.alice("GET", f"/assets/{self.asset}/ml/jobs").json()["data"]["items"]])

    def test_report_has_every_section_and_an_explanation_for_each(self):
        r = self.alice("GET", f"/ml/models/{self.model['id']}/report")
        self.assertEqual(r.status_code, 200, r.text)
        rep = r.json()["data"]
        for key in ("problem", "dataset", "data", "features", "validation", "candidates", "ranking", "best", "baseline", "evaluation", "importance", "reproducibility", "limitations"):
            self.assertIn(key, rep, key)
        for kind, ex in rep["explanations"].items():
            self.assertEqual(ex["title"], "Explanation", kind)
            self.assertIn(ex["status"], ("available", "unavailable"), kind)
        self.assertEqual({"problem", "features", "ranking", "baseline", "evaluation", "importance", "overfitting"} - set(rep["explanations"]), set())
        self.assertEqual(rep["explanations"]["ranking"]["status"], "available")
        self.assertEqual(rep["explanations"]["evaluation"]["status"], "available")

    def test_single_prediction_then_history_then_detail(self):
        r = self.alice("POST", f"/ml/models/{self.model['id']}/predict", {"values": VALUES})
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()["data"]
        self.assertIn(data["prediction"]["label"], ("true", "false"))
        self.assertAlmostEqual(sum(p["probability"] for p in data["probabilities"]), 1.0, places=2)
        self.assertEqual(data["explanation"]["title"], "Explanation")
        self.assertTrue(data["local"]["effects"])
        hist = self.alice("GET", f"/ml/models/{self.model['id']}/predictions", params={"kind": "single"}).json()["data"]
        self.assertIn(data["prediction_id"], [p["id"] for p in hist["items"]])
        got = self.alice("GET", f"/ml/predictions/{data['prediction_id']}").json()["data"]
        self.assertEqual(got["inputs"]["plan"], "pro")
        self.assertEqual(got["result"]["prediction"], data["prediction"])
        self.assertEqual(got["result"]["explanation"]["title"], "Explanation")

    def test_a_prediction_can_be_made_without_saving_it(self):
        n = self.alice("GET", f"/ml/models/{self.model['id']}/predictions").json()["data"]["total"]
        r = self.alice("POST", f"/ml/models/{self.model['id']}/predict", {"values": VALUES, "save": False, "explain": False})
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.json()["data"]["prediction_id"])
        self.assertEqual(self.alice("GET", f"/ml/models/{self.model['id']}/predictions").json()["data"]["total"], n)

    def test_missing_or_odd_inputs_are_reported_never_silently_invented(self):
        r = self.alice("POST", f"/ml/models/{self.model['id']}/predict", {"values": {"x1": 1.0, "plan": "platinum"}, "save": False})
        self.assertEqual(r.status_code, 201, r.text)
        issues = r.json()["data"]["input_issues"]
        self.assertTrue(issues)
        self.assertTrue(any("platinum" in json.dumps(i) or "plan" in json.dumps(i) for i in issues))

    def test_batch_prediction_and_download(self):
        r = self.alice("POST", f"/ml/models/{self.model['id']}/predict-batch", {"dataset_asset_id": self.asset})
        self.assertEqual(r.status_code, 201, r.text)
        b = r.json()["data"]
        self.assertEqual(b["kind"], "batch")
        self.assertEqual(b["rows"], len(self.df))
        self.assertTrue(b["downloadable"])
        self.assertEqual(b["summary"]["explanation"]["title"], "Explanation")
        d = self.alice("GET", f"/ml/predictions/{b['id']}/download")
        self.assertEqual(d.status_code, 200)
        self.assertIn("attachment", d.headers["content-disposition"])
        self.assertEqual(d.headers["x-content-type-options"], "nosniff")
        self.assertIn("no-store", d.headers["cache-control"])
        lines = d.text.strip().splitlines()
        self.assertEqual(len(lines) - 1, len(self.df))
        self.assertIn("prediction", lines[0].lower())

    def test_partial_dependence_for_one_input(self):
        r = self.alice("POST", f"/ml/models/{self.model['id']}/partial-dependence", {"feature": "x1"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["explanation"]["title"], "Explanation")
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{self.model['id']}/partial-dependence", {"feature": "no_such_column"}), 422), "UNKNOWN_INPUT")
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{self.model['id']}/partial-dependence", {}), 422), "VALIDATION_ERROR")

    def test_rename_then_list_filter_then_compare_then_delete(self):
        second = self.alice("POST", f"/assets/{self.asset}/ml/jobs", {"config": {**FAST, "algorithms": ["decision_tree"]}})
        self.assertEqual(second.status_code, 201, second.text)
        done = wait(self.alice, second.json()["data"]["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        mid = done["model"]["id"]
        renamed = self.alice("PATCH", f"/ml/models/{mid}", {"name": "  A better name  "})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["data"]["name"], "A better name")
        self.assertEqual(self.err(self.alice("PATCH", f"/ml/models/{mid}", {"name": "   "}), 422), "VALIDATION_ERROR")
        listed = self.alice("GET", "/ml/models", params={"asset_id": self.asset, "target": "bought"}).json()["data"]
        self.assertIn(mid, [m["id"] for m in listed["items"]])
        cmp = self.alice("POST", "/ml/models/compare", {"ids": [self.model["id"], mid]})
        self.assertEqual(cmp.status_code, 200, cmp.text)
        c = cmp.json()["data"]
        self.assertEqual(len(c["models"]), 2)
        self.assertEqual(c["explanation"]["title"], "Explanation")
        self.assertEqual(self.err(self.alice("POST", "/ml/models/compare", {"ids": [self.model["id"]]}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(self.alice("POST", "/ml/models/compare", {"ids": [mid, mid]}), 422), "VALIDATION_ERROR")
        pred = self.alice("POST", f"/ml/models/{mid}/predict", {"values": VALUES}).json()["data"]["prediction_id"]
        gone = self.alice("DELETE", f"/ml/models/{mid}")
        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertEqual(self.err(self.alice("GET", f"/ml/models/{mid}"), 404), "NOT_FOUND")
        self.assertEqual(self.err(self.alice("GET", f"/ml/predictions/{pred}"), 404), "NOT_FOUND")
        self.assertEqual(self.err(self.alice("GET", f"/ml/models/{mid}/report"), 404), "NOT_FOUND")
        self.assertEqual(self.alice("GET", f"/ml/models/{self.model['id']}").status_code, 200)       # the other model is untouched

    def test_models_of_different_targets_cannot_be_compared(self):
        df = S.classification_frame(300, seed=9)
        df["spend"] = (df["x1"] * 3 + df["x2"]).round(3)
        asset = upload(self.alice, self.pid, "two_targets.csv", sup.csv_of(df))
        a = wait(self.alice, self.alice("POST", f"/assets/{asset}/ml/jobs", {"config": {"target": "bought", "algorithms": ["logistic_regression"], "features": {"exclude": ["spend"]}}}).json()["data"]["id"])
        b = wait(self.alice, self.alice("POST", f"/assets/{asset}/ml/jobs", {"config": {"target": "spend", "algorithms": ["ridge"], "features": {"exclude": ["bought"]}}}).json()["data"]["id"])
        self.assertEqual((a["status"], b["status"]), ("completed", "completed"))
        r = self.alice("POST", "/ml/models/compare", {"ids": [a["model"]["id"], b["model"]["id"]]})
        self.assertEqual(self.err(r, 422), "NOT_COMPARABLE")


class Validation(PredictiveApiBase):
    def test_bad_requests_get_a_clear_422(self):
        a = self.asset
        before = self.alice("GET", f"/assets/{a}/ml/jobs").json()["data"]
        for path, body in ((f"/assets/{a}/ml/assess", {"config": {}}),
                           (f"/assets/{a}/ml/assess", {"config": {"target": "no_such_column"}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "algorithms": ["not_an_algorithm"]}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "validation": {"test_size": 0.99}}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "primary_metric": "r2"}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "problem_type": "regression"}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "seed": -5}}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "exec": "import os"}}),
                           (f"/assets/{a}/ml/jobs", {"config": "target=bought"}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought"}, "label": "x" * 500}),
                           (f"/assets/{a}/ml/jobs", {"config": {"target": "bought", "algorithms": ["ridge"]}})):
            r = self.alice("POST", path, body)
            self.assertEqual(r.status_code, 422, f"{body} -> {r.status_code} {r.text}")
            error = r.json()["error"]
            self.assertTrue(error["code"] and error["message"])
            self.assertNotIn("Traceback", r.text)
        self.assertEqual(self.alice("GET", f"/assets/{a}/ml/jobs").json()["data"], before, "a rejected request must not start a job")

    def test_prediction_inputs_are_validated(self):
        m = self.model["id"]
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{m}/predict", {"values": "x1=1"}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{m}/predict", {}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(self.alice("POST", f"/ml/models/{m}/predict", {"values": {f"c{i}": 1 for i in range(401)}}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(self.alice("GET", f"/ml/models/{m}/predictions", params={"kind": "weird"}), 422), "VALIDATION_ERROR")

    def test_comparing_needs_two_to_six_models(self):
        ids = [self.model["id"]] * 7
        self.assertEqual(self.err(self.alice("POST", "/ml/models/compare", {"ids": ids}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(self.alice("POST", "/ml/models/compare", {"ids": []}), 422), "VALIDATION_ERROR")

    def test_a_rejected_request_starts_no_job(self):
        before = self.alice("GET", f"/assets/{self.asset}/ml/jobs").json()["data"]
        self.alice("POST", f"/assets/{self.asset}/ml/jobs", {"config": {"target": "no_such_column"}})
        self.assertEqual(self.alice("GET", f"/assets/{self.asset}/ml/jobs").json()["data"], before)

    def test_a_job_that_finished_cannot_be_cancelled(self):
        self.assertEqual(self.err(self.alice("POST", f"/ml/jobs/{self.job['id']}/cancel"), 409), "JOB_FINISHED")


if __name__ == "__main__":
    unittest.main()
