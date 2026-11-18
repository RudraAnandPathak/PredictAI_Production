"""Dataset preparation for modelling: problem detection, configuration validation, column decisions, leakage, imbalance, pipeline behaviour."""
import copy
import unittest
import warnings

import numpy as np
import pandas as pd

import analytics_support as sup
import ml_support as ml
from app.core.errors import AppError
from app.modules.predictive import catalog, pipeline as pl, prep, problem as prob
from app.modules.predictive.config import DEFAULT_CONFIG, parse_config
from app.modules.predictive.constants import Problem


def assess(df, target, dates=(), extended=False, **raw):
    frame, types = ml.typed(df, dates)
    cfg = parse_config({"target": target, **raw}, types)
    return prep.assess(frame, types, cfg, extended=extended, seed=cfg["seed"]), frame, types, cfg


def by_name(report):
    return {c["name"]: c for c in report["columns"]}


class ProblemDetectionTests(unittest.TestCase):
    def detect(self, df, col, requested="auto"):
        frame, types = ml.typed(df)
        return prob.detect(frame[col], types[col], requested)

    def test_two_text_values_is_binary(self):
        d = self.detect(ml.classification_frame(), "bought")
        self.assertEqual(d["chosen"], Problem.BINARY)
        self.assertIn("two", d["why"])

    def test_three_text_values_is_multiclass(self):
        self.assertEqual(self.detect(ml.multiclass_frame(), "grade")["chosen"], Problem.MULTICLASS)

    def test_continuous_numeric_is_regression(self):
        self.assertEqual(self.detect(ml.regression_frame(), "price")["chosen"], Problem.REGRESSION)

    def test_zero_one_numbers_are_binary(self):
        self.assertEqual(self.detect(sup.synth(), "churned")["chosen"], Problem.BINARY)

    def test_small_set_of_whole_numbers_is_multiclass_with_regression_alternative(self):
        df = pd.DataFrame({"x": np.arange(300) % 7, "y": np.arange(300) % 4})
        d = self.detect(df, "y")
        self.assertEqual(d["recommended"], Problem.MULTICLASS)
        self.assertEqual(d["alternatives"][0]["problem"], Problem.REGRESSION)

    def test_override_to_regression_on_text_is_refused_with_reason(self):
        d = self.detect(ml.multiclass_frame(), "grade", Problem.REGRESSION)
        self.assertFalse(d["valid"])
        self.assertIn("text", d["errors"][0]["message"])

    def test_binary_requested_for_three_classes_is_refused(self):
        d = self.detect(ml.multiclass_frame(), "grade", Problem.BINARY)
        self.assertFalse(d["valid"])
        self.assertIn("exactly two", d["errors"][0]["message"])

    def test_classification_of_a_continuous_target_is_refused(self):
        d = self.detect(ml.regression_frame(), "price", Problem.MULTICLASS)
        self.assertFalse(d["valid"])
        self.assertIn("continuous", d["errors"][0]["message"])

    def test_override_that_differs_from_recommendation_is_valid_but_warned(self):
        df = pd.DataFrame({"y": np.arange(300) % 4})
        d = self.detect(df, "y", Problem.REGRESSION)
        self.assertTrue(d["valid"])
        self.assertTrue(d["warnings"])

    def test_constant_target_and_too_few_rows_and_date_target(self):
        self.assertEqual(self.detect(pd.DataFrame({"y": ["a"] * 50}), "y")["errors"][0]["code"], "TARGET_CONSTANT")
        self.assertEqual(self.detect(pd.DataFrame({"y": ["a", "b"] * 5}), "y")["errors"][0]["code"], "TOO_FEW_ROWS")
        frame, types = ml.typed(sup.synth(), dates=("signup",))
        self.assertEqual(prob.detect(frame["signup"], types["signup"])["errors"][0]["code"], "TARGET_UNSUPPORTED")

    def test_too_many_text_classes(self):
        df = pd.DataFrame({"y": [f"c{i}" for i in range(200)]})
        self.assertEqual(self.detect(df, "y")["errors"][0]["code"], "TOO_MANY_CLASSES")

    def test_require_valid_raises_a_422_with_details(self):
        d = self.detect(ml.multiclass_frame(), "grade", Problem.REGRESSION)
        with self.assertRaises(AppError) as cm:
            prob.require_valid(d)
        self.assertEqual(cm.exception.status_code, 422)

    def test_float_and_int_labels_are_one_class(self):
        s = pd.Series([1.0, 2.0, 1.0, 2.0] * 20)
        self.assertEqual(sorted(prob.class_labels(s).unique()), ["1", "2"])


