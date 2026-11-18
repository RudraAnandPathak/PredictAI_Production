"""AutoML engine: metrics, validation strategy, ranking, tuning, cancellation, time budget, determinism, leakage safeguards and overfitting detection.
Pure computation on synthetic data with known structure; no database or HTTP."""
import io
import math
import unittest

import numpy as np
import pandas as pd
from sklearn import metrics as skm

import ml_support as S
from app.core.errors import AppError
from app.modules.predictive import metrics as M, predict, problem as prob, train, validation as V
from app.modules.predictive.config import parse_config
from app.modules.predictive.errors import Cancelled, MlError


def run(df, types, *, budget=60, reporter=None, **raw):
    cfg = parse_config({"time_budget_s": budget, **raw}, types)
    rep = reporter or train.Reporter(None, None, time_budget_s=budget)
    return train.run_automl(df, types, cfg, extended=False, rep=rep, dataset={"asset_id": "a1"})


def comparable(report):
    """The report without wall-clock timings and library paths, for equality checks between runs."""
    import json
    text = json.dumps(report, sort_keys=True, default=str)
    obj = json.loads(text)

    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items() if k not in ("timing", "duration_s", "fit_s", "cv_s", "resources", "fit_s_per_fold", "predict_ms_per_1000_rows", "duration_ms", "isolation")}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        return o
    return scrub(obj)


class MetricTests(unittest.TestCase):
    def test_binary_metrics_match_hand_calculation(self):
        y = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
        p1 = np.array([.9, .8, .7, .3, .6, .2, .2, .1, .1, .1])
        proba = np.column_stack([1 - p1, p1])
        out = M.classification_metrics(y, proba, ["no", "yes"], positive=1)
        m = out["metrics"]
        # predictions at 0.5: TP=3, FN=1, FP=1, TN=5
        self.assertAlmostEqual(m["accuracy"], 0.8)
        self.assertAlmostEqual(m["precision"], 3 / 4)
        self.assertAlmostEqual(m["recall"], 3 / 4)
        self.assertAlmostEqual(m["f1"], 3 / 4)
        self.assertAlmostEqual(m["specificity"], 5 / 6, places=5)
        self.assertAlmostEqual(m["balanced_accuracy"], (3 / 4 + 5 / 6) / 2, places=5)
        self.assertEqual(out["confusion_matrix"]["counts"], [[5, 1], [1, 3]])
        self.assertAlmostEqual(m["roc_auc"], skm.roc_auc_score(y, p1), places=5)
        self.assertAlmostEqual(m["average_precision"], skm.average_precision_score(y, p1), places=5)
        self.assertAlmostEqual(m["log_loss"], skm.log_loss(y, proba), places=5)
        self.assertEqual(out["support"], {"no": 6, "yes": 4})

    def test_multiclass_metrics_and_pr_auc_is_deliberately_not_computed(self):
        y = np.array([0, 1, 2, 0, 1, 2, 0, 1, 2, 2])
        proba = np.eye(3)[[0, 1, 2, 0, 2, 2, 1, 1, 2, 2]] * 0.8 + 0.1
        out = M.classification_metrics(y, proba, ["a", "b", "c"], positive=None)
        self.assertIn("average_precision", out["not_computed"])
        self.assertNotIn("average_precision", out["metrics"])
        self.assertNotIn("precision", out["metrics"])        # binary-only
        self.assertAlmostEqual(out["metrics"]["f1_macro"], skm.f1_score(y, proba.argmax(1), average="macro"), places=5)
        self.assertAlmostEqual(out["metrics"]["roc_auc"], skm.roc_auc_score(y, proba / proba.sum(1, keepdims=True), multi_class="ovr"), places=5)

    def test_single_class_evaluation_rows_report_ranking_metrics_as_not_computed(self):
        y = np.zeros(10, dtype=int)
        proba = np.tile([.7, .3], (10, 1))
        out = M.classification_metrics(y, proba, ["no", "yes"], positive=1)
        self.assertIn("roc_auc", out["not_computed"])
        self.assertNotIn("roc_auc", out["metrics"])

    def test_regression_metrics_match_numpy(self):
        y = np.array([10., 12, 15, 20, 30, 41, 5, 8])
        pred = np.array([11., 11, 17, 18, 33, 40, 6, 7])
        out = M.regression_metrics(y, pred, n_params=2, linear=True)
        m = out["metrics"]
        e = pred - y
        self.assertAlmostEqual(m["mae"], np.abs(e).mean(), places=5)
        self.assertAlmostEqual(m["mse"], (e ** 2).mean(), places=5)
        self.assertAlmostEqual(m["rmse"], math.sqrt((e ** 2).mean()), places=5)
        self.assertAlmostEqual(m["r2"], skm.r2_score(y, pred), places=5)
        self.assertAlmostEqual(m["mape"], np.abs(e / y).mean(), places=5)
        n, k = len(y), 2
        self.assertAlmostEqual(m["adjusted_r2"], 1 - (1 - m["r2"]) * (n - 1) / (n - k - 1), places=5)
        self.assertAlmostEqual(m["bias"], e.mean(), places=5)

    def test_mape_is_withheld_when_actual_values_include_zero_and_adjusted_r2_for_non_linear_models(self):
        y = np.array([0., 1, 2, 3, 4, 5])
        out = M.regression_metrics(y, y + 0.5, n_params=None, linear=False)
        self.assertNotIn("mape", out["metrics"])
        self.assertIn("mape", out["not_computed"])
        self.assertNotIn("adjusted_r2", out["metrics"])
        self.assertIn("adjusted_r2", out["not_computed"])

    def test_overfit_verdict_thresholds(self):
        self.assertEqual(M.overfit_verdict(0.90, 0.88, "R²")["level"], "low")
        self.assertEqual(M.overfit_verdict(0.90, 0.80, "R²")["level"], "moderate")
        self.assertEqual(M.overfit_verdict(1.00, 0.60, "R²")["level"], "high")
        self.assertEqual(M.overfit_verdict(None, 0.6, "R²")["level"], "unknown")

    def test_calibration_verdict_distinguishes_honest_from_overconfident_probabilities(self):
        rng = np.random.default_rng(0)
        p = rng.uniform(0, 1, 4000)
        y_good = (rng.uniform(0, 1, 4000) < p).astype(int)
        good = M.calibration(y_good, np.column_stack([1 - p, p]), 1, 2)
        self.assertEqual(good["verdict"], "good")
        y_bad = (rng.uniform(0, 1, 4000) < 0.5).astype(int)
        bad = M.calibration(y_bad, np.column_stack([1 - p, p]), 1, 2)
        self.assertEqual(bad["verdict"], "poor")


