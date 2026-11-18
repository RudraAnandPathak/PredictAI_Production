"""Dataset research: a question about the person's own data is answered by calculation (the Phase 4 analytics and Phase 5 model services), never by a language model.

Every number checked here is computed again independently with pandas from the same fixed-seed data, then compared with the number the research reported, so these tests show that the
reported figures are right, not merely that a figure is present. No outside website is contacted."""
import json
import re
import unittest

import numpy as np
import pandas as pd

from app.core.errors import AppError
from app.modules.research.datasets import DatasetResearcher, claims_compare, claims_discover, detect_target, is_identifier, num, regenerate, RegenerateError
from app.modules.research.planner import build_plan
from app.modules.workspace import keys

from research_data_support import CANARY, DatasetResearchBase, airline_frame
from support import Harness  # noqa: F401  (kept so the shared fixture imports resolve the same way everywhere)
from test_workspace_uploads import upload
import analytics_support as sup


def numbers(text):
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*(?:e[+-]?\d+)?", text)]


def close(found, expected, rel=0.006):
    return any(abs(x - expected) <= rel * abs(expected) + 1e-9 for x in found)


class PureFunctions(unittest.TestCase):
    """The sentence templates and helpers, with no database."""

    def test_numbers_are_written_the_way_a_person_reads_them(self):
        self.assertEqual(num(11985.43), "11,985")
        self.assertEqual(num(300), "300")
        self.assertEqual(num(0.0216), "0.0216")
        self.assertEqual(num(None), "n/a")
        self.assertEqual(num(123.456), "123.5")

    def test_identifier_like_columns_are_recognised_by_name(self):
        for name in ("row_id", "id", "customer_id", "Index", "uuid", "Unnamed: 0"):
            self.assertTrue(is_identifier(name), name)
        for name in ("price", "paid_amount", "valid", "ideal_weight"):
            self.assertFalse(is_identifier(name), name)

    def test_the_outcome_named_in_the_question_is_found_and_preferred_after_with_of_for(self):
        cols = [{"name": "price"}, {"name": "seats_available"}, {"name": "passengers"}]
        self.assertEqual(detect_target("What factors are associated with passengers?", cols, []), "passengers")
        self.assertEqual(detect_target("What is related to seats available?", cols, []), "seats_available")
        self.assertIsNone(detect_target("What does this dataset tell us?", cols, []))
        self.assertEqual(detect_target("What does this tell us?", cols, [{"target": "bought"}]), "bought")

    def test_a_relationship_sentence_quotes_its_numbers_and_never_states_a_cause(self):
        data = {"patterns": [{"x": "price", "y": "passengers", "measure": "Spearman rho", "strength": -0.612, "direction": "negative", "n": 540, "p_adjusted_bh": 0.0004, "practical_relevance": "large"}]}
        claim = claims_discover(data, {"name": "airline.csv", "target": "passengers"})[0]
        self.assertIn("-0.612", claim.text)
        self.assertIn("n = 540", claim.text)
        self.assertIn("p < 0.001", claim.text)
        self.assertNotRegex(claim.text.lower(), r"\b(cause|causes|caused|because|leads to|results in)\b")
        self.assertTrue(any("does not show that one thing causes" in c for c in claim.caveats))

    def test_comparing_two_datasets_describes_the_files_and_does_not_claim_a_test(self):
        item = lambda name, mean: {"name": name, "columns": ["price"], "summary": {"rows_in_dataset": 10, "columns": 1}, "stats": {"price": {"column": "price", "mean": mean, "median": mean}}}  # noqa: E731
        claims = claims_compare([item("a.csv", 100.0), item("b.csv", 120.0)])
        self.assertEqual(len(claims), 2)
        self.assertIn("not a test", claims[1].text)

    def test_a_stored_result_that_changed_or_vanished_cannot_rebuild_a_statement(self):
        calc = {"fn": "dataset_summary", "claim": "summary", "run": {"run_id": "r1", "op": "dataset_summary", "op_index": 0, "result_hash": "H"}, "ctx": {"name": "x.csv"}}
        run = {"result": {"result_hash": "H", "results": [{"op": "dataset_summary", "status": "ok", "data": {"rows_in_dataset": 5, "columns": 2, "column_types": {"integer": 2}, "missing_cells": {"count": 0, "pct": 0},
                                                                                                    "complete_rows": 5, "duplicate_rows": {"count": 0}}}]}}
        self.assertIn("5 rows", regenerate(calc, lambda rid: run))
        with self.assertRaises(RegenerateError):
            regenerate(calc, lambda rid: {"result": {**run["result"], "result_hash": "OTHER"}})
        with self.assertRaises(RegenerateError):
            regenerate({**calc, "claim": "nothing"}, lambda rid: run)
        with self.assertRaises(RegenerateError):
            regenerate({**calc, "fn": "mystery"}, lambda rid: run)


