"""The analytics engine against known truth: numpy/scipy/statistics reference values, edge cases, determinism, evidence contract, isolation."""
import ast
import io
import json
import math
import os
import pathlib
import statistics
import unittest
from unittest import mock

import numpy as np
import pandas as pd
from scipy import stats

import analytics_support as sup
from analytics_support import first, run_engine
from app.core.errors import AppError
from app.modules.analytics.evidence import op_missing
from app.modules.analytics.ops.common import benjamini_hochberg, holm


class DescriptiveTruth(unittest.TestCase):
    def test_column_stats_match_reference_implementations(self):
        v = [3.0, 7.0, 7.0, 19.0, 24.0, 2.5, 8.0, 11.0, 13.0, 50.0]
        df = pd.DataFrame({"x": v})
        _, out = run_engine(df, [{"op": "column_stats", "params": {"columns": ["x"]}}])
        c = first(out)["data"]["columns"][0]
        self.assertAlmostEqual(c["mean"], statistics.fmean(v), places=6)
        self.assertAlmostEqual(c["median"], statistics.median(v), places=6)
        self.assertAlmostEqual(c["std"], statistics.stdev(v), places=5)
        self.assertAlmostEqual(c["variance"], statistics.variance(v), delta=1e-4)
        self.assertEqual((c["min"], c["max"], c["non_missing"], c["missing"]), (2.5, 50.0, 10, 0))
        self.assertAlmostEqual(c["skewness"], float(stats.skew(v, bias=False)), places=4)
        self.assertAlmostEqual(c["iqr"], float(np.subtract(*np.percentile(v, [75, 25]))), places=6)
        self.assertEqual(first(out)["evidence"]["sample_size"], 10)

    def test_missing_values_are_reported_not_imputed(self):
        df = pd.DataFrame({"x": [1.0, np.nan, 3.0, np.nan, 5.0], "y": ["a", "b", None, "b", "a"]})
        _, out = run_engine(df, [{"op": "missing_values", "params": {}}])
        by = {c["column"]: c for c in first(out)["data"]["columns"]}
        self.assertEqual((by["x"]["missing"], by["y"]["missing"]), (2, 1))
        _, out = run_engine(df, [{"op": "aggregate", "params": {"agg": "mean", "measure": "x"}}])
        self.assertAlmostEqual(first(out)["data"]["overall"]["value"], 3.0)
        self.assertEqual(first(out)["data"]["overall"]["n"], 3)

    def test_constant_empty_and_tiny_inputs_do_not_crash_or_invent_results(self):
        df = pd.DataFrame({"a": [5.0] * 20, "b": np.arange(20.0)})
        _, out = run_engine(df, [{"op": "correlation", "params": {"columns": ["a", "b"]}}, {"op": "discover", "params": {}}])
        self.assertTrue(all(r["status"] in ("ok", "not_run", "failed") for r in out["results"]))
        d = first(out, "discover")["data"]
        self.assertEqual(d["patterns"], [])
        self.assertEqual(d["message"], "Not enough evidence.")
        empty = pd.DataFrame({"a": pd.Series([], dtype="float64"), "b": pd.Series([], dtype="float64")})
        _, out = run_engine(empty, [{"op": "column_stats", "params": {"columns": ["a"]}}, {"op": "dataset_summary", "params": {}}])
        self.assertEqual(out["rows_in_dataset"], 0)
        for r in out["results"]:
            self.assertIn(r["status"], ("ok", "not_run", "failed"))
        small = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 7.0]})
        _, out = run_engine(small, [{"op": "hypothesis", "params": {"kind": "correlation", "outcome": "x", "second": "y"}}])
        r = first(out)
        self.assertEqual(r["status"], "not_run")
        self.assertIsNone((r["data"] or {}).get("result"))
        self.assertIn("Not enough evidence", json.dumps(r["data"]))

    def test_outlier_flagging_uses_a_stated_rule(self):
        v = list(np.random.default_rng(1).normal(100, 5, 200)) + [400.0]
        _, out = run_engine(pd.DataFrame({"x": v}), [{"op": "data_quality", "params": {}}])
        issue = next(i for i in first(out)["data"]["issues"] if i["code"] == "OUTLIERS")
        self.assertGreaterEqual(issue["count"], 1)
        self.assertIn(201, issue["row_numbers"])      # the planted 400 is flagged
        self.assertIn("1.5", issue["method"])