class ImbalanceAndMetricTests(unittest.TestCase):
    def table(self, *counts):
        return [{"label": str(i), "count": c} for i, c in enumerate(counts)]

    def test_levels(self):
        self.assertEqual(prob.imbalance(self.table(50, 50))["level"], "balanced")
        self.assertEqual(prob.imbalance(self.table(65, 35))["level"], "balanced")
        self.assertEqual(prob.imbalance(self.table(75, 25))["level"], "mild")
        self.assertEqual(prob.imbalance(self.table(90, 10))["level"], "moderate")
        self.assertEqual(prob.imbalance(self.table(990, 10))["level"], "severe")

    def test_severe_imbalance_ranks_by_pr_auc_and_flags_accuracy(self):
        plan = prob.metric_plan(Problem.BINARY, "auto", balance=prob.imbalance(self.table(990, 10)))
        self.assertEqual(plan["primary"], "average_precision")
        self.assertTrue(any("Accuracy" in r for r in plan["reasons"]))

    def test_balanced_binary_uses_roc_auc_not_accuracy(self):
        self.assertEqual(prob.metric_plan(Problem.BINARY, "auto", balance=prob.imbalance(self.table(50, 50)))["primary"], "roc_auc")

    def test_multiclass_primary_is_macro_f1_and_pr_auc_is_not_computed(self):
        plan = prob.metric_plan(Problem.MULTICLASS, "auto", balance=prob.imbalance(self.table(30, 30, 30)))
        self.assertEqual(plan["primary"], "f1_macro")
        self.assertEqual(plan["not_computed"][0]["metric"], "PR-AUC")

    def test_regression_mape_skipped_when_target_has_zeros(self):
        plan = prob.metric_plan(Problem.REGRESSION, "auto", target={"zeros": 3, "skewness": 0.1, "outlier_share": 0.0})
        self.assertIn("MAPE", [m["metric"] for m in plan["not_computed"]])
        self.assertNotIn("MAPE", plan["computed"])
        plan = prob.metric_plan(Problem.REGRESSION, "auto", target={"zeros": 0, "skewness": 0.1, "outlier_share": 0.0})
        self.assertIn("MAPE", plan["computed"])

    def test_skewed_regression_target_is_ranked_by_mae(self):
        self.assertEqual(prob.metric_plan(Problem.REGRESSION, "auto", target={"zeros": 0, "skewness": 3.0, "outlier_share": 0.1})["primary"], "neg_mean_absolute_error")

    def test_invalid_metric_for_problem_is_refused(self):
        with self.assertRaises(AppError):
            prob.metric_plan(Problem.REGRESSION, "roc_auc")
        with self.assertRaises(AppError):
            prob.metric_plan(Problem.MULTICLASS, "average_precision")
        with self.assertRaises(AppError):
            prob.metric_plan(Problem.BINARY, "r2")
        self.assertEqual(prob.metric_plan(Problem.BINARY, "f1")["chosen_by"], "you")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.types = ml.typed(ml.classification_frame())

    def parse(self, **raw):
        return parse_config(raw, self.types)

    def bad(self, field, **raw):
        with self.assertRaises(AppError) as cm:
            parse_config(raw, self.types)
        self.assertEqual(cm.exception.status_code, 422)
        self.assertTrue(any(field in str(d) for d in (cm.exception.details or [])) or field in cm.exception.message, (field, cm.exception.details))

    def test_defaults_are_complete_and_not_shared_between_calls(self):
        a = self.parse(target="bought")
        a["preprocessing"]["scaling"] = "none"
        b = self.parse(target="bought")
        self.assertEqual(b["preprocessing"]["scaling"], "auto")
        self.assertEqual(set(b), set(DEFAULT_CONFIG))

    def test_unknown_keys_and_missing_or_wrong_target_are_refused(self):
        self.bad("target")
        self.bad("target", target="nope")
        self.bad("surprise", target="bought", surprise=1)
        self.bad("preprocessing.warp", target="bought", preprocessing={"warp": 1})

    def test_target_cannot_be_an_input(self):
        self.bad("features.include", target="bought", features={"include": ["bought", "x1"]})

    def test_numeric_ranges_and_enums(self):
        self.bad("validation.test_size", target="bought", validation={"test_size": 0.9})
        self.bad("validation.cv_folds", target="bought", validation={"cv_folds": 1})
        self.bad("validation.cv_folds", target="bought", validation={"cv_folds": 50})
        self.bad("preprocessing.scaling", target="bought", preprocessing={"scaling": "magic"})
        self.bad("seed", target="bought", seed=-1)
        self.bad("seed", target="bought", seed=True)
        self.bad("hpo.method", target="bought", hpo={"method": "bayes"})
        self.bad("primary_metric", target="bought", primary_metric="vibes")

    def test_limits_depend_on_entitlement(self):
        self.bad("validation.cv_folds", target="bought", validation={"cv_folds": 8})
        cfg = parse_config({"target": "bought", "validation": {"cv_folds": 8}}, self.types, extended=True)
        self.assertEqual(cfg["validation"]["cv_folds"], 8)

    def test_shuffle_with_time_series_validation_is_refused(self):
        self.bad("validation.shuffle", target="bought", validation={"cv_strategy": "time_series", "shuffle": True})

    def test_unknown_algorithm_and_grid_validation(self):
        self.bad("algorithms", target="bought", algorithms=["deep_magic"])
        self.bad("hpo.param_grids.random_forest.max_depth", target="bought", hpo={"param_grids": {"random_forest": {"max_depth": [0]}}})
        self.bad("hpo.param_grids.random_forest.bogus", target="bought", hpo={"param_grids": {"random_forest": {"bogus": [1]}}})
        self.bad("hpo.param_grids.naive_bayes", target="bought", hpo={"param_grids": {"naive_bayes": {}}})
        self.bad("hpo.param_grids.linear_regression", target="bought", hpo={"param_grids": {"linear_regression": {"x": [1]}}})
        ok = self.parse(target="bought", hpo={"method": "grid", "param_grids": {"random_forest": {"max_depth": [3, None, 8]}}})
        self.assertEqual(ok["hpo"]["param_grids"]["random_forest"]["max_depth"], [3, None, 8])

    def test_grid_values_must_be_bounded_and_not_code(self):
        self.bad("hpo.param_grids", target="bought", hpo={"param_grids": {"random_forest": {"n_estimators": [10 ** 9]}}})
        self.bad("hpo.param_grids", target="bought", hpo={"param_grids": {"random_forest": {"n_estimators": ["__import__('os')"]}}})
        self.bad("hpo.param_grids", target="bought", hpo={"param_grids": {"random_forest": {"max_depth": list(range(1, 30))}}})

    def test_column_overrides_are_validated(self):
        self.bad("features.overrides", target="bought", features={"overrides": {"ghost": {"role": "drop"}}})
        self.bad("features.overrides", target="bought", features={"overrides": {"x1": {"role": "banana"}}})
        self.bad("features.overrides", target="bought", features={"overrides": {"x1": {"magic": 1}}})
        self.bad("features.exclude", target="bought", features={"exclude": ["ghost"]})

    def test_time_column_must_be_date_or_numeric(self):
        self.bad("validation.time_column", target="bought", validation={"time_column": "plan"})
        self.assertEqual(self.parse(target="bought", validation={"time_column": "x1"})["validation"]["time_column"], "x1")