class MetricChoiceTests(unittest.TestCase):
    def test_accuracy_is_not_the_default_and_is_flagged_for_imbalanced_data(self):
        severe = {"level": "severe", "accuracy_warning": True}
        plan = prob.metric_plan("binary_classification", "auto", balance=severe)
        self.assertEqual(plan["primary"], "average_precision")
        self.assertNotEqual(plan["primary"], "accuracy")
        self.assertTrue(any("accuracy" in r.lower() for r in plan["reasons"] + [plan["note"]]))

    def test_invalid_metric_for_problem_is_refused_with_a_clear_message(self):
        for metric, problem_ in (("roc_auc", "regression"), ("r2", "binary_classification"), ("f1", "multiclass_classification"), ("average_precision", "multiclass_classification")):
            with self.assertRaises(AppError) as ctx:
                prob.metric_plan(problem_, metric)
            self.assertEqual(ctx.exception.code, "METRIC_INVALID")
            self.assertEqual(ctx.exception.status_code, 422)

    def test_regression_target_with_zeros_does_not_compute_mape(self):
        plan = prob.metric_plan("regression", "auto", target={"zeros": 3, "skewness": 0.0, "outlier_share": 0.0})
        self.assertNotIn("MAPE", plan["computed"])
        self.assertEqual([n["metric"] for n in plan["not_computed"]], ["MAPE"])

    def test_heavy_tailed_regression_target_is_ranked_by_mae(self):
        plan = prob.metric_plan("regression", "auto", target={"zeros": 0, "skewness": 3.5, "outlier_share": 0.1})
        self.assertEqual(plan["primary"], "neg_mean_absolute_error")