class NumbersAreRight(DatasetResearchBase):
    def test_a_relationship_with_the_outcome_matches_an_independent_correlation_ratio(self):
        s, b = self.ask("What factors are associated with bought?", [self.cust["id"]])
        df = self.cust_df
        grand = df["x1"].mean()
        ssb = sum(len(g) * (g["x1"].mean() - grand) ** 2 for _, g in df.groupby("bought"))
        eta = (ssb / ((df["x1"] - grand) ** 2).sum()) ** 0.5
        quotes = [e["quote"] for e in self.calc(b) if "'x1'" in e["quote"] and "'bought'" in e["quote"] and "associated" in e["quote"]]
        self.assertTrue(quotes, [e["quote"] for e in self.calc(b)])
        self.assertTrue(close(numbers(quotes[0]), eta), (quotes[0], eta))
        self.assertTrue(close(numbers(quotes[0]), len(df), rel=0), "the number of rows used is reported exactly")
        self.assertEqual(b["plan"]["subtopics"][0]["scope"], "dataset")

    def test_a_category_split_matches_the_value_counts(self):
        s, b = self.ask("What is the distribution of plan?", [self.cust["id"]])
        shares = (self.cust_df["plan"].value_counts(normalize=True) * 100).round(1)
        text = next(e["quote"] for e in self.calc(b) if e["quote"].startswith("The values of 'plan'"))
        for level, pct in shares.items():
            self.assertTrue(re.search(rf"{level} {pct:.0f}(\.\d)?%", text) or re.search(rf"{level} {pct:.3g}%", text), (level, pct, text))

    def test_group_means_and_counts_match_pandas(self):
        s, b = self.ask("Are there differences in price between fare_class groups?", [self.air["id"]])
        text = next(e["quote"] for e in self.calc(b) if e["quote"].startswith("Average 'price' by 'fare_class'"))
        for level, g in self.air_df.groupby("fare_class"):
            self.assertIn(f"{level} = {num(float(g['price'].mean()))} (n = {len(g)})", text)
        diff = next(e["quote"] for e in self.calc(b) if "than in" in e["quote"])
        self.assertIn("Hedges g", diff)
        self.assertIn("95% interval", diff)

    def test_missing_and_suspicious_values_are_found_with_their_real_percentages(self):
        s, b = self.ask("Which columns have missing or suspicious values?", [self.air["id"]])
        quotes = " ".join(e["quote"] for e in self.calc(b))
        pct = self.air_df["passengers"].isna().mean() * 100
        self.assertIn(f"'passengers' ({num(pct)}% missing)", quotes)
        self.assertRegex(quotes, r"value\(s\) of 'delay_minutes' lie outside the usual range")
        self.assertEqual({e["origin"] for e in b["evidence"]}, {"calculated"})

    def test_a_dataset_summary_reports_rows_columns_and_missing_cells_exactly(self):
        s, b = self.ask("What does this dataset tell us?", [self.air["id"]])
        text = next(e["quote"] for e in self.calc(b) if e["quote"].startswith("airline.csv has"))
        df = self.air_df
        self.assertIn(f"{len(df)} rows and {df.shape[1]} columns", text)
        self.assertIn(f"{int(df.isna().sum().sum())} cells", text)
        self.assertIn(f"{int(df.dropna().shape[0])} rows are complete", text)

    def test_an_identifier_column_is_never_reported_as_a_relationship(self):
        s, b = self.ask("What are the strongest relationships in the data?", [self.cust["id"]])
        for e in self.calc(b):
            self.assertNotIn("'row_id'", e["quote"])

    def test_relationships_are_stated_as_associations_with_a_caveat_and_never_as_causes(self):
        s, b = self.ask("What drives bought?", [self.cust["id"]])
        rel = [c for c in b["conclusions"] if "associated" in c["text"] or "relationship" in c["text"]]
        self.assertTrue(rel)
        for c in rel:
            self.assertTrue(any("does not show that one thing causes" in x for x in c["caveats"]), c)
            self.assertEqual(c["confidence"], "calculated")
        ours = " ".join(e["quote"] for e in self.calc(b) if e["citation"].get("kind") == "calculation").lower()      # sentences PredictAI's own templates wrote
        self.assertTrue(ours)
        self.assertNotRegex(ours, r"\b(causes|caused|because of|leads to|results in)\b")