class CatalogTests(unittest.TestCase):
    def test_classification_and_regression_lists_match_the_specification(self):
        cls, _ = catalog.select("auto", Problem.BINARY, 1000, 10)
        reg, _ = catalog.select("auto", Problem.REGRESSION, 1000, 10)
        self.assertEqual(set(cls), {"logistic_regression", "decision_tree", "random_forest", "extra_trees", "gradient_boosting", "hist_gradient_boosting", "svm", "knn", "naive_bayes"})
        self.assertEqual(set(reg), {"linear_regression", "ridge", "lasso", "decision_tree", "random_forest", "extra_trees", "gradient_boosting", "hist_gradient_boosting", "svm", "knn"})

    def test_cheapest_first_so_a_time_limit_keeps_the_baselines(self):
        chosen, _ = catalog.select("auto", Problem.BINARY, 1000, 10)
        costs = [catalog.CATALOG[k].cost for k in chosen]
        self.assertEqual(costs, sorted(costs))

    def test_svm_and_gradient_boosting_are_skipped_on_big_tables_with_a_reason(self):
        chosen, skipped = catalog.select("auto", Problem.BINARY, 50_000, 100)
        self.assertNotIn("svm", chosen)
        self.assertNotIn("gradient_boosting", chosen)
        reasons = {s["algorithm"]: s["reason"] for s in skipped}
        self.assertIn("needless computing", reasons["svm"])
        self.assertIn("hist_gradient_boosting", chosen)

    def test_incompatible_requested_algorithm_is_reported(self):
        chosen, skipped = catalog.select(["naive_bayes", "ridge"], Problem.BINARY, 500, 5)
        self.assertEqual(chosen, ["naive_bayes"])
        self.assertEqual(skipped[0]["algorithm"], "ridge")

    def test_every_catalog_entry_builds_for_its_kinds(self):
        for key, algo in catalog.CATALOG.items():
            for kind in algo.kinds:
                self.assertIsNotNone(algo.build(kind, 1, None), key)