class RelationshipTruth(unittest.TestCase):
    def test_exact_linear_relationship_and_direction(self):
        x = np.arange(1, 51, dtype=float)
        df = pd.DataFrame({"x": x, "y": 2 * x + 1, "z": -x})
        _, out = run_engine(df, [{"op": "correlation", "params": {"columns": ["x", "y", "z"]}}])
        pairs = {(p["x"], p["y"]): p for p in first(out)["data"]["pairs"]}
        self.assertAlmostEqual(pairs[("x", "y")]["pearson"]["r"], 1.0, places=9)
        self.assertEqual(pairs[("x", "z")]["direction"], "negative")

    def test_pearson_matches_scipy_and_ci_contains_estimate(self):
        df = sup.synth()
        d = df.dropna(subset=["age", "spend"])
        _, out = run_engine(df, [{"op": "correlation", "params": {"columns": ["age", "spend"], "methods": ["pearson", "spearman"]}}])
        p = first(out)["data"]["pairs"][0]
        r, pv = stats.pearsonr(d["age"], d["spend"])
        rho, _ = stats.spearmanr(d["age"], d["spend"])
        self.assertAlmostEqual(p["pearson"]["r"], r, places=6)
        self.assertAlmostEqual(p["spearman"]["rho"], rho, places=6)
        lo, hi = p["pearson"]["ci"]
        self.assertTrue(lo < r < hi)
        self.assertEqual(p["n"], len(d))

    def test_outlier_makes_the_engine_prefer_spearman_and_say_why(self):
        _, out = run_engine(sup.synth(), [{"op": "correlation", "params": {"columns": ["age", "income"]}}])
        p = first(out)["data"]["pairs"][0]
        self.assertEqual(p["primary_measure"], "spearman")
        self.assertIn("outliers", p["pearson_caution"])

    def test_group_difference_and_chi_square_match_scipy(self):
        df = sup.synth()
        _, out = run_engine(df, [{"op": "contingency", "params": {"row": "region", "column": "segment"}}])
        ct = pd.crosstab(df["region"], df["segment"])
        chi2, p, dof, _ = stats.chi2_contingency(ct, correction=False)
        got = first(out)["data"]["chi_square"]
        self.assertAlmostEqual(got["statistic"], chi2, places=4)
        self.assertEqual(got["df"], dof)

    def test_discover_ranks_planted_relationships_and_never_reports_noise(self):
        _, out = run_engine(sup.synth(), [{"op": "discover", "params": {}}])
        pats = first(out)["data"]["patterns"]
        top = {tuple(sorted((p["x"], p["y"]))) for p in pats[:4]}
        self.assertIn(("age", "income"), top)
        self.assertIn(("region", "segment"), top)
        rng = np.random.default_rng(3)
        noise = pd.DataFrame({f"c{i}": rng.normal(size=300) for i in range(6)})
        _, out = run_engine(noise, [{"op": "discover", "params": {}}])
        d = first(out)["data"]
        self.assertEqual(d["patterns"], [])
        self.assertEqual(d["message"], "Not enough evidence.")


