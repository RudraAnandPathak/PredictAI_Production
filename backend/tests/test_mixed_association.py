"""Mixed-variable analysis: right method per pair of column types, correct numbers, assumption checks, missing data, refusals, integration."""
import math
import unittest

import numpy as np
import pandas as pd
from scipy import stats

import analytics_support as sup
from app.modules.analytics.ops.mixed import eta_with_ci, pair_measure
from test_analytics_service import ServiceBase


def run_mixed(df, x, y, **params):
    plan, out = sup.run_engine(df, [{"op": "mixed_association", "params": {"x": x, "y": y, **params}}], kind="relationships", dates=("signup",))
    return out["results"][0]


def eta_reference(x, g):
    d = pd.DataFrame({"x": x, "g": g}).dropna()
    grand = d["x"].mean()
    ssb = sum(len(v) * (v.mean() - grand) ** 2 for _, v in d.groupby("g")["x"])
    sst = ((d["x"] - grand) ** 2).sum()
    return math.sqrt(ssb / sst)


def cramers_reference(a, b):
    t = pd.crosstab(a, b).to_numpy().astype(float)
    n = t.sum()
    chi2 = stats.chi2_contingency(t, correction=False)[0]
    r, k = t.shape
    phi2 = max(0.0, chi2 / n - (r - 1) * (k - 1) / (n - 1))
    rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
    return math.sqrt(phi2 / min(rc - 1, kc - 1))


class PairTypeTests(unittest.TestCase):
    def setUp(self):
        self.df = sup.synth()

    def test_numeric_numeric_uses_a_correlation_with_the_right_value_and_interval(self):
        r = run_mixed(self.df, "age", "income")
        d = r["data"]
        self.assertEqual((r["status"], d["pair_type"]), ("ok", "numeric_numeric"))
        ok = self.df[["age", "income"]].dropna()
        self.assertEqual(d["n"], len(ok))
        rho = stats.spearmanr(ok["age"], ok["income"]).statistic
        pear = stats.pearsonr(ok["age"], ok["income"]).statistic
        self.assertIn(d["measure"]["name"], ("Spearman rho", "Pearson r"))
        expected = rho if d["measure"]["name"] == "Spearman rho" else pear
        self.assertAlmostEqual(d["measure"]["value"], expected, places=6)
        lo, hi = d["measure"]["ci"]
        self.assertLess(lo, d["measure"]["value"])
        self.assertGreater(hi, d["measure"]["value"])
        self.assertGreater(d["strength"], 0.3)
        self.assertEqual(d["direction"], "positive")
        self.assertAlmostEqual(d["detail"]["spearman_rho"], rho, places=6)
        self.assertAlmostEqual(d["detail"]["pearson_r"], pear, places=6)

    def test_category_numeric_reports_eta_and_the_group_test_the_data_allow(self):
        r = run_mixed(self.df, "region", "income")                          # the categorical column may come first
        d = r["data"]
        self.assertEqual(d["pair_type"], "categorical_numeric")
        ok = self.df[["region", "income"]].dropna()
        self.assertAlmostEqual(d["measure"]["value"], eta_reference(ok["income"], ok["region"]), places=6)
        self.assertEqual(d["measure"]["name"], "eta (correlation ratio)")
        self.assertIn(d["test"]["name"], ("Welch's ANOVA", "One-way ANOVA", "Kruskal-Wallis H test"))
        self.assertIsNotNone(d["test"]["p_value"])
        self.assertIn("group_test_effect_size", d["detail"])

    def test_the_numeric_first_order_gives_the_same_answer(self):
        a, b = run_mixed(self.df, "region", "spend")["data"], run_mixed(self.df, "spend", "region")["data"]
        self.assertEqual(a["measure"]["value"], b["measure"]["value"])
        self.assertEqual(a["test"]["p_value"], b["test"]["p_value"])

    def test_two_groups_use_a_two_sample_test(self):
        r = run_mixed(self.df, "churned", "age", x_as="categorical")
        self.assertEqual(r["data"]["pair_type"], "categorical_numeric")
        self.assertIn(r["data"]["test"]["name"], ("Welch's t-test", "Mann-Whitney U test"))

    def test_category_category_reports_cramers_v_matching_an_independent_formula(self):
        r = run_mixed(self.df, "region", "segment")
        d = r["data"]
        self.assertEqual(d["pair_type"], "categorical_categorical")
        self.assertAlmostEqual(d["measure"]["value"], cramers_reference(self.df["region"], self.df["segment"]), places=6)
        self.assertEqual(d["test"]["name"], "Chi-square test of independence")
        self.assertTrue(d["test"]["significant"])
        self.assertIsNotNone(d["measure"]["ci"])

    def test_a_numeric_column_can_be_treated_as_categories_on_request(self):
        r = run_mixed(self.df, "churned", "region", x_as="categorical")
        d = r["data"]
        self.assertEqual(d["pair_type"], "categorical_categorical")
        self.assertEqual((d["x_role"], d["y_role"]), ("categorical", "categorical"))
        self.assertAlmostEqual(d["measure"]["value"], cramers_reference(self.df["churned"].astype(str), self.df["region"]), places=2)

    def test_a_weak_relationship_is_reported_as_weak_not_significant_for_nothing(self):
        r = run_mixed(self.df, "region", "income")["data"]
        self.assertLess(r["strength"], 0.2)
        self.assertFalse(r["test"]["significant"])


class AssumptionsMissingDataAndUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.df = sup.synth()

    def test_assumption_checks_and_a_reason_for_the_chosen_method_are_present(self):
        for x, y in (("age", "income"), ("region", "income"), ("region", "segment")):
            d = run_mixed(self.df, x, y)["data"]
            self.assertTrue(d["assumption_checks"], (x, y))
            self.assertTrue(d["why"])
            self.assertTrue(d["selected_test"]["why"])
            self.assertTrue(all("status" in c and "detail" in c for c in d["assumption_checks"]))

    def test_missing_values_are_excluded_and_counted(self):
        d = run_mixed(self.df, "age", "income")["data"]
        self.assertEqual(d["missing_excluded"], int(self.df["income"].isna().sum()))
        self.assertEqual(d["n"] + d["missing_excluded"], len(self.df))

    def test_effect_sizes_come_with_an_interval_and_the_method_that_produced_it(self):
        for x, y in (("age", "income"), ("region", "income"), ("region", "segment")):
            m = run_mixed(self.df, x, y)["data"]["measure"]
            self.assertEqual(len(m["ci"]), 2, (x, y))
            self.assertTrue(m["ci_method"])
            self.assertEqual(m["confidence"], 0.95)

    def test_a_small_sample_is_flagged_and_a_tiny_one_is_not_analysed(self):
        small = self.df.head(25)
        d = run_mixed(small, "age", "spend")
        self.assertEqual(d["status"], "ok")
        self.assertTrue(d["data"]["small_sample"])
        tiny = run_mixed(self.df.head(6), "age", "spend")
        self.assertEqual(tiny["status"], "not_run")
        self.assertEqual(tiny["data"]["message"], "Not enough evidence.")

    def test_association_is_never_called_causation(self):
        r = run_mixed(self.df, "age", "income")
        self.assertIn("does not show that one variable causes the other", r["data"]["causation_note"])
        self.assertTrue(any("not causation" in s or "causation" in s for s in r["evidence"]["limitations"]))

    def test_evidence_is_complete_for_every_pair_type(self):
        for x, y in (("age", "income"), ("region", "income"), ("region", "segment")):
            ev = run_mixed(self.df, x, y)["evidence"]
            for key in ("columns", "sample_size", "null_policy", "method", "formula_ref", "uncertainty", "assumptions", "limitations"):
                self.assertTrue(ev.get(key) not in (None, "", []) or key == "assumptions", (x, y, key))

    def test_results_are_reproducible(self):
        a, b = run_mixed(self.df, "region", "income"), run_mixed(self.df, "region", "income")
        self.assertEqual(a["data"], b["data"])


