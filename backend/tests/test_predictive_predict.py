"""Single and batch prediction: schema and pipeline preservation, input validation, honest probabilities, flagged rows, CSV safety."""
import csv
import io
import unittest

import numpy as np
import pandas as pd

import ml_support as S
from app.modules.predictive import predict, train
from app.modules.predictive.config import parse_config
from app.modules.predictive.errors import MlError


def fit(raw, dates=(), **cfg_raw):
    df, types = S.typed(raw, dates=dates)
    cfg = parse_config({"time_budget_s": 60, **cfg_raw}, types)
    res = train.run_automl(df, types, cfg, extended=False, rep=train.Reporter(None, None, 60), dataset={})
    bundle = predict.load_bundle(res.model_bytes)
    bg = pd.read_csv(io.BytesIO(res.background_csv), dtype=object, keep_default_na=False, na_values=[""])
    return res, bundle, bg, df, types


class ClassificationPrediction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.bundle, cls.bg, cls.df, cls.types = fit(S.classification_frame(500), target="bought", algorithms=["logistic_regression", "random_forest"])
        cls.values = {"x1": 1.5, "x2": -1.0, "plan": "pro", "noise": 0.0}

    def test_input_schema_lists_only_the_columns_the_model_uses_with_their_roles(self):
        schema = self.bundle["input_schema"]
        self.assertEqual(sorted(s["name"] for s in schema), ["noise", "plan", "x1", "x2"])
        by = {s["name"]: s for s in schema}
        self.assertEqual(by["x1"]["role"], "numeric")
        self.assertEqual(by["plan"]["role"], "categorical")
        self.assertEqual(sorted(by["plan"]["levels"]), ["basic", "plus", "pro"])
        self.assertIsNotNone(by["x1"]["min"])

    def test_single_prediction_returns_label_probabilities_summing_to_one_and_a_reading(self):
        out = predict.predict_one(self.bundle, self.values, self.bg, explain=False)
        self.assertIn(out["prediction"]["label"], self.bundle["classes"])
        self.assertAlmostEqual(sum(p["probability"] for p in out["probabilities"]), 1.0, places=3)
        self.assertEqual(out["prediction"]["probability"], max(p["probability"] for p in out["probabilities"]))
        reading = out["probability_reading"].lower()
        self.assertGreater(len(reading), 40)
        self.assertNotRegex(reading, r"\bwill\b|\bcertain|\bguaranteed to")                  # never worded as a promise
        self.assertEqual(out["input_issues"], [])

    def test_probabilities_are_described_in_line_with_the_calibration_check(self):
        for verdict, expect in (("good", "estimates, not guarantees"), ("fair", "approximate scores"), ("poor", "NOT match"), (None, "could not be checked")):
            b = dict(self.bundle, summary={"calibration": verdict})
            self.assertIn(expect, predict.predict_one(b, self.values, None, explain=False)["probability_reading"])

    def test_the_saved_file_contains_the_preprocessing_and_reloading_it_changes_nothing(self):
        """Raw values go in; imputation, encoding and scaling happen inside the saved pipeline, step by step identically to calling it whole."""
        a = predict.predict_one(self.bundle, self.values, None, explain=False)
        again = predict.predict_one(predict.load_bundle(self.res.model_bytes), self.values, None, explain=False)
        self.assertEqual(a["prediction"], again["prediction"])
        pipe = self.bundle["pipeline"]
        self.assertEqual([n for n, _ in pipe.steps][:2], ["coerce", "prep"])
        frame = pd.DataFrame([{k: str(v) for k, v in self.values.items()}])
        whole = pipe.predict_proba(frame)
        staged = pipe[-1].predict_proba(pipe[:-1].transform(frame))
        self.assertTrue(np.allclose(whole, staged))
        self.assertGreater(pipe[:-1].transform(frame).shape[1], len(self.values) - 1)        # the category was expanded into indicator columns

    def test_the_direction_of_the_effects_matches_the_known_structure_of_the_data(self):
        lo = predict.predict_one(self.bundle, {**self.values, "x1": -2.0}, None, explain=False)
        hi = predict.predict_one(self.bundle, {**self.values, "x1": 2.0}, None, explain=False)
        pos = lambda o: o["positive"]["probability"]
        self.assertGreater(pos(hi), pos(lo) + 0.3)

    def test_local_explanation_is_labelled_as_not_shap_and_not_causal(self):
        out = predict.predict_one(self.bundle, self.values, self.bg, explain=True)
        local = out["local"]
        self.assertIn("not SHAP", local["method"])
        self.assertIn("do not show what would happen", local["note"])
        top = local["effects"][0]
        self.assertEqual(top["feature"], "x1")
        self.assertEqual(top["direction"], "raises")

    def test_local_explanation_is_unavailable_without_reference_rows_and_says_so(self):
        out = predict.predict_one(self.bundle, self.values, None, explain=True)
        self.assertFalse(out["local"]["available"])

    def test_unknown_inputs_are_refused_by_name(self):
        with self.assertRaises(MlError) as ctx:
            predict.predict_one(self.bundle, {**self.values, "cheat": 1}, None, explain=False)
        self.assertEqual(ctx.exception.code, "UNKNOWN_INPUT")
        self.assertIn("cheat", ctx.exception.message)

    def test_empty_input_is_refused(self):
        with self.assertRaises(MlError) as ctx:
            predict.predict_one(self.bundle, {}, None, explain=False)
        self.assertEqual(ctx.exception.code, "NO_INPUT")

    def test_non_scalar_and_oversized_values_are_refused(self):
        for bad in ({"x1": [1, 2]}, {"x1": {"a": 1}}, {"plan": "p" * 2000}):
            with self.assertRaises(MlError) as ctx:
                predict.predict_one(self.bundle, {**self.values, **bad}, None, explain=False)
            self.assertEqual(ctx.exception.code, "INPUT_INVALID")

    def test_missing_unreadable_unseen_and_out_of_range_values_are_flagged_not_hidden(self):
        out = predict.predict_one(self.bundle, {"x1": "abc", "x2": 9999, "plan": "platinum", "noise": None}, None, explain=False)
        kinds = {(i["column"], i["kind"]) for i in out["input_issues"]}
        self.assertIn(("x1", "invalid"), kinds)
        self.assertIn(("x2", "range"), kinds)
        self.assertIn(("plan", "unseen"), kinds)
        self.assertIn(("noise", "missing"), kinds)
        self.assertTrue(all(i["message"] for i in out["input_issues"]))

    def test_boolean_json_values_are_accepted_as_true_false_text(self):
        # the unit under test is only the coercion: a JSON boolean must not crash or become "True"
        out = predict.predict_one(self.bundle, {**self.values, "plan": True}, None, explain=False)
        self.assertEqual(out["inputs"]["plan"], "true")

    def test_inputs_are_echoed_as_text_without_changing_their_value(self):
        out = predict.predict_one(self.bundle, {**self.values, "x1": 1.5}, None, explain=False)
        self.assertEqual(out["inputs"]["x1"], "1.5")


