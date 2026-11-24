"""Model monitoring: drift maths on known data, stored checks, outcome-based performance, isolation and honest 'not enough data' states."""
import unittest

import numpy as np
import pandas as pd

import analytics_support as sup
import ml_support as S
from app.modules.monitoring import drift as D
from p7_support import P7Base, code_of
from test_workspace_uploads import upload

VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}


class Migration(unittest.TestCase):
    def test_migration_twelve_is_purely_additive_and_adds_only_empty_mon_tables(self):
        import sqlite3, tempfile
        from pathlib import Path
        from app.db.migrator import discover, migrate
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(f"{tmp}/db.sqlite", isolation_level=None)
            conn.execute("PRAGMA foreign_keys = ON")
            first = Path(tmp) / "m11"
            first.mkdir()
            for m in discover()[:11]:
                (first / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, first), list(range(1, 12)))
            conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','u1@x.io','u1@x.io','active','t','t')")

            def snap():
                names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' AND name NOT LIKE '%_fts%'"))
                return ({t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in names},
                        {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'")})
            rows0, schema0 = snap()
            second = Path(tmp) / "m12"
            second.mkdir()
            for m in discover()[:12]:
                (second / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
            self.assertEqual(migrate(conn, second), [12])                 # later migrations are tested by their own tests
            rows1, schema1 = snap()
            self.assertEqual(rows0, {k: v for k, v in rows1.items() if k in rows0})
            self.assertEqual(schema0, {k: v for k, v in schema1.items() if k in schema0})
            added = set(rows1) - set(rows0)
            self.assertEqual(added, {"mon_checks", "mon_outcomes"})
            self.assertTrue(all(rows1[t] == [] for t in added))
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(migrate(conn), [13, 14, 15, 16, 17])
            conn.close()


class DriftMaths(unittest.TestCase):
    def test_same_distribution_is_stable_and_a_shift_is_significant(self):
        rng = np.random.default_rng(1)
        a, b = pd.Series(rng.normal(0, 1, 2000)), pd.Series(rng.normal(0, 1, 2000))
        same = D.numeric(a, b)
        self.assertEqual(same["status"], "stable")
        self.assertLess(same["psi"], 0.1)
        moved = D.numeric(a, pd.Series(rng.normal(2, 1, 2000)))
        self.assertEqual(moved["status"], "significant")
        self.assertGreater(moved["psi"], 0.25)
        self.assertLess(moved["ks"]["p_value"], 0.001)

    def test_psi_matches_a_hand_computed_value(self):
        p, q = np.array([0.5, 0.5]), np.array([0.25, 0.75])
        self.assertAlmostEqual(D._psi(p, q), (0.25 - 0.5) * np.log(0.5) + (0.75 - 0.5) * np.log(1.5), places=9)

    def test_categorical_shift_names_unseen_categories(self):
        ref = pd.Series(["a"] * 60 + ["b"] * 40)
        cur = pd.Series(["a"] * 20 + ["b"] * 20 + ["c"] * 60)
        r = D.categorical(ref, cur)
        self.assertEqual(r["status"], "significant")
        self.assertEqual(r["unseen_categories"], ["c"])
        self.assertAlmostEqual(r["unseen_share"], 0.6, places=4)

    def test_too_little_data_is_reported_not_scored(self):
        r = D.numeric(pd.Series(range(10)), pd.Series(range(10)))
        self.assertEqual(r["status"], "insufficient_data")
        self.assertNotIn("psi", r)
        whole = D.compare(pd.DataFrame({"x": range(10)}), pd.DataFrame({"x": range(10)}), {"x": "numeric", "when": "datetime"})
        self.assertEqual(whole["status"], "insufficient_data")
        self.assertIn("when", whole["not_compared"])

    def test_constant_training_column_is_handled(self):
        r = D.numeric(pd.Series([1.0] * 100), pd.Series([1.0] * 50 + [2.0] * 50))
        self.assertEqual(r["status"], "significant")
        self.assertIsNone(r["psi"])

    def test_performance_never_judges_on_few_outcomes_or_without_a_baseline(self):
        few = D.performance("classification", ["a"] * 5, ["a"] * 5, {"accuracy": 0.9})
        self.assertEqual(few["status"], "insufficient_outcomes")
        nob = D.performance("classification", ["a"] * 40, ["a"] * 40, {})
        self.assertEqual(nob["status"], "no_baseline")
        bad = D.performance("classification", ["a"] * 40, ["a"] * 10 + ["b"] * 30, {"accuracy": 0.9})
        self.assertEqual(bad["status"], "degraded")
        reg = D.performance("regression", [10.0] * 40, [11.0] * 40, {"mae": 0.5})
        self.assertEqual(reg["status"], "degraded")
        self.assertAlmostEqual(reg["bias"], 1.0)


class Monitoring(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.m1 = cls.train()
        cls.vid = cls.p7.registry.register(cls.alice, cls.m1, name="Monitored")["versions"][0]["id"]
        shifted = cls.df.copy()
        shifted["x1"] = shifted["x1"] + 5
        cls.shifted = upload(cls.ws, cls.alice, cls.project, "later.csv", sup.csv_of(shifted))["asset"]
        cls.same = upload(cls.ws, cls.alice, cls.project, "same.csv", sup.csv_of(cls.df))["asset"]

    def test_a_shifted_dataset_is_flagged_and_the_check_is_stored(self):
        r = self.p7.monitoring.check_drift(self.alice, self.vid, self.shifted["id"])
        self.assertEqual(r["status"], "significant")
        self.assertIn("x1", r["drifted_columns"])
        self.assertEqual(r["columns"]["x1"]["kind"], "numeric")
        self.assertIn("rule of thumb", r["note"])
        again = self.p7.monitoring.get_check(self.alice, r["id"])
        self.assertEqual(again["drifted_columns"], r["drifted_columns"])
        self.assertIn(r["id"], [c["id"] for c in self.p7.monitoring.checks(self.alice, self.vid)["items"]])

    def test_the_training_data_against_itself_is_stable(self):
        r = self.p7.monitoring.check_drift(self.alice, self.vid, self.same["id"])
        self.assertEqual(r["status"], "stable")
        self.assertEqual(r["drifted_columns"], [])

    def test_outcomes_drive_performance_and_few_outcomes_are_not_judged(self):
        self.assertEqual(self.p7.monitoring.performance(self.alice, self.vid)["status"], "insufficient_outcomes")
        ids = []
        for i in range(34):
            out = self.p7.registry.predict(self.alice, self.vid, {**VALUES, "x1": 0.1 * i - 1})
            ids.append((out["prediction_id"], out["prediction"]["label"]))
        for pid, label in ids:
            self.p7.monitoring.record_outcome(self.alice, self.vid, pid, label)
        ok = self.p7.monitoring.performance(self.alice, self.vid)
        self.assertEqual(ok["n"], 34)
        self.assertEqual(ok["accuracy"], 1.0)
        self.assertEqual(ok["status"], "within_expectation")
        for pid, label in ids[:24]:                                  # correcting an outcome replaces it
            self.p7.monitoring.record_outcome(self.alice, self.vid, pid, "__wrong__")
        bad = self.p7.monitoring.performance(self.alice, self.vid)
        self.assertEqual(bad["n"], 34)
        self.assertEqual(bad["status"], "degraded")
        self.assertIn("not proof", bad["note"])
        self.assertIn("no background watcher", " ".join(self.p7.monitoring.summary(self.alice, self.vid)["limits"]))

    def test_invalid_outcomes_and_foreign_predictions_are_refused(self):
        self.assertEqual(code_of(self.p7.monitoring.record_outcome, self.alice, self.vid, "nope", "x")[1], 404)

    def test_only_the_owner_can_monitor(self):
        for call in (lambda: self.p7.monitoring.check_drift(self.bob, self.vid, self.same["id"]), lambda: self.p7.monitoring.summary(self.bob, self.vid),
                     lambda: self.p7.monitoring.performance(self.bob, self.vid), lambda: self.p7.monitoring.checks(self.bob, self.vid)):
            self.assertEqual(code_of(call)[1], 404)
        c = self.p7.monitoring.check_drift(self.alice, self.vid, self.same["id"])
        self.assertEqual(code_of(self.p7.monitoring.get_check, self.bob, c["id"])[1], 404)

    def test_another_users_dataset_is_never_compared(self):
        other = self.ws.projects.create(self.bob, "Other")["id"]
        theirs = upload(self.ws, self.bob, other, "theirs.csv", sup.csv_of(self.df))["asset"]
        self.assertEqual(code_of(self.p7.monitoring.check_drift, self.alice, self.vid, theirs["id"])[1], 404)

    def test_data_from_another_project_is_not_compared(self):
        second = self.ws.projects.create(self.alice, "Second")["id"]
        elsewhere = upload(self.ws, self.alice, second, "elsewhere.csv", sup.csv_of(self.df))["asset"]
        self.assertEqual(code_of(self.p7.monitoring.check_drift, self.alice, self.vid, elsewhere["id"])[1], 404)


if __name__ == "__main__":
    unittest.main()
