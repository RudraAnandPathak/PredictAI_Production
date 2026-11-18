"""The universal "Explanation" for predictive modelling: present on every output, written only from the calculated result, every figure traceable, technical results
untouched, no causal claims, and honest ("unavailable") whenever the result cannot support a statement."""
import copy
import json
import re
import unittest

import analytics_support as sup
import ml_support as S
import test_predictive_service as svc
from app.modules.explanation import SCHEMA, TITLE, core, explain
from test_workspace_uploads import upload

ML_KINDS = ["ml:assessment", "ml:problem", "ml:features", "ml:ranking", "ml:baseline", "ml:evaluation", "ml:importance", "ml:overfitting", "ml:learning_curve", "ml:hpo",
            "ml:prediction", "ml:batch", "ml:pdp", "ml:model_compare"]
REPORT_KINDS = ["problem", "features", "ranking", "baseline", "evaluation", "importance", "overfitting"]
NEGATION = re.compile(r"\b(not|no|never|cannot|can't|doesn't|does not|isn't|is not|without|neither|nor|rather than|instead of)\b", re.I)
CAUSAL = re.compile(r"\b(cause[sd]?|causing|causal|causation|because of|due to|leads? to|led to|results? in|resulted in|responsible for|drives?|driven by|produces?)\b", re.I)


def text_of(ex):
    return " ".join([ex.get("summary") or "", *ex.get("points", []), *ex.get("cautions", [])])


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