class RegressionPrediction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.bundle, cls.bg, cls.df, cls.types = fit(S.regression_frame(400), dates=("when",), target="price", algorithms=["linear_regression", "random_forest"])
        cls.values = {"x1": 5.0, "x2": 0.5, "cat": "v", "when": "2023-03-01"}

    def test_prediction_is_near_the_known_function_and_carries_a_typical_error_range(self):
        out = predict.predict_one(self.bundle, self.values, None, explain=False)
        truth = 3.0 * 5.0 + 2.0 * 0.5 + 4.0
        self.assertAlmostEqual(out["prediction"]["value"], truth, delta=2.5)
        te = out["typical_error"]
        self.assertAlmostEqual(te["range_80"][1] - te["range_80"][0], 2 * te["within_80"], places=3)
        self.assertIn("not a guaranteed interval", te["reading"])

    def test_prediction_outside_the_training_target_range_is_flagged(self):
        out = predict.predict_one(self.bundle, {**self.values, "x1": 5000.0}, None, explain=False)
        cols = {i["column"] for i in out["input_issues"]}
        self.assertIn("x1", cols)
        self.assertIn("(prediction)", cols)

    def test_dates_are_read_and_an_unreadable_date_is_flagged(self):
        out = predict.predict_one(self.bundle, {**self.values, "when": "not a date"}, None, explain=False)
        self.assertIn(("when", "invalid"), {(i["column"], i["kind"]) for i in out["input_issues"]})