class AssessmentTests(unittest.TestCase):
    def test_clean_dataset_is_ready_and_reports_decisions_per_column(self):
        r, *_ = assess(ml.classification_frame(), "bought")
        cols = by_name(r)
        self.assertEqual(r["problem"]["chosen"], Problem.BINARY)
        self.assertEqual(cols["row_id"]["decision"]["action"], "drop")
        self.assertIn("identifier", cols["row_id"]["decision"]["reason"])
        self.assertEqual(cols["flat"]["decision"]["action"], "drop")
        self.assertIn("Constant", cols["flat"]["decision"]["reason"])
        self.assertEqual(cols["plan"]["decision"]["encoding"], "onehot")
        self.assertEqual(cols["x1"]["decision"]["action"], "use")
        self.assertEqual(r["suitability"]["verdict"], "ready")
        self.assertTrue(r["algorithms"]["selected"])
        self.assertEqual(r["schema"], "predictai.ml-assessment/1")

    def test_original_frame_is_not_modified(self):
        df = ml.classification_frame()
        frame, types = ml.typed(df)
        before = frame.copy(deep=True)
        cfg = parse_config({"target": "bought"}, types)
        prep.assess(frame, types, cfg, extended=False, seed=1)
        pd.testing.assert_frame_equal(frame, before)

    def test_assessment_is_reproducible(self):
        a, *_ = assess(ml.classification_frame(), "bought", seed=5)
        b, *_ = assess(ml.classification_frame(), "bought", seed=5)
        self.assertEqual(a, b)

    def test_missing_values_are_measured_and_handled(self):
        df = ml.classification_frame()
        df.loc[df.sample(150, random_state=1).index, "x1"] = np.nan
        df.loc[df.sample(500, random_state=2).index, "x2"] = np.nan      # 83% missing: dropped
        r, *_ = assess(df, "bought")
        cols = by_name(r)
        self.assertEqual(cols["x1"]["missing"], 150)
        self.assertEqual(cols["x1"]["decision"]["action"], "use")
        self.assertEqual(cols["x1"]["decision"]["impute"], "median")
        self.assertEqual(cols["x2"]["decision"]["action"], "drop")
        self.assertIn("Mostly missing", cols["x2"]["decision"]["reason"])

    def test_missing_threshold_is_configurable(self):
        df = ml.classification_frame()
        df.loc[df.sample(500, random_state=2).index, "x2"] = np.nan
        r, *_ = assess(df, "bought", preprocessing={"max_missing_pct": 95})
        self.assertEqual(by_name(r)["x2"]["decision"]["action"], "use")

    def test_near_constant_column_is_dropped(self):
        df = ml.classification_frame()
        df["almost"] = "a"
        df.loc[[0, 1], "almost"] = "b"
        r, *_ = assess(df, "bought")
        self.assertIn("Near-constant", by_name(r)["almost"]["decision"]["reason"])

    def test_free_text_and_high_cardinality_and_identifier_text(self):
        df = ml.classification_frame()
        n = len(df)
        df["note"] = [f"customer wrote a long free text comment number {i} with details" for i in range(n)]
        df["code"] = [f"C{i % 60:03d}" for i in range(n)]            # 60 categories
        df["uuid"] = [f"u{i:06d}" for i in range(n)]
        r, *_ = assess(df, "bought")
        cols = by_name(r)
        self.assertIn("Free text", cols["note"]["decision"]["reason"])
        self.assertEqual(cols["code"]["decision"]["action"], "use")
        self.assertEqual(cols["code"]["decision"]["encoding"], "frequency")
        self.assertIn("High cardinality", cols["code"]["decision"]["reason"])
        self.assertIn("identifier", cols["uuid"]["decision"]["reason"])
        r2, *_ = assess(df, "bought", preprocessing={"high_cardinality": "drop"})
        self.assertEqual(by_name(r2)["code"]["decision"]["action"], "drop")
        r3, *_ = assess(df, "bought", preprocessing={"high_cardinality": "ordinal"})
        self.assertEqual(by_name(r3)["code"]["decision"]["encoding"], "ordinal")

    def test_date_column_becomes_calendar_features_and_can_be_turned_off(self):
        r, *_ = assess(ml.regression_frame(), "price", dates=("when",))
        d = by_name(r)["when"]["decision"]
        self.assertEqual(d["role"], "datetime")
        self.assertIn("year", d["parts"])
        r2, *_ = assess(ml.regression_frame(), "price", dates=("when",), preprocessing={"datetime_features": False})
        self.assertEqual(by_name(r2)["when"]["decision"]["action"], "drop")

    def test_skewed_numeric_gets_log_only_in_auto_mode_and_never_when_negative(self):
        df = ml.regression_frame()
        df["amount"] = np.random.default_rng(1).lognormal(3, 1.4, len(df))
        df["signed"] = np.random.default_rng(2).standard_t(2, len(df))
        r, *_ = assess(df, "price", dates=("when",), preprocessing={"log_transform": "auto"})
        cols = by_name(r)
        self.assertEqual(cols["amount"]["decision"]["transform"], "log1p")
        self.assertEqual(cols["signed"]["decision"]["transform"], "none")
        r2, *_ = assess(df, "price", dates=("when",))
        self.assertEqual(by_name(r2)["amount"]["decision"]["transform"], "none")

    def test_outlier_share_is_reported(self):
        df = ml.classification_frame()
        df.loc[:20, "x1"] = 50.0
        r, *_ = assess(df, "bought")
        self.assertGreater(by_name(r)["x1"]["outlier_share"], 0.02)

    def test_duplicates_are_counted_and_explained(self):
        df = ml.classification_frame()
        df = pd.concat([df, df.iloc[:60]], ignore_index=True)
        df["row_id"] = np.arange(len(df))
        r, *_ = assess(df, "bought")
        self.assertEqual(r["duplicates"]["rows"], 60)
        self.assertTrue(any("repeat another row" in c for c in r["suitability"]["cautions"]))

    def test_class_imbalance_is_detected_and_class_weighting_proposed(self):
        df = ml.classification_frame()
        df["bought"] = np.where(np.arange(len(df)) % 25 == 0, "yes", "no")
        r, *_ = assess(df, "bought")
        self.assertIn(r["imbalance"]["level"], ("moderate", "severe"))
        self.assertEqual(r["metrics"]["primary"], "average_precision")
        self.assertEqual(r["proposed_config"]["class_weight"], "auto")
        self.assertEqual(r["proposed_config"]["positive_label"], "yes")
        self.assertEqual(r["suitability"]["verdict"], "caution")

    def test_rare_classes_block_until_the_person_chooses_to_drop_them(self):
        df = ml.multiclass_frame()
        df.loc[:1, "grade"] = "odd"
        r, *_ = assess(df, "grade")
        self.assertEqual(r["suitability"]["verdict"], "not_suitable")
        self.assertTrue(any(i["code"] == "RARE_CLASSES" for i in r["issues"]))
        self.assertEqual(r["proposed_config"]["rare_classes"], "drop")
        r2, *_ = assess(df, "grade", rare_classes="drop")
        self.assertNotEqual(r2["suitability"]["verdict"], "not_suitable")

    def test_invalid_problem_override_makes_the_dataset_not_suitable_with_the_reason(self):
        r, *_ = assess(ml.multiclass_frame(), "grade", problem_type="regression")
        self.assertEqual(r["suitability"]["verdict"], "not_suitable")
        self.assertIn("text", r["suitability"]["blockers"][0])

    def test_no_usable_inputs_is_not_suitable(self):
        df = pd.DataFrame({"flat": ["z"] * 100, "y": ["a", "b"] * 50})
        r, *_ = assess(df, "y")
        self.assertEqual(r["suitability"]["verdict"], "not_suitable")
        self.assertTrue(any(i["code"] == "NO_FEATURES" for i in r["issues"]))

    def test_too_many_encoded_columns_is_refused_with_advice(self):
        df = ml.classification_frame()
        for i in range(30):
            df[f"cat{i}"] = np.random.default_rng(i).choice([f"v{j}" for j in range(19)], len(df))
        r, *_ = assess(df, "bought")
        self.assertGreater(r["preprocessing"]["encoded_features_estimate"], 400)
        self.assertTrue(any(i["code"] == "TOO_WIDE" for i in r["issues"]))
        r2, *_ = assess(df, "bought", extended=True)
        self.assertFalse(any(i["code"] == "TOO_WIDE" for i in r2["issues"]))

    def test_person_overrides_win_and_are_marked(self):
        r, *_ = assess(ml.classification_frame(), "bought", features={"overrides": {"row_id": {"role": "use"}, "x2": {"role": "drop"}, "plan": {"encoding": "ordinal"}}})
        cols = by_name(r)
        self.assertEqual(cols["row_id"]["decision"]["action"], "use")
        self.assertIn("overridden", cols["row_id"]["decision"]["notes"])
        self.assertEqual(cols["x2"]["decision"]["action"], "drop")
        self.assertEqual(cols["plan"]["decision"]["encoding"], "ordinal")

    def test_include_exclude_lists(self):
        r, *_ = assess(ml.classification_frame(), "bought", features={"include": ["x1", "plan"]})
        used = {n for n, c in by_name(r).items() if c["decision"]["action"] == "use"}
        self.assertEqual(used, {"x1", "plan"})
        r, *_ = assess(ml.classification_frame(), "bought", features={"exclude": ["x1"]})
        self.assertEqual(by_name(r)["x1"]["decision"]["action"], "drop")

    def test_role_overrides_that_cannot_work_are_refused(self):
        df = ml.classification_frame()
        frame, types = ml.typed(df)
        from app.modules.predictive.errors import MlError
        with self.assertRaises(MlError) as cm:
            prep.assess(frame, types, parse_config({"target": "bought", "features": {"overrides": {"plan": {"role": "numeric"}}}}, types), extended=False, seed=1)
        self.assertEqual(cm.exception.code, "COLUMN_ROLE_INVALID")

    def test_numeric_column_can_be_treated_as_categories(self):
        df = ml.classification_frame()
        df["level"] = np.random.default_rng(3).integers(1, 5, len(df))
        r, *_ = assess(df, "bought", features={"overrides": {"level": {"role": "categorical"}}})
        d = by_name(r)["level"]["decision"]
        self.assertEqual(d["role"], "categorical")
        self.assertTrue(d["numeric_labels"])