class ModelsAndEarlierResults(DatasetResearchBase):
    def test_model_sentences_reuse_the_phase_5_numbers_and_stay_labelled_as_estimates(self):
        s, b = self.ask("Which variables are useful for prediction, and how good is the model?", [self.cust["id"]])
        ev = [e for e in b["evidence"] if e["origin"] == "model_prediction"]
        self.assertTrue(ev)
        self.assertTrue(all(e["kind"] == "prediction" for e in ev))
        head = next(e["quote"] for e in ev if "was trained to predict" in e["quote"])
        self.assertTrue(close(numbers(head), self.model["test_score"]), (head, self.model["test_score"]))
        report = self.ws.predictive.report(self.alice, self.model["id"])
        top = report["importance"]["permutation"]["features"][0]["feature"]
        self.assertTrue(any(f"'{top}'" in e["quote"] for e in ev if "shuffling" in e["quote"]))
        for e in ev:
            self.assertTrue(any("not causation" in c or "estimates" in c for c in e["caveats"]))

    def test_saved_phase_4_findings_and_earlier_runs_are_reused_and_checked(self):
        run = self.ws.analytics.run(self.alice, self.cust["id"], kind="relationships", operations=[{"op": "correlation", "params": {"columns": ["x1", "x2", "noise"], "top": 5}}], label="Churn check")
        s, b = self.ask("Explain these statistical results", [self.cust["id"]])
        earlier = [e for e in b["evidence"] if e["quote"].startswith("Earlier result (Churn check)")]
        self.assertTrue(earlier or any("Saved finding" in e["quote"] for e in b["evidence"]), [e["quote"] for e in b["evidence"]])
        saved = [e for e in b["evidence"] if "Saved finding" in e["quote"]]
        self.assertTrue(saved, "the saved Phase 4 findings are carried into the answer")
        self.assertTrue(all("evidence status" in e["quote"] for e in saved))
        self.assertTrue(run["id"])

    def test_a_dataset_without_a_model_says_so_instead_of_inventing_one(self):
        s, b = self.ask("Which variables are useful for prediction?", [self.air["id"]])
        self.assertFalse([e for e in b["evidence"] if e["origin"] == "model_prediction"])
        notes = " ".join(b["report"]["dataset"]["notes"])
        self.assertIn("no trained model", notes)
        self.assertTrue(any(g["subtopic"] for g in b["gaps"]))


class TwoDatasets(DatasetResearchBase):
    def test_two_datasets_are_compared_with_their_real_sizes_and_means(self):
        s, b = self.ask("Compare the two datasets", [self.air["id"], self.air23["id"]])
        quotes = [e["quote"] for e in self.calc(b)]
        size = next(q for q in quotes if q.startswith("airline.csv has") and "airline_2023.csv has" in q)
        self.assertIn(f"{len(self.air_df)} rows", size)
        self.assertIn(f"{len(self.air23_df)} rows", size)
        self.assertIn("share 8 column name(s)", size)
        col = next(q for q in quotes if q.startswith("'price': mean") and "airline_2023.csv" in q)
        self.assertTrue(close(numbers(col), float(self.air_df["price"].mean())))
        self.assertTrue(close(numbers(col), float(self.air23_df["price"].mean())))
        self.assertIn("not a test", col)
        self.assertEqual({e["subtopic"] for e in self.calc(b) if "share 8" in e["quote"]}, {next(st["id"] for st in b["plan"]["subtopics"] if st.get("intent") == "compare_datasets")})

    def test_a_single_dataset_cannot_be_compared_and_the_plan_says_so(self):
        s, b = self.ask("Compare the two datasets", [self.air["id"]])
        self.assertTrue(any("only one is selected" in x for x in b["plan"]["limitations"]), b["plan"]["limitations"])
        self.assertFalse([e for e in self.calc(b) if "not a test" in e["quote"]])                 # nothing is compared when there is nothing to compare with


