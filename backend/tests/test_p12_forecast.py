"""Forecasting: method maths on known series, the backtest, intervals, and the service's refusals."""
import unittest

import numpy as np
import pandas as pd

import analytics_support as sup
from app.modules.predictive import forecast as F
from p7_support import P7Base, code_of
from test_workspace_uploads import upload


def seasonal(n=72, noise=0.0, seed=0):
    t = np.arange(n)
    return 100 + 2 * t + 10 * np.sin(2 * np.pi * t / 12) + np.random.default_rng(seed).normal(0, noise, n)


class Methods(unittest.TestCase):
    def test_simple_methods_on_known_values(self):
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(list(F.predict("naive", y, 2, 1)), [6.0, 6.0])
        self.assertEqual(list(F.predict("drift", y, 2, 1)), [7.0, 8.0])
        self.assertEqual(list(F.predict("seasonal_naive", np.array([1.0, 2, 3, 1, 2, 3]), 4, 3)), [1.0, 2.0, 3.0, 1.0])

    def test_the_backtest_prefers_the_method_that_fits_the_pattern(self):
        y = seasonal()
        bt = F.backtest(y, 6, 12, F.available(len(y), 12))
        self.assertTrue(bt["scored"])
        self.assertEqual(F.pick(bt), "seasonal_trend")
        self.assertLess(bt["methods"]["seasonal_trend"]["mase"], bt["methods"]["naive"]["mase"])

    def test_a_short_series_is_not_backtested(self):
        y = seasonal(14)
        bt = F.backtest(y, 6, 1, F.available(len(y), 1))
        self.assertFalse(bt["scored"])
        self.assertIsNone(F.pick(bt))
        self.assertIn("at least 2 folds", bt["reason"])

    def test_seasonal_methods_need_two_cycles(self):
        self.assertNotIn("seasonal_trend", F.available(20, 12))
        self.assertIn("seasonal_trend", F.available(30, 12))

    def test_seasonality_is_detected_and_not_invented(self):
        self.assertTrue(F.seasonality(seasonal(72, 0.5), 12)["detected"])
        flat = F.seasonality(np.random.default_rng(3).normal(0, 1, 72), 12)
        self.assertFalse(flat["detected"])
        self.assertFalse(F.seasonality(np.arange(10.0), 12)["checked"])

    def test_gaps_are_filled_and_counted_but_too_many_are_refused(self):
        y, n = F.fill_gaps(np.array([1.0, np.nan, 3.0, 4.0, 5.0]))
        self.assertEqual((n, y[1]), (1, 2.0))
        with self.assertRaises(ValueError):
            F.fill_gaps(np.array([1.0, np.nan, np.nan, np.nan, 5.0]))

    def test_intervals_never_narrow_with_distance_and_report_their_coverage(self):
        y = seasonal(72, 2.0, 5)
        bt = F.backtest(y, 6, 12, F.available(len(y), 12))
        best = F.pick(bt)
        rows, info = F.intervals(F.predict(best, y, 6, 12), bt["errors"][best])
        self.assertTrue(info["available"])
        widths = [r["upper95"] - r["lower95"] for r in rows]
        self.assertTrue(all(b >= a - 1e-3 for a, b in zip(widths, widths[1:])))           # never narrower further out (rounding aside)
        for r in rows:
            self.assertLess(r["lower80"], r["point"]); self.assertLess(r["point"], r["upper80"]); self.assertLess(r["upper80"], r["upper95"])
        self.assertIn("backtest_coverage", info)
        none, why = F.intervals(np.array([1.0, 2.0]), None)
        self.assertFalse(why["available"]); self.assertNotIn("lower80", none[0])


class Service(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        months = pd.period_range("2019-01", periods=72, freq="M")
        df = pd.DataFrame({"when": [p.start_time.strftime("%Y-%m-%d") for p in months], "sales": seasonal(72, 1.0, 2).round(3), "label": ["a"] * 72})
        cls.sales = upload(cls.ws, cls.alice, cls.project, "sales.csv", sup.csv_of(df))["asset"]
        short = df.head(8)
        cls.short = upload(cls.ws, cls.alice, cls.project, "short.csv", sup.csv_of(short))["asset"]

    def test_monthly_forecast_has_backtest_intervals_and_limits(self):
        r = self.p7.forecast.run(self.alice, self.sales["id"], "when", "sales", freq="month", horizon=6)
        self.assertEqual(r["frequency"], "month")
        self.assertEqual(r["method"]["key"], "seasonal_trend")
        self.assertTrue(r["method"]["validated_by_backtest"])
        self.assertEqual(len(r["forecast"]), 6)
        self.assertEqual(r["forecast"][0]["period"], "2025-01")
        self.assertTrue(r["intervals"]["available"])
        self.assertTrue(r["seasonality"]["detected"])
        self.assertTrue(any("does not know about events" in s for s in r["limitations"]))
        truth = 100 + 2 * 72 + 10 * np.sin(2 * np.pi * 72 / 12)
        self.assertAlmostEqual(r["forecast"][0]["point"], truth, delta=4)

    def test_forecast_is_reproducible(self):
        a = self.p7.forecast.run(self.alice, self.sales["id"], "when", "sales", freq="month", horizon=4)
        b = self.p7.forecast.run(self.alice, self.sales["id"], "when", "sales", freq="month", horizon=4)
        self.assertEqual(a["forecast"], b["forecast"])

    def test_row_counts_can_be_forecast_without_a_measure(self):
        r = self.p7.forecast.run(self.alice, self.sales["id"], "when", None, agg="count", freq="month", horizon=3)
        self.assertEqual(len(r["forecast"]), 3)

    def test_too_short_series_is_refused(self):
        self.assertEqual(code_of(self.p7.forecast.run, self.alice, self.short["id"], "when", "sales", freq="month")[0], "SERIES_TOO_SHORT")

    def test_bad_requests_are_refused(self):
        run = self.p7.forecast.run
        self.assertEqual(code_of(run, self.alice, self.sales["id"], "label", "sales")[1], 422)
        self.assertEqual(code_of(run, self.alice, self.sales["id"], "when", "label")[1], 422)
        self.assertEqual(code_of(run, self.alice, self.sales["id"], "when", "sales", horizon=99)[1], 422)
        self.assertEqual(code_of(run, self.alice, self.sales["id"], "when", "sales", agg="evil")[1], 422)

    def test_another_user_gets_a_404(self):
        self.assertEqual(code_of(self.p7.forecast.run, self.bob, self.sales["id"], "when", "sales")[1], 404)


if __name__ == "__main__":
    unittest.main()