class InvalidCombinationTests(unittest.TestCase):
    def setUp(self):
        self.df = sup.synth()

    def failure(self, x, y, **params):
        r = run_mixed(self.df, x, y, **params)
        self.assertEqual(r["status"], "failed", r)
        return r["error"]

    def test_a_date_column_is_refused_with_advice(self):
        err = self.failure("signup", "income")
        self.assertEqual(err["code"], "MIXED_INVALID")
        self.assertIn("date", err["message"])

    def test_the_same_column_twice_is_refused(self):
        self.assertEqual(self.failure("age", "age")["code"], "MIXED_INVALID")

    def test_text_cannot_be_forced_to_numeric(self):
        self.assertEqual(self.failure("region", "income", x_as="numeric")["code"], "MIXED_INVALID")

    def test_an_identifier_like_column_is_not_accepted_as_categories(self):
        err = self.failure("id", "income", x_as="categorical")
        self.assertEqual(err["code"], "TOO_MANY_CATEGORIES")

    def test_a_constant_column_is_refused(self):
        self.assertEqual(self.failure("const", "income")["code"], "CONSTANT_COLUMN")

    def test_unknown_columns_and_parameters_never_reach_the_engine(self):
        from app.core.errors import AppError
        with self.assertRaises(AppError):
            run_mixed(self.df, "nope", "income")
        with self.assertRaises(AppError):
            run_mixed(self.df, "age", "income", x_as="bogus")


class SharedMeasureTests(unittest.TestCase):
    """The predictive workflow screens features with `pair_measure`; it must agree with the operation."""

    def setUp(self):
        self.df = sup.synth()
        self.rng = np.random.default_rng(1)

    def test_categorical_pairs_agree_with_the_operation(self):
        d = run_mixed(self.df, "region", "segment")["data"]
        m = pair_measure(self.df["region"], self.df["segment"], "categorical", "categorical", self.rng)
        self.assertAlmostEqual(m["value"], d["measure"]["value"], places=6)

    def test_category_numeric_pairs_agree_with_the_operation(self):
        ok = self.df[["region", "spend"]].dropna()
        d = run_mixed(self.df, "region", "spend")["data"]
        m = pair_measure(ok["region"], ok["spend"], "categorical", "numeric", self.rng)
        self.assertAlmostEqual(m["value"], d["measure"]["value"], places=6)

    def test_numeric_pairs_report_spearman(self):
        ok = self.df[["age", "income"]].dropna()
        m = pair_measure(ok["age"], ok["income"], "numeric", "numeric", self.rng)
        self.assertAlmostEqual(m["value"], stats.spearmanr(ok["age"], ok["income"]).statistic, places=6)

    def test_too_little_data_gives_nothing(self):
        self.assertIsNone(pair_measure(self.df["age"].head(5), self.df["spend"].head(5), "numeric", "numeric", self.rng))

    def test_eta_interval_contains_the_estimate_and_is_deterministic_for_a_seed(self):
        ok = self.df[["region", "spend"]].dropna()
        a = eta_with_ci(ok["spend"].to_numpy(), ok["region"].to_numpy(), np.random.default_rng(3))
        b = eta_with_ci(ok["spend"].to_numpy(), ok["region"].to_numpy(), np.random.default_rng(3))
        self.assertEqual(a, b)
        self.assertLessEqual(a[1][0], a[0])
        self.assertGreaterEqual(a[1][1], a[0])


class IntegrationTests(ServiceBase):
    def test_runs_through_the_service_with_verified_evidence_and_an_explanation(self):
        run = self.run_ops([{"op": "mixed_association", "params": {"x": "region", "y": "segment"}}], kind="relationships")
        self.assertEqual(run["evidence_status"], "VERIFIED", run["result"]["evidence"]["missing"])
        res = run["result"]["results"][0]
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["explanation"]["title"], "Explanation")
        self.assertEqual(res["explanation"]["status"], "available")
        self.assertIn("Cramér's V", " ".join(res["explanation"]["points"]) + res["explanation"]["summary"] + " ".join(t["term"] for t in res["explanation"]["terms"]))

    def test_is_part_of_the_published_operation_vocabulary(self):
        names = [o["name"] for o in self.an.schema(self.alice, self.asset["id"])["operations"]]
        self.assertIn("mixed_association", names)

    def test_it_does_not_replace_or_duplicate_the_phase_4_operations(self):
        names = [o["name"] for o in self.an.schema(self.alice, self.asset["id"])["operations"]]
        for existing in ("hypothesis", "correlation", "group_compare", "contingency", "discover"):
            self.assertEqual(names.count(existing), 1)

    def test_other_users_cannot_run_it_on_this_dataset(self):
        from app.core.errors import AppError
        with self.assertRaises(AppError) as cm:
            self.an.run(self.bob, self.asset["id"], kind="relationships", operations=[{"op": "mixed_association", "params": {"x": "region", "y": "segment"}}])
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