class LargeData(DatasetResearchBase):
    def test_a_dataset_too_large_for_one_run_is_sampled_and_the_sample_is_stated(self):
        real = self.ws.analytics.run
        calls = []

        def run(principal, asset_id, **kw):
            calls.append(kw.get("sample_rows"))
            if kw.get("sample_rows") is None:
                raise AppError("ANALYSIS_TOO_LARGE", "This is too large to analyse in one run.", 413)
            return real(principal, asset_id, **kw)
        self.ws.analytics.run = run
        try:
            q = "What does this dataset tell us?"
            plan = build_plan(q, "topic", web=False, providers=[], datasets=[self.air["id"]])
            out = DatasetResearcher(self.ws).analyse(self.alice, self.pid, plan, q, [self.air["id"]], lambda *a: None)
        finally:
            self.ws.analytics.run = real
        self.assertTrue(out["candidates"])
        self.assertTrue(any(c is not None for c in calls) and calls[0] is None)
        self.assertTrue(any("random sample" in n for n in out["notes"]))
        self.assertTrue(all(c["rows_used"] <= 100_000 for c in out["calculations"]))


class AccessAndPrivacy(DatasetResearchBase):
    def test_another_persons_dataset_cannot_be_named_in_a_research_session(self):
        with self.assertRaises(AppError) as cm:
            self.svc.create_session(self.bob, self.bob_pid, {"question": "What does this dataset tell us?", "options": {"datasets": [self.air["id"]]}})
        self.assertEqual(cm.exception.status_code, 404)

    def test_a_dataset_from_another_of_my_projects_cannot_be_used_here(self):
        other = self.ws.projects.create(self.alice, "Other")["id"]
        foreign = upload(self.ws, self.alice, other, "elsewhere.csv", sup.csv_of(airline_frame(60, seed=3)))["asset"]
        with self.assertRaises(AppError):
            self.svc.create_session(self.alice, self.pid, {"question": "What does this dataset tell us?", "options": {"datasets": [foreign["id"]]}})

    def test_the_researcher_itself_refuses_what_the_caller_may_not_open(self):
        q = "What does this dataset tell us?"
        plan = build_plan(q, "topic", web=False, providers=[], datasets=[self.air["id"]])
        out = DatasetResearcher(self.ws).analyse(self.bob, self.bob_pid, plan, q, [self.air["id"]], lambda *a: None)
        self.assertEqual((out["candidates"], out["calculations"]), ([], []))
        self.assertTrue(out["notes"])
        out2 = DatasetResearcher(self.ws).analyse(self.alice, self.bob_pid, plan, q, [self.air["id"]], lambda *a: None)       # right person, wrong project
        self.assertEqual(out2["candidates"], [])

    def test_another_person_cannot_read_the_bundle_that_holds_the_calculations(self):
        s, b = self.ask("What does this dataset tell us?", [self.air["id"]])
        with self.assertRaises(AppError):
            self.svc.get_bundle(self.bob, b["id"])
        with self.assertRaises(AppError):
            self.svc.inspect_citation(self.bob, b["id"], b["evidence"][0]["id"])

    def test_no_row_value_or_free_text_from_the_data_reaches_the_bundle(self):
        s, b = self.ask("What does this dataset tell us about missing values and outliers?", [self.air["id"]])
        blob = json.dumps(b)
        self.assertNotIn(CANARY, blob)
        self.assertNotIn("AI0001", blob)
        self.assertFalse(b["web"]["used"])

    def test_a_dataset_question_needs_no_approval_and_sends_nothing_outside(self):
        s = self.svc.create_session(self.alice, self.pid, {"question": "What does this dataset tell us?", "options": {"datasets": [self.air["id"]]}})
        self.assertFalse(s["needs_approval"])
        self.assertEqual(s["web"]["will_send"], [])
        self.assertTrue(all(st["scope"] == "dataset" for st in s["plan"]["subtopics"]))
        self.assertIn("no data values leave the platform", " ".join(t["description"] for t in s["plan"]["tasks"]))