class BatchPrediction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = S.classification_frame(500)
        cls.res, cls.bundle, cls.bg, cls.df, cls.types = fit(cls.raw, target="bought", algorithms=["logistic_regression"])

    def new_data(self, n=60, **edits):
        d = self.raw.head(n).drop(columns=["bought"]).copy()
        for k, v in edits.items():
            d[k] = v
        return S.typed(d.assign(bought="yes"))[0].drop(columns=["bought"])

    def test_batch_returns_one_row_per_input_in_order_with_the_original_values(self):
        data = self.new_data()
        blob, summary = predict.predict_table(self.bundle, data, source_rows=[int(i) for i in data.index])
        rows = list(csv.DictReader(io.StringIO(blob.decode())))
        self.assertEqual(len(rows), 60)
        self.assertEqual([int(r["row"]) for r in rows], list(range(1, 61)))
        self.assertEqual(rows[0]["plan"], str(self.raw.iloc[0]["plan"]))
        self.assertEqual(summary["rows"], 60)
        self.assertEqual(sum(c["rows"] for c in summary["prediction_counts"]), 60)
        for r in rows:
            self.assertIn(r["prediction"], self.bundle["classes"])
            self.assertAlmostEqual(sum(float(r[f"probability_{c}"]) for c in self.bundle["classes"]), 1.0, places=3)

    def test_batch_agrees_with_single_predictions_for_the_same_rows(self):
        data = self.new_data(5)
        blob, _ = predict.predict_table(self.bundle, data)
        rows = list(csv.DictReader(io.StringIO(blob.decode())))
        for i, r in enumerate(rows):
            single = predict.predict_one(self.bundle, {c: r[c] for c in ("x1", "x2", "plan", "noise")}, None, explain=False)
            self.assertEqual(single["prediction"]["label"], r["prediction"])
            self.assertAlmostEqual(single["prediction"]["probability"], float(r["probability"]), places=3)

    def test_rows_with_unseen_or_out_of_range_values_are_flagged_and_counted(self):
        data = self.new_data(40)
        data.loc[data.index[0], "plan"] = "platinum"
        data.loc[data.index[1], "x1"] = 1e6
        blob, summary = predict.predict_table(self.bundle, data)
        rows = list(csv.DictReader(io.StringIO(blob.decode())))
        self.assertIn("unseen:plan", rows[0]["flags"])
        self.assertIn("range:x1", rows[1]["flags"])
        self.assertEqual(summary["rows_with_warnings"], 2)
        self.assertEqual(summary["issues_by_column"]["plan"]["unseen"], 1)

    def test_large_shift_in_the_new_data_is_reported_as_possible_drift(self):
        data = self.new_data(40, plan="platinum")
        _, summary = predict.predict_table(self.bundle, data)
        self.assertIn("plan", summary["possible_drift"])
        self.assertTrue(any("differ from the training data" in n for n in summary["notes"]))

    def test_missing_columns_are_named_in_the_error(self):
        data = self.new_data().drop(columns=["x1", "plan"])
        with self.assertRaises(MlError) as ctx:
            predict.predict_table(self.bundle, data)
        self.assertEqual(ctx.exception.code, "MISSING_COLUMNS")
        self.assertIn("x1", ctx.exception.message)

    def test_row_limit_and_empty_input_are_enforced(self):
        data = self.new_data(30)
        with self.assertRaises(MlError) as ctx:
            predict.predict_table(self.bundle, data, max_rows=10)
        self.assertEqual(ctx.exception.code, "BATCH_TOO_LARGE")
        with self.assertRaises(MlError) as ctx:
            predict.predict_table(self.bundle, data.head(0))
        self.assertEqual(ctx.exception.code, "BATCH_EMPTY")

    def test_formula_injection_is_neutralised_in_the_downloaded_csv(self):
        data = self.new_data(10)
        data["plan"] = "=HYPERLINK(\"http://evil.example\",\"x\")"
        blob, _ = predict.predict_table(self.bundle, data)
        rows = list(csv.DictReader(io.StringIO(blob.decode())))
        self.assertTrue(all(r["plan"].startswith("'") for r in rows))

    def test_an_input_column_named_prediction_does_not_collide_with_the_output_columns(self):
        raw = S.classification_frame(300).rename(columns={"noise": "prediction", "x2": "flags"})
        res, bundle, bg, df, types = fit(raw, target="bought", algorithms=["logistic_regression"])
        data = df.drop(columns=["bought"]).head(5)
        blob, _ = predict.predict_table(bundle, data)
        head = next(csv.reader(io.StringIO(blob.decode())))
        self.assertEqual(len(head), len(set(head)))
        self.assertIn("prediction_model", head)

    def test_progress_and_cancellation_are_honoured(self):
        data = self.new_data(60)
        seen = []
        predict.predict_table(self.bundle, data, progress=lambda d, n: seen.append((d, n)))
        self.assertEqual(seen[-1], (60, 60))

        def stop():
            raise RuntimeError("cancelled")
        with self.assertRaises(RuntimeError):
            predict.predict_table(self.bundle, data, cancel=stop)

    def test_regression_batch_summarises_the_predicted_distribution(self):
        res, bundle, bg, df, types = fit(S.regression_frame(300), dates=("when",), target="price", algorithms=["ridge"])
        _, summary = predict.predict_table(bundle, df.drop(columns=["price"]).head(50))
        ps = summary["prediction_summary"]
        self.assertLessEqual(ps["min"], ps["p25"])
        self.assertLessEqual(ps["p25"], ps["median"])
        self.assertLessEqual(ps["median"], ps["p75"])
        self.assertLessEqual(ps["p75"], ps["max"])
        self.assertIn("typical_error", summary)


class ModelFile(unittest.TestCase):
    def test_a_file_that_is_not_a_model_bundle_is_refused(self):
        import joblib
        buf = io.BytesIO()
        joblib.dump({"hello": "world"}, buf)
        with self.assertRaises(MlError) as ctx:
            predict.load_bundle(buf.getvalue())
        self.assertEqual(ctx.exception.code, "MODEL_UNREADABLE")

    def test_training_library_version_mismatch_is_noted(self):
        res, bundle, *_ = fit(S.classification_frame(200), target="bought", algorithms=["logistic_regression"])
        self.assertEqual(bundle["_notes"], [])
        bundle["versions"] = dict(bundle["versions"], scikit_learn="0.0.1")
        raw = res.model_bytes
        import joblib
        b = joblib.load(io.BytesIO(raw))
        b["versions"]["scikit_learn"] = "0.0.1"
        buf = io.BytesIO()
        joblib.dump(b, buf)
        self.assertTrue(predict.load_bundle(buf.getvalue())["_notes"])


if __name__ == "__main__":
    unittest.main()