class LeakageTests(unittest.TestCase):
    def test_copy_of_target_and_derived_column_are_excluded_by_default(self):
        df = ml.classification_frame()
        df["bought_flag"] = np.where(df["bought"] == "yes", 1, 0)
        df["amount_paid"] = np.where(df["bought"] == "yes", 100.0, 0.0) + np.random.default_rng(1).normal(0, 0.01, len(df))
        r, *_ = assess(df, "bought")
        cols = by_name(r)
        self.assertEqual(cols["bought_flag"]["decision"]["action"], "drop")
        self.assertEqual(cols["amount_paid"]["decision"]["action"], "drop")
        self.assertIn("leakage", cols["amount_paid"]["decision"]["reason"].lower())
        self.assertEqual({l["column"] for l in r["leakage"] if l["severity"] == "high"}, {"bought_flag", "amount_paid"})
        self.assertTrue(any("answer" in c for c in r["suitability"]["cautions"]))

    def test_person_can_keep_a_flagged_column(self):
        df = ml.classification_frame()
        df["amount_paid"] = np.where(df["bought"] == "yes", 100.0, 0.0) + np.random.default_rng(1).normal(0, 0.01, len(df))
        r, *_ = assess(df, "bought", features={"overrides": {"amount_paid": {"role": "use"}}})
        self.assertEqual(by_name(r)["amount_paid"]["decision"]["action"], "use")

    def test_name_similarity_is_a_warning_only(self):
        df = ml.classification_frame()
        df["bought_last_year"] = np.random.default_rng(4).normal(0, 1, len(df))
        r, *_ = assess(df, "bought")
        lk = [l for l in r["leakage"] if l["column"] == "bought_last_year"][0]
        self.assertEqual(lk["severity"], "warning")
        self.assertEqual(by_name(r)["bought_last_year"]["decision"]["action"], "use")

    def test_honest_features_are_not_flagged(self):
        r, *_ = assess(ml.classification_frame(), "bought")
        self.assertEqual(r["leakage"], [])

    def test_regression_leakage(self):
        df = ml.regression_frame()
        df["price_eur"] = df["price"] * 0.92
        r, *_ = assess(df, "price", dates=("when",))
        self.assertEqual(by_name(r)["price_eur"]["decision"]["action"], "drop")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.df = ml.classification_frame()
        self.frame, self.types = ml.typed(self.df)
        self.cfg = parse_config({"target": "bought"}, self.types)
        self.used, self.dropped, _ = prep.resolve_features(self.frame, self.types, self.cfg, "bought")
        self.X = self.frame[[u["name"] for u in self.used]]
        self.y = (self.frame["bought"] == "yes").astype(int).to_numpy()

    def fit(self, algo, X=None, y=None, **kw):
        pipe, meta = pl.build_pipeline(self.used, algo, Problem.BINARY, 3, **kw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pipe.fit(self.X if X is None else X, self.y if y is None else y)
        return pipe, meta

    def test_every_binary_algorithm_fits_and_predicts_probabilities(self):
        for algo in catalog.select("auto", Problem.BINARY, 600, 10)[0]:
            pipe, _ = self.fit(algo)
            p = pipe.predict_proba(self.X.head(5))
            self.assertEqual(p.shape, (5, 2), algo)
            np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-6)

    def test_statistics_come_from_training_rows_only(self):
        train = self.X.iloc[:300].copy()
        train.loc[train.index[:10], "x1"] = np.nan
        pipe, _ = self.fit("logistic_regression", X=train, y=self.y[:300])
        scaler = pipe.named_steps["prep"].named_transformers_["num0"].named_steps["scale"]
        imputer = pipe.named_steps["prep"].named_transformers_["num0"].named_steps["impute"]
        cols = pipe.named_steps["prep"].transformers_[0][2]
        self.assertAlmostEqual(imputer.statistics_[cols.index("x1")], float(np.nanmedian(train["x1"])), places=9)
        self.assertAlmostEqual(scaler.mean_[cols.index("x2")], float(train["x2"].mean()), places=9)
        # changing rows that were not in training must not change anything learned
        other = self.X.iloc[300:].copy()
        other["x1"] = other["x1"] * 1000
        before = scaler.mean_.copy()
        pipe.predict_proba(other)
        np.testing.assert_array_equal(scaler.mean_, before)

    def test_scaling_follows_the_algorithm_when_auto(self):
        lin, _ = self.fit("logistic_regression")
        tree, _ = self.fit("random_forest")
        self.assertIn("scale", lin.named_steps["prep"].named_transformers_["num0"].named_steps)
        self.assertNotIn("scale", tree.named_steps["prep"].named_transformers_["num0"].named_steps)

    def test_explicit_scaler_is_applied_to_any_algorithm(self):
        used = copy.deepcopy(self.used)
        for u in used:
            if u["role"] == "numeric":
                u["scale"] = "robust"
        pipe, _ = pl.build_pipeline(used, "random_forest", Problem.BINARY, 1)
        pipe.fit(self.X, self.y)
        from sklearn.preprocessing import RobustScaler
        self.assertIsInstance(pipe.named_steps["prep"].named_transformers_["num0"].named_steps["scale"], RobustScaler)

    def test_feature_map_traces_every_encoded_column_to_an_original_column(self):
        pipe, meta = self.fit("random_forest")
        fm = pl.feature_map(pipe, meta)
        self.assertEqual(len(fm["names"]), len(fm["origins"]))
        self.assertEqual(len(fm["names"]), pipe.named_steps["model"].n_features_in_)
        self.assertEqual(set(fm["origins"]), {"x1", "x2", "plan", "noise"})
        self.assertEqual(sorted(n for n, o in zip(fm["names"], fm["origins"]) if o == "plan"), ["plan_basic", "plan_plus", "plan_pro"])

    def test_unseen_categories_junk_text_and_missing_inputs_do_not_break_prediction(self):
        pipe, _ = self.fit("random_forest")
        raw = pd.DataFrame([{"x1": "abc", "x2": "", "plan": "enterprise", "noise": None}, {"x1": "1.5", "x2": "0.2", "plan": " pro ", "noise": "0"}])
        p = pipe.predict_proba(raw)
        self.assertEqual(p.shape, (2, 2))
        self.assertTrue(np.isfinite(p).all())

    def test_missing_column_in_input_is_treated_as_missing_values(self):
        pipe, _ = self.fit("random_forest")
        p = pipe.predict_proba(pd.DataFrame([{"x1": 1.0, "x2": 0.0}]))
        self.assertTrue(np.isfinite(p).all())

    def test_fit_is_reproducible_with_the_same_seed(self):
        a, _ = self.fit("random_forest")
        b, _ = self.fit("random_forest")
        np.testing.assert_array_equal(a.predict_proba(self.X), b.predict_proba(self.X))

    def test_missing_indicator_outlier_clip_and_log_blocks(self):
        df = ml.classification_frame()
        df.loc[df.sample(60, random_state=3).index, "x1"] = np.nan
        df["amount"] = np.random.default_rng(1).lognormal(3, 1.4, len(df))
        frame, types = ml.typed(df)
        cfg = parse_config({"target": "bought", "preprocessing": {"missing_indicator": True, "outliers": "clip_iqr", "log_transform": "auto"}}, types)
        used, _, _ = prep.resolve_features(frame, types, cfg, "bought")
        pipe, meta = pl.build_pipeline(used, "logistic_regression", Problem.BINARY, 1)
        X = frame[[u["name"] for u in used]]
        pipe.fit(X, (frame["bought"] == "yes").astype(int))
        fm = pl.feature_map(pipe, meta)
        self.assertIn("x1__was_missing", fm["names"])
        Z = pl.transformed_matrix(pipe, X)
        self.assertTrue(np.isfinite(Z).all())
        fenced = pipe.named_steps["prep"].named_transformers_
        self.assertTrue(any("clip" in t.named_steps for t in fenced.values() if hasattr(t, "named_steps")))

    def test_date_features_are_extracted_and_traced(self):
        frame, types = ml.typed(ml.regression_frame(), dates=("when",))
        cfg = parse_config({"target": "price"}, types)
        used, _, _ = prep.resolve_features(frame, types, cfg, "price")
        pipe, meta = pl.build_pipeline(used, "random_forest", Problem.REGRESSION, 1)
        X = frame[[u["name"] for u in used]]
        pipe.fit(X, frame["price"])
        fm = pl.feature_map(pipe, meta)
        self.assertIn("when__year", fm["names"])
        self.assertEqual({o for n, o in zip(fm["names"], fm["origins"]) if n.startswith("when__")}, {"when"})
        self.assertEqual(pipe.predict(pd.DataFrame([{"x1": 5, "x2": 0, "cat": "u", "when": "2023-05-01"}])).shape, (1,))

    def test_frequency_and_ordinal_encoders_handle_unseen_values(self):
        df = ml.classification_frame()
        df["code"] = [f"C{i % 40:03d}" for i in range(len(df))]
        frame, types = ml.typed(df)
        for mode in ("frequency", "ordinal"):
            cfg = parse_config({"target": "bought", "preprocessing": {"high_cardinality": mode}}, types)
            used, _, _ = prep.resolve_features(frame, types, cfg, "bought")
            pipe, _ = pl.build_pipeline(used, "random_forest", Problem.BINARY, 1)
            X = frame[[u["name"] for u in used]]
            pipe.fit(X, (frame["bought"] == "yes").astype(int))
            self.assertEqual(pipe.predict_proba(pd.DataFrame([{**X.iloc[0].to_dict(), "code": "NEVER-SEEN"}])).shape, (1, 2))

    def test_class_weight_only_where_supported(self):
        self.assertEqual(pl.class_weight_for("auto", "random_forest", True), "balanced")
        self.assertIsNone(pl.class_weight_for("auto", "random_forest", False))
        self.assertIsNone(pl.class_weight_for("balanced", "naive_bayes", True))
        self.assertIsNone(pl.class_weight_for("none", "random_forest", True))
        self.assertEqual(pl.class_weight_for("balanced", "random_forest", False), "balanced")

    def test_hyperparameters_are_applied_including_wrapped_svm(self):
        pipe, _ = pl.build_pipeline(self.used, "random_forest", Problem.BINARY, 1, params={"max_depth": 3})
        self.assertEqual(pipe.named_steps["model"].max_depth, 3)
        pipe, _ = pl.build_pipeline(self.used, "svm", Problem.BINARY, 1, params={"C": 4.0})
        self.assertEqual(pipe.named_steps["model"].estimator.C, 4.0)


if __name__ == "__main__":
    unittest.main()