class Integrity(DatasetResearchBase):
    def test_every_calculated_statement_is_rebuilt_from_its_stored_result_and_matches(self):
        s, b = self.ask("What factors are associated with bought?", [self.cust["id"]])
        check = next(c for c in b["quality"]["checks"] if c["id"] == "numerical_accuracy")
        self.assertEqual(check["status"], "pass", check)
        n = len([e for e in b["evidence"] if (e["citation"] or {}).get("calc")])
        self.assertGreater(n, 0)
        self.assertIn(f"All {n} calculated statements", check["detail"])
        self.assertIn("No language model wrote any number", check["detail"])
        for e in self.calc(b):
            if (e["citation"].get("kind") == "calculation"):
                res = self.svc.inspect_citation(self.alice, b["id"], e["id"])
                self.assertTrue(res["verification"]["verified"], res["verification"])
                self.assertEqual(res["open"]["type"], "analysis_run")
                self.assertEqual(res["calculation"]["run_id"], e["citation"]["run_id"])

    def test_a_stored_result_edited_after_the_fact_fails_the_citation_check(self):
        s, b = self.ask("Which columns have missing or suspicious values?", [self.air["id"]])
        ev = next(e for e in self.calc(b) if e["citation"].get("kind") == "calculation" and "missing" in e["quote"] and "%" in e["quote"])
        run_id, asset_id = ev["citation"]["run_id"], ev["citation"]["asset_id"]
        key = keys.analysis_key(self.pid, asset_id, run_id)
        doc = json.loads(self.ws.storage.read_bytes(key).decode("utf-8"))
        for r in doc["results"]:
            if r["op"] == "missing_values":
                for c in r["data"]["columns"]:
                    if c["column"] == "passengers":
                        c["pct"] = 1.5                                                           # the hash field is left as it was: only the numbers are forged
        self.ws.storage.put(key, [json.dumps(doc).encode("utf-8")], overwrite=True)
        res = self.svc.inspect_citation(self.alice, b["id"], ev["id"])
        self.assertFalse(res["verification"]["verified"])
        self.assertIn("different statement", res["verification"]["reason"])

    def test_deleting_a_dataset_takes_the_research_that_rests_on_it_with_it(self):
        temp = upload(self.ws, self.alice, self.pid, "temp.csv", sup.csv_of(airline_frame(80, seed=5)))["asset"]
        s, b = self.ask("What does this dataset tell us?", [temp["id"]])
        ev = next(e for e in self.calc(b) if e["citation"].get("kind") == "calculation")
        self.assertTrue(self.svc.inspect_citation(self.alice, b["id"], ev["id"])["verification"]["verified"])
        self.ws.assets.delete(self.alice, temp["id"])
        with self.assertRaises(AppError) as cm:                                                  # the calculations, their citations and the bundle are purged with the data
            self.svc.inspect_citation(self.alice, b["id"], ev["id"])
        self.assertEqual(cm.exception.status_code, 404)


class FollowUps(DatasetResearchBase):
    def test_a_follow_up_keeps_the_datasets_and_names_the_question_it_builds_on(self):
        s, b = self.ask("What does this dataset tell us?", [self.air["id"]])
        f = self.svc.create_follow_up(self.alice, s["id"], {"question": "And which columns have missing values?"})
        self.assertEqual(f["options"]["datasets"], [self.air["id"]])
        self.assertEqual(f["parent"]["of"], s["id"])
        self.assertEqual(f["plan"]["local"]["reuse_sources_of"], s["id"])
        job = self.svc.run(self.alice, f["id"], None)
        self.runtime.runner.wait(job["id"], 120)
        done = self.svc.job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed")
        fb = self.svc.get_bundle(self.alice, done["result_id"])["bundle"]
        self.assertIn("'passengers'", " ".join(e["quote"] for e in self.calc(fb)))

    def test_a_follow_up_cannot_be_attached_to_someone_elses_session_or_an_unrun_one(self):
        s = self.svc.create_session(self.alice, self.pid, {"question": "What does this dataset tell us?", "options": {"datasets": [self.air["id"]]}})
        with self.assertRaises(AppError):
            self.svc.create_follow_up(self.alice, s["id"], {"question": "And what else?"})       # not run yet: nothing to build on
        s2, _ = self.ask("What does this dataset tell us?", [self.air["id"]])
        with self.assertRaises(AppError):
            self.svc.create_follow_up(self.bob, s2["id"], {"question": "And what else?"})
        with self.assertRaises(AppError):
            self.svc.create_session(self.bob, self.bob_pid, {"question": "What else is there?", "options": {"parent_session_id": s2["id"]}})


if __name__ == "__main__":
    unittest.main()