class ValidationTests(unittest.TestCase):
    def test_stratified_holdout_keeps_class_shares(self):
        y = np.array([0] * 90 + [1] * 10)
        tr, te = V.holdout(y, "binary_classification", 0.2, seed=1)
        self.assertEqual(len(te), 20)
        self.assertEqual(int(y[te].sum()), 2)
        self.assertFalse(set(tr) & set(te))

    def test_time_ordered_holdout_takes_the_most_recent_rows(self):
        order = np.array([5, 1, 3, 9, 7, 2, 8, 4, 6, 0])
        y = np.arange(10)
        tr, te = V.holdout(y, "regression", 0.3, seed=1, time_order=order, shuffle=False)
        self.assertEqual(sorted(order[te]), [7, 8, 9])
        self.assertTrue(order[tr].max() < order[te].min())

    def test_holdout_is_reproducible_for_the_same_seed_and_differs_for_another(self):
        y = np.arange(100) % 2
        a = V.holdout(y, "binary_classification", 0.25, seed=7)[1]
        b = V.holdout(y, "binary_classification", 0.25, seed=7)[1]
        c = V.holdout(y, "binary_classification", 0.25, seed=8)[1]
        self.assertEqual(list(a), list(b))
        self.assertNotEqual(list(a), list(c))

    def test_auto_strategy_choice_follows_the_problem(self):
        self.assertEqual(V.resolve_strategy("auto", "regression", None), "kfold")
        self.assertEqual(V.resolve_strategy("auto", "binary_classification", None), "stratified_kfold")
        self.assertEqual(V.resolve_strategy("auto", "regression", "when"), "time_series")

    def test_time_series_folds_never_validate_on_the_past(self):
        y = np.arange(60, dtype=float)
        cv, desc = V.make_cv("time_series", "regression", y, folds=4, repeats=1, seed=0, shuffle=False)
        for tr, va in V.fold_indices(cv, np.zeros((60, 1)), y):
            self.assertLess(tr.max(), va.min())


class ClassificationRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = S.classification_frame(500)
        cls.df, cls.types = S.typed(cls.raw)
        cls.res = run(cls.df, cls.types, target="bought", algorithms=["logistic_regression", "decision_tree", "random_forest", "naive_bayes"])
        cls.rep = cls.res.report

    def test_ranking_is_by_cross_validation_never_by_the_test_rows(self):
        rank = self.rep["ranking"]
        cv = [r["cv_primary"] for r in rank]
        self.assertEqual(cv, sorted(cv, reverse=True))
        self.assertEqual([r["rank"] for r in rank], list(range(1, len(rank) + 1)))
        self.assertEqual(self.rep["best"]["id"], rank[0]["id"])
        self.assertIn("never to choose", self.rep["validation"]["selection_rule"])

    def test_informative_data_beats_the_baseline_clearly(self):
        self.assertGreater(self.rep["best"]["beats_baseline_by"], 0.15)
        self.assertAlmostEqual(self.rep["baseline"]["cv_primary"], 0.5, places=2)

    def test_identifier_and_constant_columns_are_not_used_as_inputs(self):
        used = [u["name"] for u in self.rep["features"]["used"]]
        self.assertEqual(sorted(used), ["noise", "plan", "x1", "x2"])
        dropped = {d["name"] for d in self.rep["features"]["dropped"]}
        self.assertEqual(dropped, {"row_id", "flat"})

    def test_every_requested_metric_is_reported_for_the_chosen_model(self):
        m = self.rep["evaluation"]["metrics"]
        for key in ("accuracy", "balanced_accuracy", "precision", "recall", "f1", "roc_auc", "average_precision", "log_loss"):
            self.assertIn(key, m)
        ev = self.rep["evaluation"]
        self.assertIn("confusion_matrix", ev)
        self.assertIn("classification_report", ev)
        self.assertEqual(ev["basis"], "holdout")
        self.assertEqual(ev["n"], self.rep["data"]["test_rows"])

    def test_reported_test_metrics_agree_with_an_independent_recomputation(self):
        bundle = predict.load_bundle(self.res.model_bytes)
        names = [s["name"] for s in bundle["input_schema"]]
        # The reference rows are a sample of the data; the independent check is that the saved model reproduces its own reported predictions for them.
        bg = pd.read_csv(io.BytesIO(self.res.background_csv), dtype=object, keep_default_na=False, na_values=[""])
        self.assertTrue(set(names) <= set(bg.columns))
        out = bundle["pipeline"].predict_proba(bundle["pipeline"].named_steps["coerce"].transform(bg[names]))
        self.assertTrue(np.allclose(out.sum(axis=1), 1.0))

    def test_cross_validation_scores_and_spread_are_recorded_for_every_candidate(self):
        for c in self.rep["candidates"]:
            if c["status"] != "ok":
                continue
            self.assertEqual(c["cv"]["folds"], self.rep["validation"]["cross_validation"]["folds"] * self.rep["validation"]["cross_validation"]["repeats"])
            self.assertEqual(len(c["cv"]["primary"]["scores"]), c["cv"]["folds"])
            self.assertGreaterEqual(c["cv"]["primary"]["std"], 0)
            self.assertIn(c["overfitting"]["level"], ("low", "moderate", "high", "unknown"))
            self.assertIn("fit_s", c["timing"])
            self.assertGreater(c["model_bytes"], 0)

    def test_comparison_shows_training_time_model_size_and_generalisation(self):
        row = self.rep["ranking"][0]
        for key in ("cv_primary", "test_primary", "fit_s", "model_bytes", "overfitting", "delta_vs_best"):
            self.assertIn(key, row)
        self.assertIn("generalisation", self.rep["candidates"][0])

    def test_same_seed_reproduces_identical_results_and_another_seed_changes_the_split(self):
        again = run(self.df, self.types, target="bought", algorithms=["logistic_regression", "decision_tree", "random_forest", "naive_bayes"])
        self.assertEqual(comparable(self.rep), comparable(again.report))
        other = run(self.df, self.types, target="bought", algorithms=["logistic_regression"], seed=99)
        self.assertNotEqual(self.rep["evaluation"]["metrics"]["accuracy"], other.report["evaluation"]["metrics"]["accuracy"])

    def test_report_states_the_validation_design_and_reproducibility(self):
        v = self.rep["validation"]
        self.assertTrue(v["holdout"]["stratified"])
        self.assertEqual(v["holdout"]["train_rows"] + v["holdout"]["test_rows"], self.rep["data"]["rows_used"])
        self.assertEqual(self.rep["reproducibility"]["seed"], 20240601)
        self.assertIn("scikit_learn", self.rep["reproducibility"]["versions"])

    def test_result_is_plain_json(self):
        import json
        json.dumps(self.rep, allow_nan=False)             # no NaN / Infinity / numpy types anywhere

    def test_limitations_never_claim_causation_or_guarantees(self):
        text = " ".join(self.rep["limitations"])
        self.assertIn("does not show what causes", text)
        self.assertIn("never as guarantees", text)


class RegressionRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = S.regression_frame(400)
        cls.df, cls.types = S.typed(cls.raw, dates=("when",))
        cls.res = run(cls.df, cls.types, target="price", algorithms=["linear_regression", "ridge", "random_forest", "hist_gradient_boosting"])
        cls.rep = cls.res.report

    def test_regression_is_detected_and_scored_with_error_metrics(self):
        self.assertEqual(self.rep["problem"]["type"], "regression")
        m = self.rep["evaluation"]["metrics"]
        for key in ("mae", "mse", "rmse", "r2", "mape"):
            self.assertIn(key, m)
        self.assertGreater(m["r2"], 0.95)                 # a linear signal plus small noise

    def test_reported_errors_match_the_scatter_data_they_came_from(self):
        ev = self.rep["evaluation"]
        sc = ev["scatter"]
        self.assertEqual(sc["points"], sc["of"])          # small enough to be complete
        a, p = np.array(sc["actual"]), np.array(sc["predicted"])
        self.assertAlmostEqual(float(np.abs(a - p).mean()), ev["metrics"]["mae"], places=3)
        self.assertAlmostEqual(float(np.sqrt(((a - p) ** 2).mean())), ev["metrics"]["rmse"], places=3)
        self.assertAlmostEqual(float(skm.r2_score(a, p)), ev["metrics"]["r2"], places=3)
        self.assertEqual(sum(ev["residuals"]["histogram"]["counts"]), ev["n"])

    def test_error_metrics_are_ranked_as_negatives_but_shown_as_positive_errors(self):
        best = self.rep["best"]
        self.assertTrue(best["lower_is_better"])
        self.assertLess(best["cv_primary"], 0)
        self.assertGreater(best["cv_primary_shown"], 0)
        self.assertAlmostEqual(best["cv_primary_shown"], -best["cv_primary"], places=5)
        scores = [r["cv_primary"] for r in self.rep["ranking"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_adjusted_r2_only_for_linear_models_and_typical_error_range_present(self):
        m = self.rep["evaluation"]["metrics"]
        if self.rep["best"]["algorithm"] in ("linear_regression", "ridge", "lasso"):
            self.assertIn("adjusted_r2", m)
        else:
            self.assertNotIn("adjusted_r2", m)
        te = self.rep["evaluation"]["typical_error"]
        self.assertLessEqual(te["within_80"], te["within_95"])

    def test_date_column_becomes_calendar_features_not_a_raw_timestamp(self):
        when = next(u for u in self.rep["features"]["used"] if u["name"] == "when")
        self.assertEqual(when["role"], "datetime")


class MulticlassRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.types = S.typed(S.multiclass_frame(450))
        cls.rep = run(cls.df, cls.types, target="grade", algorithms=["logistic_regression", "random_forest"]).report

    def test_multiclass_ranked_by_macro_f1_without_pr_auc(self):
        self.assertEqual(self.rep["problem"]["type"], "multiclass_classification")
        self.assertEqual(self.rep["metrics_plan"]["primary"], "f1_macro")
        self.assertIn("average_precision", self.rep["evaluation"]["not_computed"])
        self.assertEqual(len(self.rep["evaluation"]["confusion_matrix"]["labels"]), 3)

    def test_requesting_a_binary_only_metric_for_several_classes_is_refused(self):
        with self.assertRaises(AppError) as ctx:
            run(self.df, self.types, target="grade", algorithms=["logistic_regression"], primary_metric="average_precision")
        self.assertEqual(ctx.exception.code, "METRIC_INVALID")


class ImbalanceRun(unittest.TestCase):
    def test_rare_positive_class_is_ranked_by_pr_auc_and_warns_about_accuracy(self):
        r = np.random.default_rng(2)
        n = 800
        x = r.normal(0, 1, n)
        y = (r.random(n) < 1 / (1 + np.exp(-(2.2 * x - 3.2)))).astype(int)
        raw = pd.DataFrame({"x": x.round(4), "z": r.normal(0, 1, n).round(4), "fraud": np.where(y == 1, "yes", "no")})
        df, types = S.typed(raw)
        rep = run(df, types, target="fraud", algorithms=["logistic_regression", "random_forest"]).report
        self.assertIn(rep["metrics_plan"]["primary"], ("average_precision", "roc_auc"))
        self.assertTrue(rep["data"]["class_balance"]["level"] in ("moderate", "severe"))
        self.assertTrue(rep["data"]["class_balance"]["accuracy_warning"])
        self.assertTrue(any("accuracy" in w.lower() for w in rep["warnings"]))


class TuningRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.types = S.typed(S.classification_frame(400))

    def test_random_search_is_bounded_reported_and_its_winner_is_re_scored(self):
        rep = run(self.df, self.types, target="bought", algorithms=["logistic_regression", "decision_tree"], hpo={"method": "random", "n_iter": 5, "top_k": 1}).report
        h = rep["hpo"]
        self.assertEqual(h["method"], "random")
        self.assertEqual(len(h["algorithms"]), 1)
        a = h["algorithms"][0]
        self.assertLessEqual(a["trials_run"], 5)
        self.assertEqual(a["trials_planned"], 5)
        self.assertIn("best_params", a)
        self.assertIn("best_score", a)
        self.assertIn("duration_s", a)
        self.assertTrue(a["trials"] and "params" in a["trials"][0] and "mean" in a["trials"][0])
        self.assertIn("slightly optimistic", h["caution"])
        tuned = [c for c in rep["candidates"] if c["tuned"]]
        self.assertEqual(len(tuned), 1)
        self.assertEqual(tuned[0]["params"], a["best_params"])

    def test_grid_search_tries_every_combination_inside_the_limit(self):
        rep = run(self.df, self.types, target="bought", algorithms=["decision_tree"],
                  hpo={"method": "grid", "top_k": 1, "param_grids": {"decision_tree": {"max_depth": [2, 4], "min_samples_leaf": [3, 10]}}}).report
        a = rep["hpo"]["algorithms"][0]
        self.assertEqual((a["trials_planned"], a["trials_run"]), (4, 4))
        self.assertEqual({tuple(sorted(t["params"].items())) for t in a["trials"]},
                         {(("max_depth", d), ("min_samples_leaf", m)) for d in (2, 4) for m in (3, 10)})

    def test_a_grid_larger_than_the_trial_limit_is_rejected_before_any_work(self):
        big = {"decision_tree": {"max_depth": [1, 2, 3, 4, 5, 6, 7, 8], "min_samples_leaf": [1, 2, 3, 4, 5, 6, 7, 8], "min_samples_split": [2, 3, 4, 5]}}
        with self.assertRaises(AppError) as ctx:
            parse_config({"target": "bought", "hpo": {"method": "grid", "param_grids": big}}, self.types)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_unknown_or_out_of_range_hyper_parameters_are_rejected(self):
        for grid in ({"decision_tree": {"__class__": [1]}}, {"decision_tree": {"max_depth": [10 ** 9]}}, {"not_an_algorithm": {"max_depth": [3]}}):
            with self.assertRaises(AppError):
                parse_config({"target": "bought", "hpo": {"method": "grid", "param_grids": grid}}, self.types)

    def test_trial_limit_is_enforced_by_entitlement(self):
        with self.assertRaises(AppError):
            parse_config({"target": "bought", "hpo": {"method": "random", "n_iter": 31}}, self.types)
        parse_config({"target": "bought", "hpo": {"method": "random", "n_iter": 31}}, self.types, extended=True)


class CancellationAndBudget(unittest.TestCase):
    def setUp(self):
        self.df, self.types = S.typed(S.classification_frame(400))

    def test_cancellation_between_units_of_work_stops_the_run_and_returns_nothing(self):
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 6
        rep = train.Reporter(None, cancel, time_budget_s=60)
        with self.assertRaises(Cancelled):
            run(self.df, self.types, target="bought", reporter=rep)
        self.assertGreater(calls["n"], 6)

    def test_cancelled_before_start_does_nothing(self):
        with self.assertRaises(Cancelled):
            run(self.df, self.types, target="bought", reporter=train.Reporter(None, lambda: True, time_budget_s=60))

    def test_progress_is_reported_monotonically_with_a_phase_and_message(self):
        seen = []
        rep = train.Reporter(seen.append, None, time_budget_s=60)
        run(self.df, self.types, target="bought", algorithms=["logistic_regression", "decision_tree"], reporter=rep)
        self.assertTrue(seen)
        fractions = [d["fraction"] for d in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(all(d["phase"] and d["message"] for d in seen))
        self.assertLessEqual(max(fractions), 1.0)

    def test_when_time_runs_out_after_the_first_model_the_rest_are_skipped_with_a_reason_and_a_result_is_still_returned(self):
        class FakeClock(train.Reporter):
            def elapsed(self):                       # the budget is "used up" as soon as one model has been fully trained
                return 0.0 if self.done_steps < 7 else 10 ** 6
        out = run(self.df, self.types, target="bought", reporter=FakeClock(None, None, time_budget_s=60), algorithms=["logistic_regression", "random_forest", "hist_gradient_boosting"])
        status = {c["algorithm"]: c["status"] for c in out.report["candidates"]}
        self.assertEqual(status["logistic_regression"], "ok")
        skipped = [c for c in out.report["candidates"] if c["status"] == "skipped"]
        self.assertTrue(skipped)
        self.assertTrue(all("time limit" in c["reason"] for c in skipped))
        self.assertEqual(out.report["best"]["algorithm"], "logistic_regression")

    def test_when_no_model_finishes_within_the_time_limit_the_run_fails_with_a_clear_code(self):
        class AlwaysLate(train.Reporter):
            def elapsed(self):
                return 10 ** 6
        with self.assertRaises(MlError) as ctx:
            run(self.df, self.types, target="bought", reporter=AlwaysLate(None, None, time_budget_s=60), algorithms=["logistic_regression", "random_forest"])
        self.assertEqual(ctx.exception.code, "NO_MODEL_TRAINED")

    def test_a_failing_algorithm_is_recorded_without_data_values_and_does_not_stop_the_run(self):
        from unittest import mock
        from app.modules.predictive import catalog

        original = catalog.CATALOG["decision_tree"]

        def boom(kind, seed, cw):
            raise ValueError("secret-cell-value-4711 broke it")
        broken = type(original)(**{**original.__dict__, "build": boom})
        with mock.patch.dict(catalog.CATALOG, {"decision_tree": broken}):
            out = run(self.df, self.types, target="bought", algorithms=["logistic_regression", "decision_tree"])
        failed = [c for c in out.report["candidates"] if c["algorithm"] == "decision_tree"]
        self.assertEqual(failed[0]["status"], "failed")
        self.assertNotIn("4711", failed[0]["reason"])
        self.assertIn("logistic_regression", [r["id"] for r in out.report["ranking"]])


class LeakageSafeguards(unittest.TestCase):
    def test_a_column_that_is_the_answer_is_excluded_and_the_score_is_not_inflated(self):
        raw = S.classification_frame(400)
        raw["cheat"] = np.where(raw["bought"] == "yes", 1.0, 0.0)
        df, types = S.typed(raw)
        rep = run(df, types, target="bought", algorithms=["logistic_regression", "decision_tree"]).report
        self.assertIn("cheat", [d["name"] for d in rep["features"]["dropped"]])
        self.assertTrue(any(x["column"] == "cheat" for x in rep["features"]["leakage"]))
        self.assertLess(rep["evaluation"]["metrics"]["roc_auc"], 0.97)

    def test_user_can_override_the_leakage_default_and_the_warning_for_near_perfect_scores_appears(self):
        raw = S.classification_frame(400)
        raw["cheat"] = np.where(raw["bought"] == "yes", 1.0, 0.0)
        df, types = S.typed(raw)
        rep = run(df, types, target="bought", algorithms=["logistic_regression"], features={"overrides": {"cheat": {"role": "use"}}}).report
        self.assertIn("cheat", [u["name"] for u in rep["features"]["used"]])
        self.assertTrue(any("almost perfect" in w for w in rep["warnings"]))

    def test_held_out_rows_never_influence_fitting_or_selection(self):
        """Time-ordered split where the relationship REVERSES in the most recent rows: if the test rows leaked into training or selection the test
        score would look good. It must look bad, because the model only ever saw the earlier pattern."""
        r = np.random.default_rng(5)
        n = 600
        x = r.normal(0, 1, n)
        day = np.arange(n)
        y = np.where(day < 480, x, -x) * 3 + r.normal(0, 0.3, n)
        raw = pd.DataFrame({"x": x.round(4), "when": (pd.to_datetime("2022-01-01") + pd.to_timedelta(day, unit="D")).strftime("%Y-%m-%d"), "y": y.round(4)})
        df, types = S.typed(raw, dates=("when",))
        rep = run(df, types, target="y", algorithms=["linear_regression", "ridge"], validation={"time_column": "when", "shuffle": False, "cv_strategy": "time_series"}).report
        self.assertTrue(rep["validation"]["holdout"]["time_ordered"])
        self.assertGreater(rep["best"]["cv_primary"], -1.0)               # cross-validation inside the earlier rows looks excellent (RMSE < 1)
        self.assertLess(rep["evaluation"]["metrics"]["r2"], 0.0)          # the unseen, reversed rows are predicted badly: they were not used

    def test_preprocessing_statistics_come_from_training_rows_only(self):
        """The imputer's median stored in the saved pipeline equals the median of the training rows (not of all rows)."""
        r = np.random.default_rng(8)
        n = 300
        x = r.normal(0, 1, n)
        x[::7] = np.nan
        raw = pd.DataFrame({"x": x, "g": r.normal(0, 1, n), "y": (r.normal(0, 1, n) + np.nan_to_num(x) > 0).astype(int)})
        df, types = S.typed(raw)
        cfg = parse_config({"target": "y", "algorithms": ["logistic_regression"], "time_budget_s": 60}, types)
        res = train.run_automl(df, types, cfg, extended=False, rep=train.Reporter(None, None, 60), dataset={})
        bundle = predict.load_bundle(res.model_bytes)
        pipe = bundle["pipeline"]
        imputer = None
        for step in pipe.named_steps["prep"].transformers_:
            for name, sub in (getattr(step[1], "steps", None) or []):
                if hasattr(sub, "statistics_") and len(sub.statistics_) and name == "impute":
                    imputer = sub
        self.assertIsNotNone(imputer)
        all_rows_median = float(np.nanmedian(df["x"].to_numpy(dtype=float)))
        self.assertNotAlmostEqual(float(imputer.statistics_[0]), all_rows_median, places=9)


class ExplainabilityRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.types = S.typed(S.classification_frame(500))
        cls.rep = run(cls.df, cls.types, target="bought", algorithms=["logistic_regression", "random_forest", "hist_gradient_boosting", "knn", "naive_bayes", "svm"]).report

    def test_permutation_importance_ranks_informative_columns_above_noise(self):
        perm = self.rep["importance"]["permutation"]
        self.assertTrue(perm["available"])
        order = [f["feature"] for f in perm["features"]]
        self.assertEqual(order[0], "x1")
        self.assertLess(order.index("x2"), order.index("noise"))
        self.assertAlmostEqual(sum(f["share"] for f in perm["features"]), 1.0, places=2)
        self.assertIn("does not show that a column causes", perm["note"])

    def test_native_importance_exists_for_linear_and_tree_models_only(self):
        best = self.rep["best"]["algorithm"]
        native = self.rep["importance"]["native"]
        if best in ("logistic_regression", "random_forest", "decision_tree", "extra_trees", "gradient_boosting"):
            self.assertTrue(native["available"])
        else:
            self.assertFalse(native["available"])

    def test_unsupported_model_types_say_so_instead_of_inventing_importance(self):
        for algo in ("knn", "hist_gradient_boosting", "naive_bayes"):
            rep = run(self.df, self.types, target="bought", algorithms=[algo]).report
            native = rep["importance"]["native"]
            self.assertEqual(rep["best"]["algorithm"], algo)
            self.assertFalse(native["available"], algo)
            self.assertTrue(native["reason"], algo)
            self.assertNotIn("features", native)
            # the model-agnostic method still works for them
            self.assertTrue(rep["importance"]["permutation"]["available"], algo)

    def test_shap_is_not_claimed(self):
        self.assertFalse(self.rep["importance"]["shap"]["available"])
        self.assertIn("not part of this build", self.rep["importance"]["shap"]["reason"])

    def test_partial_dependence_covers_numeric_and_categorical_inputs_and_tracks_the_true_direction(self):
        pdp = {p["feature"]: p for p in self.rep["importance"]["partial_dependence"] if p.get("available")}
        self.assertIn("x1", pdp)
        ys = pdp["x1"]["series"][next(iter(pdp["x1"]["series"]))]
        self.assertGreater(ys[-1], ys[0] + 0.2)                  # x1 truly raises the chance of 'bought'
        if "x2" in pdp:
            ys2 = pdp["x2"]["series"][next(iter(pdp["x2"]["series"]))]
            self.assertLess(ys2[-1], ys2[0])                      # x2 truly lowers it
        plan = pdp.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["kind"], "categorical")
        self.assertIn("describes the model, not a cause", plan["note"])

    def test_learning_curve_reports_a_verdict_and_points(self):
        lc = self.rep["learning_curve"]
        self.assertTrue(lc["available"])
        self.assertIn(lc["verdict"], ("converging", "plateau", "more_data_may_help", "memorising"))
        sizes = [p["train_rows"] for p in lc["points"]]
        self.assertEqual(sizes, sorted(sizes))

    def test_importance_text_never_claims_causation(self):
        text = (self.rep["importance"]["note"] + self.rep["importance"]["permutation"]["note"]).lower()
        self.assertNotIn("causes the outcome.", text.replace("does not show that a column causes the outcome.", ""))


class OverfittingDetection(unittest.TestCase):
    def test_a_memorising_model_on_pure_noise_is_flagged_far_more_than_a_constrained_one(self):
        r = np.random.default_rng(3)
        n = 300
        raw = pd.DataFrame({"a": r.normal(0, 1, n).round(4), "b": r.normal(0, 1, n).round(4), "c": r.normal(0, 1, n).round(4), "y": r.choice(["p", "q"], n)})
        df, types = S.typed(raw)
        cfg = parse_config({"target": "y", "algorithms": ["random_forest", "logistic_regression"], "time_budget_s": 60}, types)
        res = train.run_automl(df, types, cfg, extended=False, rep=train.Reporter(None, None, 60), dataset={})
        over = {c["algorithm"]: c["overfitting"] for c in res.report["candidates"] if c["status"] == "ok"}
        self.assertEqual(over["random_forest"]["level"], "high")
        self.assertNotEqual(over["logistic_regression"]["level"], "high")
        self.assertGreater(over["random_forest"]["gap"], over["logistic_regression"]["gap"] + 0.1)
        self.assertTrue(any("does not beat" in w for w in res.report["warnings"]) or res.report["best"]["beats_baseline_by"] < 0.1)


class SuitabilityGuards(unittest.TestCase):
    def test_too_few_rows_are_refused_with_a_clear_message(self):
        df, types = S.typed(S.classification_frame(500).head(20))
        with self.assertRaises((AppError, MlError)) as ctx:
            run(df, types, target="bought")
        self.assertIn(getattr(ctx.exception, "code", ""), ("TOO_FEW_ROWS", "DATA_UNSUITABLE", "NOT_ENOUGH_ROWS"))

    def test_a_class_with_too_few_examples_is_an_error_unless_the_person_chooses_to_drop_it(self):
        raw = S.multiclass_frame(300)
        raw.loc[raw.index[:2], "grade"] = "rare"
        df, types = S.typed(raw)
        with self.assertRaises((AppError, MlError)):
            run(df, types, target="grade", algorithms=["logistic_regression"])
        rep = run(df, types, target="grade", algorithms=["logistic_regression"], rare_classes="drop").report
        self.assertEqual(rep["data"]["rare_class_rows_dropped"], 2)

    def test_invalid_configurations_are_rejected_with_field_level_errors(self):
        df, types = S.typed(S.classification_frame(100))
        bad = [{"target": "nope"}, {"target": "bought", "algorithms": ["quantum_forest"]}, {"target": "bought", "validation": {"test_size": 0.9}},
               {"target": "bought", "validation": {"cv_folds": 1}}, {"target": "bought", "unknown_setting": 1}, {"target": "bought", "problem_type": "regression"}]
        for raw in bad:
            with self.assertRaises((AppError, MlError)) as ctx:
                run(df, types, **{k: v for k, v in raw.items()})
            if isinstance(ctx.exception, AppError):
                self.assertEqual(ctx.exception.status_code, 422, raw)
            else:
                self.assertTrue(ctx.exception.code and ctx.exception.message, raw)

    def test_algorithm_that_does_not_fit_the_problem_is_rejected_with_the_reason(self):
        df, types = S.typed(S.classification_frame(100))
        with self.assertRaises(MlError) as ctx:
            run(df, types, target="bought", algorithms=["ridge"])
        self.assertEqual(ctx.exception.code, "NO_ALGORITHMS")
        self.assertIn("is for regression", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
