"""Advanced Intelligence: root cause, metric explanation, scenario analysis, causal candidates; five registers; reproducibility; correlation never worded as causation."""
import json

import analytics_support as sup
import pandas as pd
from app.modules.intelligence.methods import FORBIDDEN_CAUSAL
from p7_support import P7Base, code_of
from test_workspace_uploads import upload

BASE = {"x1": 0.5, "x2": 0.2, "plan": "basic", "noise": 0.1}


def causal_free(test, run):
    regs = run["registers"]
    for key in ("model_interpretations", "suggestions"):
        for item in regs.get(key, []):
            low = item["text"].lower().replace("does not show that one produces the other", "")
            for phrase in FORBIDDEN_CAUSAL:
                test.assertNotIn(phrase, low, f"{key}: {item['text']}")


class Intelligence(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = cls.train()
        cls.i = cls.p7.intelligence
        df = cls.df.copy()
        df["treated"] = (df["x2"] > 0).map({True: "yes", False: "no"})
        cls.asset2 = upload(cls.ws, cls.alice, cls.project, "with_treatment.csv", sup.csv_of(df))["asset"]

    def test_root_cause_ranks_associations_with_five_registers_and_no_causal_wording(self):
        r = self.i.root_cause(self.alice, self.asset["id"], outcome="bought")
        self.assertEqual(set(r["registers"]), {"retrieved_facts", "calculated_results", "model_interpretations", "suggestions", "unresolved_questions"})
        for k in ("retrieved_facts", "calculated_results", "model_interpretations", "suggestions", "unresolved_questions"):
            self.assertTrue(r["registers"][k], k)
        cols = [a["column"] for a in r["result"]["associations"]]
        self.assertIn("x1", cols[:2])
        self.assertTrue(all("p_adjusted" in a for a in r["result"]["associations"]))
        self.assertTrue(any("association" in x["text"].lower() for x in r["registers"]["model_interpretations"]))
        causal_free(self, r)
        self.assertEqual(len(r["result_sha256"]), 64)
        self.assertEqual(r["reproduction"]["seed"], 42)
        self.assertIn("pandas", r["reproduction"]["versions"])
        self.assertTrue(r["reproduction"]["created_at"])

    def test_same_inputs_give_the_same_result_fingerprint(self):
        a = self.i.root_cause(self.alice, self.asset["id"], outcome="bought")
        b = self.i.root_cause(self.alice, self.asset["id"], outcome="bought")
        self.assertEqual(a["result_sha256"], b["result_sha256"])
        self.assertNotEqual(a["id"], b["id"], "runs are immutable records, never overwritten")

    def test_metric_explanations_for_a_column_and_for_a_model_score(self):
        m = self.i.explain_metric(self.alice, self.asset["id"], column="x1", statistic="mean", by="plan")
        self.assertIn("by", m["result"])
        self.assertTrue(m["registers"]["calculated_results"])
        reg = self.p7.registry.register(self.alice, self.model, name="Metric model")
        v = reg["versions"][0]["id"]
        mm = self.i.explain_metric(self.alice, model_version_id=v, metric="roc_auc")
        self.assertEqual(mm["result"]["basis"], "hold-out")
        self.assertIn("ranks", mm["result"]["meaning"])
        self.assertEqual(code_of(self.i.explain_metric, self.alice, model_version_id=v, metric="nope")[0], "VALIDATION_ERROR")
        self.assertEqual(code_of(self.i.explain_metric, self.alice, self.asset["id"], column="missing")[0], "VALIDATION_ERROR")

    def test_scenario_keeps_the_baseline_validates_inputs_and_warns_out_of_range(self):
        r = self.i.scenario(self.alice, self.model, baseline=BASE, scenarios=[{"name": "More x1", "changes": {"x1": 2.0}}, {"name": "Extreme", "changes": {"x1": 6.0}}])
        res = r["result"]
        self.assertEqual(res["baseline"]["inputs"], BASE)
        self.assertEqual(len(res["scenarios"]), 2)
        self.assertFalse(res["scenarios"][0]["out_of_range"])
        self.assertTrue(res["scenarios"][1]["out_of_range"])
        self.assertTrue(res["out_of_range_warnings"])
        self.assertTrue(res["sensitivity"])
        self.assertEqual(r["params"]["baseline"], BASE, "the baseline is stored as given")
        self.assertTrue(any("outside" in q["text"] for q in r["registers"]["unresolved_questions"]))
        self.assertTrue(any("not what would happen" in x["text"] or "does not show" in x["text"] for x in r["registers"]["model_interpretations"]))
        causal_free(self, r)
        again = self.i.scenario(self.alice, self.model, baseline=BASE, scenarios=[{"name": "More x1", "changes": {"x1": 2.0}}, {"name": "Extreme", "changes": {"x1": 6.0}}])
        self.assertEqual([s["score"]["value"] for s in again["result"]["scenarios"]], [s["score"]["value"] for s in res["scenarios"]])

    def test_scenario_rejects_bad_inputs(self):
        sc = [{"name": "a", "changes": {"x1": 1.0}}]
        for bad in ({"x1": "abc", "x2": 0, "plan": "basic", "noise": 0}, {"x1": 1, "x2": 0, "plan": "gold", "noise": 0}, {"x1": 1, "x2": 0, "plan": "basic"}, {"x1": 1e9, "x2": 0, "plan": "basic", "noise": 0}, {"zzz": 1}):
            self.assertEqual(code_of(self.i.scenario, self.alice, self.model, baseline=bad, scenarios=sc)[0], "VALIDATION_ERROR", str(bad))
        self.assertEqual(code_of(self.i.scenario, self.alice, self.model, baseline=BASE, scenarios=[])[0], "VALIDATION_ERROR")
        self.assertEqual(code_of(self.i.scenario, self.alice, baseline=BASE, scenarios=sc)[0], "VALIDATION_ERROR")

    def test_causal_candidates_report_associations_not_effects(self):
        r = self.i.causal_candidates(self.alice, self.asset2["id"], treatment="treated", outcome="bought", covariates=["x1", "noise"])
        self.assertEqual(r["result"]["label"], "Association estimates, not causal effects")
        names = {c["method"] for c in r["result"]["candidates"]}
        self.assertIn("Instrumental variable", names)
        iv = next(c for c in r["result"]["candidates"] if c["method"] == "Instrumental variable")
        self.assertEqual(iv["status"], "needs_information")
        self.assertTrue(all(c["assumptions"] for c in r["result"]["candidates"]))
        self.assertIsNotNone(r["result"]["estimates"]["adjusted_interval_95"])
        causal_free(self, r)
        again = self.i.causal_candidates(self.alice, self.asset2["id"], treatment="treated", outcome="bought", covariates=["x1", "noise"])
        self.assertEqual(again["result_sha256"], r["result_sha256"], "bootstrap is seeded")

    def test_isolation_between_people(self):
        run = self.i.root_cause(self.alice, self.asset["id"], outcome="bought")
        self.assertEqual(code_of(self.i.get_run, self.bob, run["id"])[1], 404)
        self.assertEqual(code_of(self.i.root_cause, self.bob, self.asset["id"], outcome="bought")[1], 404)
        self.assertEqual(code_of(self.i.scenario, self.bob, self.model, baseline=BASE, scenarios=[{"name": "a", "changes": {"x1": 1}}])[1], 404)
        self.assertEqual(self.i.list_runs(self.bob)["total"], 0)

    def test_report_from_intelligence_runs_keeps_registers_and_limits(self):
        a = self.i.root_cause(self.alice, self.asset["id"], outcome="bought")
        s = self.i.scenario(self.alice, self.model, baseline=BASE, scenarios=[{"name": "More x1", "changes": {"x1": 2.0}}])
        rep = self.p7.reports.create(self.alice, self.project, source_kind="scenario", source={"adv_run_ids": [a["id"], s["id"]]})
        text = json.dumps(rep["content"]).lower()
        self.assertIn("association", text)
        self.assertIn("scenarios against the baseline", text)
        self.assertTrue(rep["evidence"])
