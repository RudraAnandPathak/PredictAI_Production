"""Model Registry: registration, versioning, artifact integrity, lifecycle, rollback, comparison, predictions, isolation."""
import hashlib

from app.core.errors import AppError
from p7_support import FAST, P7Base, code_of

VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}


class Registry(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.m1 = cls.train()
        cls.m2 = cls.train(algorithms=["random_forest"])

    def reg(self, name="Churn model"):
        return self.p7.registry.register(self.alice, self.m1, name=name)

    def test_registration_keeps_everything_the_spec_lists(self):
        r = self.reg("Registered A")
        v = self.p7.registry.get_version(self.alice, r["versions"][0]["id"])
        meta = v["metadata"]
        for k in ("features", "target", "preprocessing", "algorithm", "hyperparameters", "training_configuration", "primary_metric", "experiment", "lineage"):
            self.assertIn(k, meta)
        self.assertEqual(meta["target"], "bought")
        self.assertEqual(meta["lineage"]["dataset_version_id"], self.ws.predictive.get_model(self.alice, self.m1)["version_id"])
        self.assertEqual(v["status"], "registered")
        self.assertEqual(len(v["artifact"]["sha256"]), 64)
        self.assertTrue(v["artifact"]["last_verified_ok"])
        self.assertIn("independent hold-out", v["evaluation"]["test"]["kind"])
        self.assertIn("cross-validation", v["evaluation"]["validation"]["kind"])
        self.assertFalse(v["evaluation"]["leakage_checks"]["target_in_features"])
        self.assertIn("does not deploy", r["deployment_note"])

    def test_the_artifact_is_a_separate_verified_copy(self):
        r = self.reg("Copy test")
        v = self.p7.registry.repo.version(r["versions"][0]["id"])
        src = self.ws.predictive.repo.model(self.alice.user_id, self.m1)
        self.assertNotEqual(v["artifact_key"], src["model_key"])
        self.assertEqual(v["artifact_sha256"], src["model_sha256"])
        self.assertTrue(self.p7.registry.verify(self.alice, v["id"])["ok"])
        path = self.ws.storage.local_path(v["artifact_key"])
        with open(path, "ab") as f:
            f.write(b"tamper")
        res = self.p7.registry.verify(self.alice, v["id"])
        self.assertFalse(res["ok"])
        self.assertIn("checksum", res["reason"])
        self.assertEqual(code_of(self.p7.registry.predict, self.alice, v["id"], VALUES)[0], "ARTIFACT_INTEGRITY")

    def test_versions_are_never_overwritten_and_history_is_kept(self):
        r = self.reg("Versions")
        r2 = self.p7.registry.register(self.alice, self.m2, model_id=r["id"], note="forest")
        self.assertEqual([v["version"] for v in r2["versions"]], [1, 2])
        self.assertEqual(r2["versions"][0]["id"], r["versions"][0]["id"])
        self.assertEqual(code_of(self.p7.registry.register, self.alice, self.m2, model_id=r["id"])[0], "ALREADY_REGISTERED")
        self.assertEqual(code_of(self.p7.registry.register, self.alice, self.m1, name="Versions")[0], "NAME_TAKEN")
        ev = [e["event"] for e in self.p7.registry.history(self.alice, r["id"])["items"]]
        self.assertEqual(ev.count("registered"), 2)

    def test_deleting_the_phase5_model_does_not_damage_a_registered_version(self):
        mid = self.train(algorithms=["logistic_regression"])
        r = self.p7.registry.register(self.alice, mid, name="Survivor")
        self.ws.predictive.delete_model(self.alice, mid)
        v = self.p7.registry.get_version(self.alice, r["versions"][0]["id"])
        self.assertTrue(self.p7.registry.verify(self.alice, v["id"])["ok"])
        self.assertEqual(code_of(self.p7.registry.predict, self.alice, v["id"], VALUES)[0], "SOURCE_MODEL_REMOVED")

    def test_lifecycle_allows_only_valid_transitions_and_protects_activation(self):
        r = self.reg("Lifecycle")
        vid = r["versions"][0]["id"]
        t = self.p7.registry.transition
        self.assertEqual(code_of(t, self.alice, vid, "active")[0], "INVALID_TRANSITION")
        self.assertEqual(t(self.alice, vid, "evaluated")["status"], "evaluated")
        self.assertEqual(t(self.alice, vid, "staging")["status"], "staging")
        self.assertEqual(code_of(t, self.alice, vid, "active"), ("CONFIRMATION_REQUIRED", 409))
        self.assertEqual(t(self.alice, vid, "active", confirm=True)["status"], "active")
        self.assertEqual(self.p7.registry.get(self.alice, r["id"])["active_version_id"], vid)
        self.assertEqual(code_of(t, self.alice, vid, "bogus")[1], 422)
        self.assertEqual(code_of(t, self.alice, vid, "archived")[0], "CONFIRMATION_REQUIRED")

    def test_activating_another_version_archives_the_old_one_and_rollback_restores_it(self):
        r = self.reg("Rollback")
        v1 = r["versions"][0]["id"]
        r = self.p7.registry.register(self.alice, self.m2, model_id=r["id"])
        v2 = r["versions"][1]["id"]
        t = self.p7.registry.transition
        for vid in (v1, v2):
            t(self.alice, vid, "evaluated")
            t(self.alice, vid, "staging")
        t(self.alice, v1, "active", confirm=True)
        t(self.alice, v2, "active", confirm=True)
        got = {v["id"]: v["status"] for v in self.p7.registry.get(self.alice, r["id"])["versions"]}
        self.assertEqual((got[v1], got[v2]), ("archived", "active"))
        self.assertEqual(code_of(self.p7.registry.rollback, self.alice, r["id"], v1)[0], "CONFIRMATION_REQUIRED")
        back = self.p7.registry.rollback(self.alice, r["id"], v1, confirm=True, reason="v2 regressed")
        self.assertEqual(back["active_version_id"], v1)
        self.assertEqual(len(back["versions"]), 2)                                        # nothing was deleted
        self.assertIn("rollback", [e["event"] for e in self.p7.registry.history(self.alice, r["id"])["items"]])

    def test_comparison_shows_only_relevant_metrics_and_separates_validation_from_holdout(self):
        r = self.reg("Compare")
        r = self.p7.registry.register(self.alice, self.m2, model_id=r["id"])
        c = self.p7.registry.compare(self.alice, [v["id"] for v in r["versions"]])
        self.assertEqual(c["family"], "classification")
        self.assertEqual(set(c["metrics_shown"]), {"accuracy", "precision", "recall", "f1", "roc_auc"})
        for row in c["rows"]:
            self.assertNotIn("rmse", row["holdout"])
            self.assertIn("cross_validation_score", row["validation"])
            self.assertIsNotNone(row["holdout"]["roc_auc"])
        self.assertTrue(any("independent" in x or "kept aside" in x for x in c["reading"]))
        self.assertEqual(code_of(self.p7.registry.compare, self.alice, [r["versions"][0]["id"]])[1], 422)

    def test_regression_and_classification_cannot_be_compared(self):
        import analytics_support as sup, ml_support as S
        from test_workspace_uploads import upload
        a = upload(self.ws, self.alice, self.project, "sales.csv", sup.csv_of(S.regression_frame(300)))["asset"]
        from p7_support import wait_job
        job = self.ws.predictive.start_training(self.alice, a["id"], {"target": "price", "algorithms": ["linear_regression"], "time_budget_s": 60})
        rid = wait_job(self.ws.predictive, self.alice, job["id"])["model_id"]
        reg_r = self.p7.registry.register(self.alice, rid, name="Regressor")
        cls_r = self.reg("Classifier for compare")
        self.assertEqual(code_of(self.p7.registry.compare, self.alice, [reg_r["versions"][0]["id"], cls_r["versions"][0]["id"]])[0], "INCOMPARABLE")
        c = self.p7.registry.get_version(self.alice, reg_r["versions"][0]["id"])
        self.assertIn("rmse", c["evaluation"]["test"]["metrics"])

    def test_predictions_keep_model_version_lineage(self):
        r = self.reg("Predicting")
        vid = r["versions"][0]["id"]
        out = self.p7.registry.predict(self.alice, vid, VALUES)
        self.assertEqual(out["registry"]["version_id"], vid)
        self.assertEqual(out["registry"]["artifact_sha256"], r["versions"][0]["artifact"]["sha256"])
        hist = self.p7.registry.predictions(self.alice, vid)
        self.assertGreaterEqual(len(hist["items"]), 1)
        self.p7.registry.transition(self.alice, vid, "evaluated")
        self.p7.registry.transition(self.alice, vid, "archived")
        self.assertEqual(code_of(self.p7.registry.predict, self.alice, vid, VALUES)[0], "VERSION_NOT_USABLE")

    def test_other_users_cannot_see_or_use_a_registered_model(self):
        r = self.reg("Private")
        vid = r["versions"][0]["id"]
        for call in (lambda: self.p7.registry.get(self.bob, r["id"]), lambda: self.p7.registry.get_version(self.bob, vid), lambda: self.p7.registry.verify(self.bob, vid),
                     lambda: self.p7.registry.predict(self.bob, vid, VALUES), lambda: self.p7.registry.transition(self.bob, vid, "evaluated"),
                     lambda: self.p7.registry.register(self.bob, self.m1, name="steal"), lambda: self.p7.registry.history(self.bob, r["id"])):
            self.assertEqual(code_of(call)[1], 404)
        self.assertNotIn(r["id"], [m["id"] for m in self.p7.registry.list(self.bob)["items"]])