class HypothesisTruth(unittest.TestCase):
    def groups(self, shift=1.0, n=60, sd=1.0, seed=2):
        r = np.random.default_rng(seed)
        return pd.DataFrame({"v": np.r_[r.normal(0, sd, n), r.normal(shift, sd, n)], "g": ["a"] * n + ["b"] * n})

    def test_two_group_test_reports_everything_and_matches_scipy_welch(self):
        df = self.groups()
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g", "prefer": "parametric"}}])
        r = first(out)
        d = r["data"]
        a, b = df[df.g == "a"].v, df[df.g == "b"].v
        res = d["result"]
        t = stats.ttest_ind(a, b, equal_var=False) if "Welch" in res["test"] else stats.ttest_ind(a, b)
        self.assertAlmostEqual(abs(res["statistic"]), abs(t.statistic), places=4)
        self.assertAlmostEqual(res["p_value"], t.pvalue, places=8)
        self.assertTrue(res["effect_size"]["ci"])
        self.assertTrue(d["assumption_checks"])
        self.assertIn("not the probability that the hypothesis is true", d["p_value_note"])
        self.assertTrue(r["evidence"]["limitations"])

    def test_unequal_variances_select_welch(self):
        df = pd.concat([self.groups(0.0, 80, 1.0, 1).query("g=='a'"), self.groups(0.0, 80, 6.0, 2).query("g=='b'")])
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g"}}])
        self.assertIn("Welch", first(out)["data"]["result"]["test"])

    def test_skewed_data_selects_a_rank_based_test(self):
        r = np.random.default_rng(5)
        df = pd.DataFrame({"v": np.r_[r.exponential(1, 80), r.exponential(2.5, 80)], "g": ["a"] * 80 + ["b"] * 80})
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g"}}])
        self.assertIn(first(out)["data"]["result"]["test"], ("Mann-Whitney U test",))

    def test_null_effect_is_not_declared_significant(self):
        df = self.groups(shift=0.0, n=100, seed=11)
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g"}}])
        dec = first(out)["data"]["result"]["decision"]
        self.assertFalse(dec["reject_null"])
        self.assertNotIn("effect", dec["wording"].lower().replace("effect size", ""))

    def test_plan_only_mode_runs_no_test(self):
        _, out = run_engine(self.groups(), [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g", "execute": False}}])
        d = first(out)["data"]
        self.assertFalse(d["executed"])
        self.assertIsNone(d.get("result"))
        self.assertTrue(d["hypothesis"]["null"] and d["selected_test"])

    def test_too_small_groups_are_refused_not_tested(self):
        df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 10.0], "g": ["a", "a", "b", "b"]})
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "v", "group": "g"}}])
        r = first(out)
        self.assertEqual(r["status"], "not_run")
        self.assertIsNone((r["data"] or {}).get("result"))

    def test_sparse_chi_square_is_not_run_as_if_assumptions_held(self):
        df = pd.DataFrame({"a": ["x"] * 9 + ["y"] * 1 + ["x"] * 1 + ["y"] * 9 + ["z"] * 2, "b": ["p"] * 10 + ["q"] * 10 + ["p", "q"]})
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "association", "outcome": "a", "group": "b"}}])
        d = first(out)["data"]
        if d.get("result") is None:
            self.assertFalse(d["executed"])
        else:
            self.assertIn("Fisher", d["result"]["test"] + json.dumps(d))

    def test_multiple_testing_corrections_match_known_values(self):
        p = [0.01, 0.04, 0.03, 0.005]
        self.assertEqual([round(x, 6) for x in benjamini_hochberg(p)], [0.02, 0.04, 0.04, 0.02])
        self.assertEqual([round(x, 6) for x in holm(p)], [0.03, 0.06, 0.06, 0.02])

    def test_paired_test_matches_scipy(self):
        r = np.random.default_rng(8)
        before = r.normal(10, 2, 50)
        after = before + r.normal(0.8, 1, 50)
        df = pd.DataFrame({"before": before, "after": after})
        _, out = run_engine(df, [{"op": "hypothesis", "params": {"kind": "paired", "outcome": "before", "second": "after", "prefer": "parametric"}}])
        res = first(out)["data"]["result"]
        self.assertAlmostEqual(res["p_value"], stats.ttest_rel(before, after).pvalue, places=8)


