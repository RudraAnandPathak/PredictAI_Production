"""Analytics service against a real database, real storage and the real numpy/pandas/scipy engine."""
import json
import unittest

import numpy as np
from scipy import stats

import analytics_support as sup
from test_workspace_extraction import Base
from test_workspace_uploads import code_of


class ServiceBase(Base):
    def setUp(self):
        super().setUp()
        self.df = sup.synth()
        self.asset = self.ok("customers.csv", sup.csv_of(self.df))
        self.an = self.ws.analytics

    def run_ops(self, ops, **kw):
        return self.an.run(self.alice, self.asset["id"], kind=kw.pop("kind", "descriptive"), operations=ops, **kw)


class RunTests(ServiceBase):
    def test_descriptive_run_matches_numpy_and_is_verified(self):
        run = self.run_ops([{"op": "column_stats", "params": {"columns": ["age"]}}])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["evidence_status"], "VERIFIED", run["result"]["evidence"]["missing"])
        data = run["result"]["results"][0]["data"]
        self.assertIn("age", json.dumps(data))
        self.assertAlmostEqual(self.df["age"].mean(), float(self.df["age"].mean()))
        self.assertTrue(run["result"]["evidence"]["source"]["checksum_sha256"])

    def test_identical_plans_reproduce_identical_hashes(self):
        ops = [{"op": "correlation", "params": {"columns": ["age", "income"]}}]
        a = self.run_ops(ops, kind="relationships")
        b = self.an.rerun(self.alice, a["id"])
        self.assertTrue(b["reproduction"]["reproduced"])
        self.assertEqual(a["result_hash"], b["result_hash"])
        self.assertEqual(b["parent_run_id"], a["id"])

    def test_rerun_after_change_of_seed_is_a_different_plan(self):
        ops = [{"op": "discover", "params": {}}]
        a = self.run_ops(ops, kind="relationships", seed=1)
        b = self.run_ops(ops, kind="relationships", seed=2)
        self.assertNotEqual(a["plan_hash"], b["plan_hash"])

    def test_filters_limit_rows_and_are_recorded(self):
        run = self.run_ops([{"op": "dataset_summary", "params": {}}], filters=[{"column": "region", "op": "eq", "value": "West"}])
        self.assertEqual(run["rows_used"], int((self.df["region"] == "West").sum()))
        self.assertEqual(run["result"]["evidence"]["source"]["row_snapshot"]["filters"][0]["column"], "region")

    def test_invalid_plans_are_rejected_without_running(self):
        for ops in ([], [{"op": "os.system", "params": {}}], [{"op": "column_stats", "params": {"columns": ["nope"]}}],
                    [{"op": "column_stats", "params": {"columns": ["age"], "__import__": "x"}}]):
            self.assertEqual(code_of(lambda: self.run_ops(ops)), "VALIDATION_ERROR")

    def test_hypothesis_reports_test_details_and_effect_size(self):
        run = self.run_ops([{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "spend", "group": "region"}}], kind="hypothesis")
        res = run["result"]["results"][0]
        self.assertEqual(res["status"], "ok", res)
        d = res["data"]
        r = d["result"]
        for k in ("test", "statistic", "df", "p_value", "effect_size"):
            self.assertIn(k, r, k)
        self.assertIn("ci", r["effect_size"])
        self.assertTrue(d["assumption_checks"] and d["hypothesis"]["null"] and d["hypothesis"]["alternative_text"] and d["multiple_testing"])
        self.assertTrue(res["evidence"]["limitations"] and res["evidence"]["assumptions"] and res["evidence"]["sample_size"])
        groups = [g.dropna().to_numpy() for _, g in self.df.groupby("region")["spend"]]
        self.assertLess(stats.f_oneway(*groups).pvalue, 0.05)
        self.assertLess(r["p_value"], 0.05)
        self.assertAlmostEqual(r["p_value"], stats.f_oneway(*groups).pvalue, delta=1e-15)
        self.assertIn("not the probability", json.dumps(res).lower())

    def test_second_test_run_adds_multiple_testing_note(self):
        op = [{"op": "hypothesis", "params": {"kind": "correlation", "outcome": "age", "second": "income"}}]
        self.run_ops(op, kind="hypothesis")
        second = self.run_ops(op, kind="hypothesis", seed=5)
        self.assertTrue(any("earlier statistical test" in n for n in second["result"]["notes"]))

    def test_run_is_listed_saved_and_audited_without_values(self):
        run = self.run_ops([{"op": "frequency", "params": {"column": "region"}}])
        self.an.save_run(self.alice, run["id"], saved=True, label="Regions")
        listed = self.an.list_runs(self.alice, self.asset["id"], saved_only=True)
        self.assertEqual([r["id"] for r in listed["items"]], [run["id"]])
        rows = self.h.conn.execute("SELECT metadata FROM audit_events WHERE event_type = 'analysis.run'").fetchall()
        self.assertTrue(rows)
        self.assertNotIn("North", " ".join(r["metadata"] or "" for r in rows))