class Models(svc.Fixture):
    """One model of each kind, trained once through the real service."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p, a = cls.p, cls.alice
        cls.reports, cls.models = {}, {}
        specs = {
            "binary": (cls.asset["id"], {"target": "bought", "algorithms": ["logistic_regression", "random_forest"], "time_budget_s": 90}),
            "tuned": (cls.asset["id"], {"target": "bought", "algorithms": ["logistic_regression"], "hpo": {"method": "grid", "top_k": 1, "cv_folds": 3}, "time_budget_s": 90}),
        }
        multi = upload(cls.ws, a, cls.project, "grades.csv", sup.csv_of(S.multiclass_frame(400)))["asset"]["id"]
        reg = upload(cls.ws, a, cls.project, "prices.csv", sup.csv_of(S.regression_frame(400)))["asset"]["id"]
        specs["multiclass"] = (multi, {"target": "grade", "algorithms": ["logistic_regression", "decision_tree"], "time_budget_s": 90})
        specs["regression"] = (reg, {"target": "price", "algorithms": ["ridge", "random_forest"], "time_budget_s": 90})
        for name, (asset_id, cfg) in specs.items():
            cls.h.clock.advance(seconds=400)                                      # stay inside the hourly training limit
            done = svc.wait_job(p, a, p.start_training(a, asset_id, cfg)["id"], timeout=200)
            assert done["status"] == "completed", (name, done.get("error"))
            cls.models[name] = done["model"]
            cls.reports[name] = p.report(a, done["model"]["id"])

    def explanations(self, name):
        rep = self.reports[name]
        out = {k: v for k, v in rep["explanations"].items()}
        for i, pdp in enumerate((rep.get("importance") or {}).get("partial_dependence", []) or []):
            if "explanation" in pdp:
                out[f"pdp[{i}]"] = pdp["explanation"]
        return out


class Coverage(Models):
    def test_every_kind_of_predictive_output_has_a_registered_explainer(self):
        registered = sorted(k for k in core.REGISTRY if k.startswith("ml:"))
        self.assertEqual(registered, sorted(ML_KINDS))

    def test_each_model_report_carries_an_explanation_for_every_section_titled_exactly_explanation(self):
        for name in self.reports:
            exps = self.reports[name]["explanations"]
            self.assertTrue(set(REPORT_KINDS) <= set(exps), (name, set(REPORT_KINDS) - set(exps)))
            for kind, ex in exps.items():
                self.assertEqual(ex["title"], TITLE, (name, kind))
                self.assertEqual(ex["schema"], SCHEMA)
                self.assertEqual(ex["status"], "available", (name, kind, ex.get("reason")))
                self.assertTrue(ex["summary"], (name, kind))
                self.assertGreater(ex["basis"]["numbers_cited"], 0, (name, kind))

    def test_tuning_and_learning_curve_are_explained_when_they_exist(self):
        self.assertEqual(self.reports["tuned"]["explanations"]["hpo"]["status"], "available")
        self.assertNotIn("hpo", self.reports["binary"]["explanations"])
        for name, rep in self.reports.items():
            if (rep.get("learning_curve") or {}).get("available"):
                self.assertEqual(rep["explanations"]["learning_curve"]["status"], "available", name)

    def test_partial_dependence_charts_are_explained_one_by_one(self):
        count = 0
        for name, rep in self.reports.items():
            for pdp in (rep.get("importance") or {}).get("partial_dependence", []) or []:
                count += 1
                self.assertEqual(pdp["explanation"]["title"], TITLE)
                if pdp.get("available", True):
                    self.assertEqual(pdp["explanation"]["status"], "available", (name, pdp.get("feature"), pdp["explanation"].get("reason")))
                else:                                                             # e.g. a date column or a constant: say so rather than invent a reading
                    self.assertEqual(pdp["explanation"]["status"], "unavailable", (name, pdp.get("feature")))
                    self.assertTrue(pdp["explanation"]["reason"])
        self.assertGreater(count, 0)

    def test_assessment_prediction_batch_pdp_and_comparison_are_explained(self):
        a = self.asset["id"]
        self.assertEqual(self.p.assess(self.alice, a, {"target": "bought"})["explanation"]["status"], "available")
        one = self.p.predict(self.alice, self.models["binary"]["id"], svc.VALUES, save=False)
        self.assertEqual(one["explanation"]["status"], "available")
        batch = self.p.predict_batch(self.alice, self.models["binary"]["id"], a)
        self.assertEqual(batch["summary"]["explanation"]["status"], "available")
        self.assertEqual(self.p.partial_dependence(self.alice, self.models["binary"]["id"], "x1")["explanation"]["status"], "available")
        cmp = self.p.compare_models(self.alice, [self.models["binary"]["id"], self.models["tuned"]["id"]])
        self.assertEqual(cmp["explanation"]["status"], "available")

    def test_regression_and_multiclass_predictions_are_explained_too(self):
        reg = self.p.predict(self.alice, self.models["regression"]["id"], {"x1": 4.0, "x2": 0.3, "cat": "u", "when": "2023-02-01"}, save=False)
        self.assertEqual(reg["explanation"]["status"], "available", reg["explanation"].get("reason"))
        mc = self.p.predict(self.alice, self.models["multiclass"]["id"], {"a": 0.2, "b": -0.4, "c": 1.0}, save=False)
        self.assertEqual(mc["explanation"]["status"], "available", mc["explanation"].get("reason"))


class Faithfulness(Models):
    def test_explaining_never_changes_the_technical_result(self):
        for name, rep in self.reports.items():
            technical = {k: v for k, v in rep.items() if k != "explanations"}
            before = json.dumps(technical, sort_keys=True)
            for kind in REPORT_KINDS:
                explain(f"ml:{kind}", rep)
            self.assertEqual(json.dumps(technical, sort_keys=True), before, name)

    def test_explanations_are_deterministic_and_never_stored_with_the_result(self):
        for name, rep in self.reports.items():
            for kind in REPORT_KINDS:
                self.assertEqual(explain(f"ml:{kind}", rep), explain(f"ml:{kind}", copy.deepcopy(rep)), (name, kind))
        stored = json.loads(self.h.storage.read_bytes(self.h.rows("SELECT report_key FROM ml_models WHERE id = ?", (self.models["binary"]["id"],))[0]["report_key"]))
        self.assertNotIn("explanations", stored)

    def test_every_number_in_the_prose_is_a_number_from_the_result(self):
        """Independent of the Say mechanism: each numeric token must match some figure in the result (as is, as a percentage, or as the absolute value of an error)."""
        for name, rep in self.reports.items():
            technical = {k: v for k, v in rep.items() if k != "explanations"}
            figures = set()
            _leaves(technical, figures)
            for kind, ex in self.explanations(name).items():
                for token in re.findall(r"(?<![\w.])-?\d[\d,]*\.?\d*(?![\w])", text_of(ex)):
                    clean = token.replace(",", "").rstrip(".")
                    self.assertTrue(_matches(float(clean), clean, figures), f"{name}/{kind}: {token!r} is not a figure of the result: {text_of(ex)[:300]}")

    def test_changing_a_figure_in_the_result_changes_the_prose_and_never_leaves_the_old_figure(self):
        rep = copy.deepcopy(self.reports["binary"])
        old = rep["evaluation"]["metrics"]["accuracy"]
        old_pct = f"{old * 100:.0f}%"
        new = 0.9 if abs(old - 0.9) > 0.05 else 0.2
        rep["evaluation"]["metrics"]["accuracy"] = new
        rep["evaluation"]["n"] = 4321
        before = text_of(explain("ml:evaluation", self.reports["binary"]))
        after = text_of(explain("ml:evaluation", rep))
        self.assertIn(old_pct, before)
        self.assertNotIn(old_pct, after)
        self.assertIn(f"{new * 100:.0f}%", after)
        self.assertIn("4,321", after)
        self.assertNotIn("4,321", before)

    def test_a_figure_that_is_missing_from_the_result_makes_the_explanation_unavailable_not_invented(self):
        needs = {"ranking": "best", "baseline": "baseline", "evaluation": "evaluation", "importance": "importance", "overfitting": "candidates", "problem": "problem", "features": "features"}
        for kind, key in needs.items():
            rep = copy.deepcopy(self.reports["binary"])
            del rep[key]
            ex = explain(f"ml:{kind}", rep)
            self.assertEqual(ex["status"], "unavailable", kind)
            self.assertIsNone(ex["summary"], kind)
            self.assertEqual(ex["title"], TITLE)


class Honesty(Models):
    def test_malformed_inputs_give_unavailable_never_an_exception(self):
        for kind in ML_KINDS:
            for bad in ({}, {"x": 1}, {"best": None}, {"candidates": "x"}, {"evaluation": []}, {"probabilities": 3}, {"effects": {}}, {"models": [None]}, {"problem": 5}):
                ex = explain(kind, bad)
                self.assertEqual(ex["title"], TITLE, (kind, bad))
                self.assertEqual(ex["status"], "unavailable", (kind, bad))
                self.assertTrue(ex["reason"])
                self.assertIsNone(ex["summary"])

    def test_no_explanation_makes_a_causal_claim_outside_a_negation(self):
        pieces = []
        for name in self.reports:
            for kind, ex in self.explanations(name).items():
                pieces.append((f"{name}/{kind}", text_of(ex)))
        for model in ("binary", "multiclass", "regression"):
            values = {"binary": svc.VALUES, "multiclass": {"a": 0.2, "b": -0.4, "c": 1.0}, "regression": {"x1": 4.0, "x2": 0.3, "cat": "u", "when": "2023-02-01"}}[model]
            pieces.append((f"prediction/{model}", text_of(self.p.predict(self.alice, self.models[model]["id"], values, save=False)["explanation"])))
        pieces.append(("pdp", text_of(self.p.partial_dependence(self.alice, self.models["binary"]["id"], "x1")["explanation"])))
        for where, text in pieces:
            for sentence in sentences(text):
                if CAUSAL.search(sentence):
                    self.assertRegex(sentence, NEGATION, f"{where}: a causal word is used affirmatively: {sentence!r}")

    def test_the_causal_language_check_itself_works(self):
        for bad in ("Age causes churn.", "Churn is driven by price.", "The plan leads to higher spend.", "Income results in a higher score."):
            self.assertTrue(CAUSAL.search(bad) and not NEGATION.search(bad), bad)
        for fine in ("Importance does not show cause.", "This is not a causal effect.", "It cannot be read as causation."):
            self.assertTrue(CAUSAL.search(fine) and NEGATION.search(fine), fine)

    def test_importance_and_partial_dependence_explicitly_say_they_describe_the_model_not_the_world(self):
        for name in ("binary", "regression"):
            imp = self.reports[name]["explanations"]["importance"]
            joined = text_of(imp).lower()
            self.assertTrue("not" in joined and ("cause" in joined or "causal" in joined or "causation" in joined or "real world" in joined or "real-world" in joined), joined)
        pdp = self.p.partial_dependence(self.alice, self.models["binary"]["id"], "x1")["explanation"]
        self.assertRegex(text_of(pdp).lower(), r"(not|no).{0,60}(cause|causal|causation|real world|real-world)|(cause|causal|causation).{0,60}(not|cannot)")

    def test_local_effects_are_never_called_shap(self):
        one = self.p.predict(self.alice, self.models["binary"]["id"], svc.VALUES, save=False)
        blob = json.dumps(one["local"]) + text_of(one["explanation"])
        for sentence in sentences(blob):
            if re.search(r"\bshap\b", sentence, re.I):
                self.assertRegex(sentence, NEGATION, sentence)

    def test_probabilities_are_described_according_to_the_calibration_check(self):
        from app.modules.explanation.predictive import CALIBRATION_WORDS

        for verdict, sentence in CALIBRATION_WORDS.items():
            rep = copy.deepcopy(self.reports["binary"])
            rep["evaluation"]["calibration"]["verdict"] = verdict
            text = text_of(explain("ml:evaluation", rep))
            self.assertIn(sentence, text, verdict)
            for other, other_sentence in CALIBRATION_WORDS.items():
                if other != verdict:
                    self.assertNotIn(other_sentence, text, (verdict, other))
        poor = copy.deepcopy(self.reports["binary"])
        poor["evaluation"]["calibration"]["verdict"] = "poor"
        self.assertNotRegex(text_of(explain("ml:evaluation", poor)), r"(?i)\bchance that\b")

    def test_the_model_is_not_presented_as_better_than_the_baseline_unless_the_figures_say_so(self):
        cases = {"clear": (0.30, 0.01, "beats it clearly"), "within_noise": (0.005, 0.05, "less than the usual variation"), "no_gain": (-0.02, 0.01, "does not beat it")}
        for name, (gain, spread, phrase) in cases.items():
            rep = copy.deepcopy(self.reports["binary"])
            rep["best"]["beats_baseline_by"], rep["best"]["cv_std"] = gain, spread
            text = text_of(explain("ml:ranking", rep))
            self.assertIn(phrase, text, name)
            for other, (_, _, other_phrase) in cases.items():
                if other != name:
                    self.assertNotIn(other_phrase, text, (name, other))

    def test_overfitting_wording_follows_the_stored_verdict(self):
        words = {"low": "no sign of overfitting", "moderate": "some sign of overfitting", "high": "strong signs of overfitting"}
        for level, phrase in words.items():
            rep = copy.deepcopy(self.reports["binary"])
            best = next(c for c in rep["candidates"] if c["id"] == rep["best"]["id"])
            best["overfitting"]["level"] = level
            text = text_of(explain("ml:overfitting", rep))
            self.assertIn(phrase, text, level)
            for other, other_phrase in words.items():
                if other != level:
                    self.assertNotIn(other_phrase, text, (level, other))


def _leaves(node, out):
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        out.add(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _leaves(v, out)
    elif isinstance(node, (list, tuple)):
        out.add(float(len(node)))                                                  # "3 candidates"
        for v in node:
            _leaves(v, out)


def _matches(value, token, figures):
    """A written figure matches a result figure when it is that figure shown to the precision written (0.57 -> 57%, 0.2634 -> 0.263, 4321 -> 4,321)."""
    decimals = len(token.split(".")[1]) if "." in token else 0
    half = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(value - c) <= half for f in figures for c in (f, abs(f), f * 100, abs(f) * 100))


if __name__ == "__main__":
    unittest.main()