class Determinism(unittest.TestCase):
    OPS = [{"op": "discover", "params": {}}, {"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "spend", "group": "region"}}]

    def test_same_seed_same_hash_different_seed_same_estimates(self):
        from app.modules.analytics.evidence import result_hash
        from app.modules.analytics.plan import plan_hash
        df = sup.synth()
        p1, o1 = run_engine(df, self.OPS, seed=5)
        p2, o2 = run_engine(df, self.OPS, seed=5)
        self.assertEqual(result_hash(plan_hash(p1), o1["results"], o1["rows_in_scope"]), result_hash(plan_hash(p2), o2["results"], o2["rows_in_scope"]))
        _, o3 = run_engine(df, self.OPS, seed=6)
        e1, e3 = first(o1, "hypothesis")["data"]["result"], first(o3, "hypothesis")["data"]["result"]
        self.assertEqual(e1["p_value"], e3["p_value"])
        self.assertNotEqual(e1["effect_size"]["ci"], e3["effect_size"]["ci"])

    def test_subprocess_isolation_gives_identical_results_to_in_process(self):
        df = sup.synth(n=300)
        ops = [{"op": "column_stats", "params": {"columns": ["age", "spend"]}}, {"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": "spend", "group": "region"}}]
        p, a = run_engine(df, ops, isolation="inprocess")
        _, b = run_engine(df, ops, isolation="subprocess")
        self.assertEqual(b["isolation"]["mode"], "subprocess")
        self.assertEqual(json.dumps([r["data"] for r in a["results"]], sort_keys=True), json.dumps([r["data"] for r in b["results"]], sort_keys=True))
        self.assertIn("scrubbed environment", b["isolation"]["limits_applied"])

    def test_subprocess_environment_is_scrubbed_of_secrets(self):
        import subprocess as sp
        captured = {}
        real = sp.Popen

        def spy(cmd, **kw):
            if any("_boot.py" in str(part) for part in cmd):          # the analysis worker, not the one-off platform probe
                captured.update(kw)
                captured["cmd"] = cmd
            return real(cmd, **kw)

        with mock.patch.dict(os.environ, {"PREDICTAI_SECRET_KEY": "super-secret-value", "AWS_SECRET_ACCESS_KEY": "x"}), mock.patch("subprocess.Popen", spy):
            run_engine(sup.synth(n=100), [{"op": "dataset_summary", "params": {}}], isolation="subprocess")
        self.assertTrue(captured, "the worker process was not started through the reviewed launcher")
        self.assertNotIn("PREDICTAI_SECRET_KEY", captured["env"])
        self.assertFalse(any("SECRET" in k for k in captured["env"]))
        self.assertIn("-I", captured["cmd"])
        self.assertIsNone(captured.get("shell"))
        self.assertEqual(captured["stdin"], sp.DEVNULL)
        self.assertTrue(captured["start_new_session"])

    def test_time_budget_is_enforced_and_recorded(self):
        from app.modules.analytics.executor import limits_for
        lim = dict(limits_for(False), time_budget_s=0)
        _, out = run_engine(sup.synth(n=500), [{"op": "discover", "params": {}}, {"op": "dataset_summary", "params": {}}], limits=lim)
        codes = {(r.get("error") or {}).get("code") for r in out["results"]}
        self.assertTrue(codes & {"ANALYSIS_TIMEOUT"}, out["results"])

    def test_memory_limit_stops_a_hungry_analysis_in_the_subprocess(self):
        # 40 MB is below what the interpreter plus pandas need, so the run must be stopped on every platform: by the kernel limit where the OS
        # enforces one (Linux), by the resident-memory watchdog everywhere else (macOS). Either way the caller sees the same coded error.
        from app.modules.analytics.executor import limits_for
        lim = dict(limits_for(False), memory_mb=40)
        with self.assertRaises(Exception) as cm:
            run_engine(sup.synth(n=500), [{"op": "dataset_summary", "params": {}}], limits=lim, isolation="subprocess")
        self.assertEqual(getattr(cm.exception, "code", ""), "ANALYSIS_MEMORY")

    def test_table_too_large_for_the_memory_limit_is_refused_before_computing(self):
        from app.modules.analytics.executor import limits_for
        lim = dict(limits_for(False), memory_mb=1)
        with self.assertRaises(Exception) as cm:
            run_engine(sup.synth(n=5000), [{"op": "dataset_summary", "params": {}}], limits=lim, isolation="inprocess")
        self.assertEqual(getattr(cm.exception, "code", ""), "ANALYSIS_MEMORY")

    def test_isolation_report_states_how_memory_is_really_enforced(self):
        from app.modules.analytics import sandbox
        _, out = run_engine(sup.synth(n=100), [{"op": "dataset_summary", "params": {}}], isolation="subprocess")
        mem = out["isolation"]["memory"]
        self.assertEqual(mem["mode"], "hard" if sandbox.hard_memory_limit_supported() else "monitored")
        has_kernel = "address space (kernel limit, verified)" in out["isolation"]["limits_applied"]
        self.assertEqual(has_kernel, mem["mode"] == "hard")          # never claims a kernel limit the probe did not prove
        self.assertIn("memory watchdog (resident memory)", out["isolation"]["limits_applied"])

    def test_oversize_tables_are_refused_not_silently_truncated(self):
        from app.modules.analytics.executor import limits_for
        lim = dict(limits_for(False), max_rows=100)
        with self.assertRaises(Exception) as cm:
            run_engine(sup.synth(n=300), [{"op": "dataset_summary", "params": {}}], limits=lim)
        self.assertTrue(str(getattr(cm.exception, "code", "")).endswith("TOO_LARGE"))
        _, out = run_engine(sup.synth(n=300), [{"op": "dataset_summary", "params": {}}], limits=lim, sample_rows=100)
        self.assertEqual(out["rows_in_scope"], 100)
        self.assertTrue(out["sampled"])


class PlanValidationAndSafety(unittest.TestCase):
    def test_unknown_things_are_rejected(self):
        df = sup.synth(n=60)
        for ops in ([{"op": "eval", "params": {}}], [{"op": "column_stats", "params": {"columns": ["__class__"]}}], [{"op": "aggregate", "params": {"agg": "median; DROP", "measure": "age"}}], [{"op": "dataset_summary", "params": {"x": 1}}]):
            with self.assertRaises(AppError):
                run_engine(df, ops)
        with self.assertRaises(AppError):
            run_engine(df, [{"op": "dataset_summary", "params": {}}], filters=[{"column": "age", "op": "gt", "value": {"$ne": 1}}])
        _, out = run_engine(df, [{"op": "aggregate", "params": {"agg": "mean", "measure": "region"}}])     # valid shape, wrong column type: recorded as a failed analysis with a clear reason
        self.assertEqual(first(out)["status"], "failed")
        self.assertEqual(first(out)["error"]["code"], "PARAM_INVALID")

    def test_engine_source_has_no_dynamic_code_network_or_shell(self):
        root = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules" / "analytics"
        banned_calls = {"eval", "exec", "compile", "__import__", "open", "system", "popen"}
        banned_imports = {"socket", "requests", "urllib", "http", "ftplib", "smtplib", "ctypes", "pickle", "marshal", "shelve"}
        for path in root.rglob("*.py"):
            if path.name in ("providers.py", "executor.py"):      # providers: the one reviewed outbound call; executor: the one reviewed subprocess
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                    if isinstance(fn, ast.Attribute) and name in ("open", "compile"):
                        continue                                  # storage.open(...) is the authorised reader; re.compile is a regex
                    if name in banned_calls and not (name == "open" and path.name in ("worker.py",)):
                        self.fail(f"{path.name}: call to {name}")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for m in mods:
                        self.assertNotIn(m.split(".")[0], banned_imports, f"{path.name} imports {m}")


class EvidenceContract(unittest.TestCase):
    def test_complete_results_are_verified_and_missing_fields_make_them_incomplete(self):
        _, out = run_engine(sup.synth(), [{"op": "hypothesis", "params": {"kind": "correlation", "outcome": "age", "second": "spend"}}])
        r = first(out)
        self.assertEqual(op_missing(r, True), [])
        for field in ("assumptions", "limitations", "uncertainty", "sample_size", "method"):
            broken = json.loads(json.dumps(r))
            broken["evidence"].pop(field, None)
            self.assertTrue(op_missing(broken, True), f"removing {field} should be flagged")

    def test_evidence_document_has_every_required_section(self):
        from app.modules.analytics.evidence import assemble
        plan, out = run_engine(sup.synth(n=200), [{"op": "aggregate", "params": {"agg": "mean", "measure": "spend", "group_by": ["region"]}}])
        from app.modules.analytics.ops import REGISTRY
        asset = {"id": "a" * 32, "name": "x.csv"}
        version = {"id": "v" * 32, "version_number": 1, "kind": "original", "checksum_sha256": "c" * 64, "format": "csv"}
        ev = assemble(plan=plan, plan_hash="h", run_id="r", project_id="p", asset=asset, version=version, lineage=[], results=out["results"], specs=REGISTRY, rows_in_scope=out["rows_in_scope"],
                      rows_in_dataset=out["rows_in_dataset"], sampled=None, executed_at="2026-01-01T00:00:00Z", isolation=out["isolation"], run_warnings=[])
        for section in ("source", "calculation", "inputs", "interpretation", "access"):
            self.assertIn(section, ev)
        self.assertEqual(ev["status"], "VERIFIED", ev["missing"])
        self.assertEqual(ev["calculation"]["plan_hash"], "h")
        # a missing source checksum must downgrade the evidence
        bad = dict(version, checksum_sha256="")
        ev2 = assemble(plan=plan, plan_hash="h", run_id="r", project_id="p", asset=asset, version=bad, lineage=[], results=out["results"], specs=REGISTRY, rows_in_scope=1,
                       rows_in_dataset=1, sampled=None, executed_at="2026-01-01T00:00:00Z", isolation=out["isolation"], run_warnings=[])
        self.assertEqual(ev2["status"], "INCOMPLETE")
        self.assertIn("source.checksum_sha256", ev2["missing"])


if __name__ == "__main__":
    unittest.main()