class DrillExportTests(ServiceBase):
    def chart(self):
        return self.run_ops([{"op": "chart", "params": {"type": "bar", "x": "region"}}], kind="chart")

    def test_drill_down_rows_match_the_selected_bar(self):
        run = self.chart()
        bars = run["result"]["results"][0]["data"]["bars"]
        west = next(b for b in bars if b["label"] == "West")
        out = self.an.rows(self.alice, run["id"], west["select"], limit=200)
        self.assertEqual(out["total"], int((self.df["region"] == "West").sum()))
        ci = out["columns"].index("region")
        self.assertTrue(all(i["values"][ci] == "West" for i in out["items"]))

    def test_export_rows_neutralises_formulas_and_is_capped(self):
        df = self.df.copy()
        df.loc[0, "region"] = "=HYPERLINK(\"http://x\")"
        a = self.ok("evil.csv", sup.csv_of(df))
        run = self.an.run(self.alice, a["id"], kind="chart", operations=[{"op": "chart", "params": {"type": "bar", "x": "region"}}])
        name, ctype, body = self.an.export(self.alice, run["id"], what="rows", select={"type": "all"})
        text = body.decode()
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)

    def test_invalid_selection_is_refused(self):
        run = self.chart()
        for sel in ({"type": "sql", "q": "1"}, {"type": "category", "column": "nope", "value": "x"}, {"type": "rows", "rows": [0]}):
            with self.assertRaises(Exception):
                self.an.rows(self.alice, run["id"], sel)


class IsolationTests(ServiceBase):
    def test_other_user_gets_identical_not_found_everywhere(self):
        run = self.run_ops([{"op": "dataset_summary", "params": {}}])
        calls = [lambda: self.an.get_run(self.bob, run["id"]), lambda: self.an.rerun(self.bob, run["id"]), lambda: self.an.rows(self.bob, run["id"], {"type": "all"}),
                 lambda: self.an.export(self.bob, run["id"], what="result"), lambda: self.an.save_run(self.bob, run["id"], saved=True),
                 lambda: self.an.schema(self.bob, self.asset["id"]), lambda: self.an.run(self.bob, self.asset["id"], kind="descriptive", operations=[{"op": "dataset_summary", "params": {}}]),
                 lambda: self.an.list_runs(self.bob, self.asset["id"]), lambda: self.an.get_run(self.bob, "does-not-exist")]
        seen = set()
        for call in calls:
            seen.add(code_of(call))
        self.assertEqual(len(seen), 1, seen)

    def test_deleting_the_dataset_removes_runs_and_result_files(self):
        run = self.run_ops([{"op": "dataset_summary", "params": {}}])
        self.ws.assets.delete(self.alice, self.asset["id"]) if hasattr(self.ws.assets, "delete") else self.skipTest("no delete")
        self.assertIsNone(self.h.conn.execute("SELECT 1 FROM analysis_runs WHERE id = ?", (run["id"],)).fetchone())
        self.assertEqual(self.ws.storage.list_prefix(f"p/{self.project}/a/{self.asset['id']}/n/"), [])


if __name__ == "__main__":
    unittest.main()
