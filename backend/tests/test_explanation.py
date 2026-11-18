"""The universal Explanation system: present on every major output, grounded in the real calculation, technical results untouched,
unsupported claims refused, honest when it cannot explain."""
import copy
import json
import re
import unittest

import analytics_support as sup
from app.modules.explanation import SCHEMA, TITLE, Draft, Say, Unverifiable, explain, explain_op
from app.modules.explanation import core
from app.modules.analytics.ops import REGISTRY
from test_analytics_service import ServiceBase

ALL_OPS = [
    ("dataset_summary", {}), ("data_quality", {}), ("missing_values", {}), ("column_stats", {"columns": ["age", "income"]}), ("frequency", {"column": "region"}),
    ("distribution_compare", {"measure": "income", "group": "region"}), ("aggregate", {"agg": "mean", "measure": "income", "group_by": ["region"]}),
    ("correlation", {"columns": ["age", "income", "spend"]}), ("group_compare", {"measure": "income", "group": "region"}),
    ("contingency", {"row": "region", "column": "segment"}), ("discover", {"top_k": 5}), ("trend", {"date": "signup", "measure": "income"}),
    ("temporal_correlation", {"date": "signup", "first": "income", "second": "spend"}),
    ("hypothesis", {"kind": "compare_groups", "outcome": "income", "group": "region"}), ("hypothesis", {"kind": "association", "outcome": "segment", "group": "region"}),
    ("hypothesis", {"kind": "correlation", "outcome": "age", "second": "income"}), ("hypothesis", {"kind": "correlation", "outcome": "age", "second": "income", "execute": False}),
    ("chart", {"type": "histogram", "x": "age"}), ("chart", {"type": "bar", "x": "region", "y": "income", "agg": "mean"}), ("chart", {"type": "scatter", "x": "age", "y": "income"}),
    ("chart", {"type": "heatmap", "kind": "correlation", "columns": ["age", "income", "spend"]}), ("chart", {"type": "timeseries", "x": "signup", "y": "income"}),
    ("mixed_association", {"x": "age", "y": "income"}), ("mixed_association", {"x": "region", "y": "income"}), ("mixed_association", {"x": "region", "y": "segment"}),
]


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings_in(v)


def run(df, op, params):
    return sup.run_engine(df, [{"op": op, "params": params}], dates=("signup",))[1]["results"][0]


class EveryOperationIsExplainedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = sup.synth()

    def test_every_registered_operation_has_an_explainer(self):
        missing = [name for name in REGISTRY if f"op:{name}" not in core.REGISTRY]
        self.assertEqual(missing, [])

    def test_each_operation_result_gets_a_titled_available_explanation(self):
        for op, params in ALL_OPS:
            res = run(self.df, op, params)
            self.assertEqual(res["status"], "ok", (op, params, res.get("error")))
            ex = explain_op(res)
            self.assertEqual(ex["title"], TITLE, op)
            self.assertEqual(ex["schema"], SCHEMA)
            self.assertEqual(ex["status"], "available", (op, params, ex.get("reason")))
            self.assertTrue(ex["summary"])
            self.assertLessEqual(len(ex["points"]), 6)

    def test_title_is_exactly_explanation(self):
        self.assertEqual(TITLE, "Explanation")
        self.assertEqual(explain("nothing", {})["title"], "Explanation")

    def test_explanations_are_deterministic(self):
        res = run(self.df, "hypothesis", {"kind": "compare_groups", "outcome": "income", "group": "region"})
        self.assertEqual(explain_op(res), explain_op(copy.deepcopy(res)))


class ExplanationReflectsTheRealCalculationTests(unittest.TestCase):
    def setUp(self):
        self.df = sup.synth()

    def text(self, ex):
        return " ".join([ex["summary"], *ex["points"], *ex["cautions"]])

    def test_correlation_text_carries_the_computed_coefficient_and_sample_size(self):
        res = run(self.df, "correlation", {"columns": ["age", "income"]})
        pair = res["data"]["pairs"][0]
        rho = pair["spearman"]["rho"]
        text = self.text(explain_op(res))
        self.assertIn(f"{rho:.3f}".rstrip("0"), text)
        self.assertIn(str(pair["n"]), text)
        self.assertIn("Spearman rho", text)                       # the technical term is kept

    def test_group_test_text_matches_p_effect_and_direction_of_the_decision(self):
        res = run(self.df, "hypothesis", {"kind": "association", "outcome": "segment", "group": "region"})
        ex = explain_op(res)
        text = self.text(ex)
        self.assertIn("p < 0.001", text)
        self.assertIn(f"{res['data']['result']['effect_size']['value']:.3f}".rstrip("0"), text)
        self.assertIn("statistically significant", text)
        quiet = run(self.df, "hypothesis", {"kind": "compare_groups", "outcome": "income", "group": "region"})
        self.assertIn("not below the chosen threshold", self.text(explain_op(quiet)))

    def test_changing_the_result_changes_the_explanation_not_the_other_way_round(self):
        res = run(self.df, "frequency", {"column": "region"})
        before = explain_op(res)
        tampered = copy.deepcopy(res)
        tampered["data"]["rows"][0]["count"] = 4321
        tampered["data"]["rows"][0]["pct"] = 99.9
        after = explain_op(tampered)
        self.assertNotEqual(before["summary"], after["summary"])
        self.assertIn("4,321", after["summary"])
        self.assertNotIn("4,321", before["summary"])

    def test_technical_terms_and_definitions_are_kept(self):
        ex = explain_op(run(self.df, "hypothesis", {"kind": "correlation", "outcome": "age", "second": "income"}))
        terms = {t["term"] for t in ex["terms"]}
        self.assertTrue({"p-value", "effect size", "confidence interval"} <= terms)
        self.assertIn("confidence interval", self.text(ex))

    def test_every_figure_in_every_explanation_is_in_the_result(self):
        for op, params in ALL_OPS:
            res = run(self.df, op, params)
            ex = explain_op(res)
            leaves = core.Leaves(res)
            literal_free = re.sub(r"'[^']*'", " ", self.text(ex))
            texts = list(strings_in(res))
            for tok in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", literal_free):
                plain = tok.replace(",", "")
                value, places = float(plain), (len(plain.split(".")[1]) if "." in plain else 0)
                # a figure in the text must be a result value shown at this precision (or a percentage/share of one), or a stated convention
                known = (any(abs(round(v, places) - value) < 1e-9 or abs(round(v * 100, places) - value) < 1e-9 or abs(round(abs(v), places) - abs(value)) < 1e-9 for v in leaves.values)
                         or value in (0.05, 0.001, 95.0) or any(plain in t for t in texts)        # numbers inside sentences the engine itself wrote
                         or (float(value).is_integer() and 0 <= value <= len(leaves.values)))        # counts of listed items (groups, pairs, issues)
                self.assertTrue(known, (op, tok))


class UnsupportedClaimsAreRefusedTests(unittest.TestCase):
    def test_a_number_that_is_not_in_the_result_cannot_be_written(self):
        say = Say({"data": {"n": 10}})
        with self.assertRaises(Unverifiable):
            say.count(11)
        with self.assertRaises(Unverifiable):
            say.num(0.5)
        self.assertEqual(say.count(10), "10")

    def test_a_hard_coded_number_in_the_prose_fails_the_whole_explanation(self):
        def rogue(say, result):
            return Draft(f"There are {say.count(result['n'])} rows and a 73% chance of success.")
        core.REGISTRY["test:rogue"] = rogue
        try:
            ex = explain("test:rogue", {"n": 5})
        finally:
            del core.REGISTRY["test:rogue"]
        self.assertEqual(ex["status"], "unavailable")
        self.assertIsNone(ex["summary"])
        self.assertIn("could not be matched", ex["reason"])

    def test_an_explainer_that_cites_an_unknown_figure_makes_the_explanation_unavailable(self):
        def rogue(say, result):
            return Draft(f"There are {say.count(999)} rows.")
        core.REGISTRY["test:rogue2"] = rogue
        try:
            ex = explain("test:rogue2", {"n": 5})
        finally:
            del core.REGISTRY["test:rogue2"]
        self.assertEqual(ex["status"], "unavailable")

    def test_digits_inside_column_names_are_not_mistaken_for_claims(self):
        df = sup.synth().rename(columns={"age": "age_2024", "income": "q3_income"})
        res = run(df, "mixed_association", {"x": "age_2024", "y": "q3_income"})
        ex = explain_op(res)
        self.assertEqual(ex["status"], "available", ex.get("reason"))
        self.assertIn("age_2024", ex["summary"])

    def test_a_name_taken_from_the_data_hides_only_itself_not_part_of_a_longer_number(self):
        """Regression: a column called "0.2" used to mask the 5 in the figure 0.25, so a valid explanation was rejected."""
        def ok(say, result):
            return Draft(f"Column '{say.lit('0.2')}' holds {say.num(result['v'], 2)}.")

        def rogue(say, result):                                  # cites 0.2 only as a name, then writes an unaccounted 0.25
            return Draft(f"Column '{say.lit('0.2')}' scored 0.25.")
        core.REGISTRY["test:ok"], core.REGISTRY["test:rogue3"] = ok, rogue
        try:
            good = explain("test:ok", {"v": 0.25})
            bad = explain("test:rogue3", {"v": 7})
        finally:
            del core.REGISTRY["test:ok"], core.REGISTRY["test:rogue3"]
        self.assertEqual(good["status"], "available", good.get("reason"))
        self.assertIn("0.25", good["summary"])
        self.assertEqual(bad["status"], "unavailable")

    def test_explainers_cannot_alter_the_result(self):
        res = run(sup.synth(), "hypothesis", {"kind": "association", "outcome": "segment", "group": "region"})
        snapshot = json.dumps(res, sort_keys=True)
        explain_op(res)
        self.assertEqual(json.dumps(res, sort_keys=True), snapshot)


class HonestWhenItCannotExplainTests(unittest.TestCase):
    def test_unknown_kinds_say_so(self):
        ex = explain("op:does_not_exist", {"data": {}})
        self.assertEqual(ex["status"], "unavailable")
        self.assertIn("No plain-English explanation", ex["reason"])

    def test_malformed_results_do_not_raise(self):
        for bad in ({}, {"data": None}, {"data": {"rows": "x"}}, {"data": {"columns": 3}}):
            for name in ("op:frequency", "op:column_stats", "op:hypothesis", "op:correlation"):
                ex = explain(name, bad)
                self.assertEqual(ex["status"], "unavailable", (name, bad))
                self.assertEqual(ex["title"], TITLE)

    def test_failed_and_not_run_operations_are_explained_without_inventing_findings(self):
        df = sup.synth()
        failed = run(df, "mixed_association", {"x": "signup", "y": "income"})
        ex = explain_op(failed)
        self.assertEqual(ex["status"], "available")
        self.assertIn("did not complete", ex["summary"])
        small = run(df.head(6), "mixed_association", {"x": "age", "y": "spend"})
        ex2 = explain_op(small)
        self.assertEqual(small["status"], "not_run")
        self.assertIn("not enough evidence", ex2["summary"].lower())


class ExplanationsAcrossModulesTests(ServiceBase):
    def test_stored_runs_carry_explanations_on_every_result_and_hashes_are_unchanged(self):
        run_ = self.run_ops([{"op": "dataset_summary", "params": {}}, {"op": "correlation", "params": {"columns": ["age", "income"]}}], kind="relationships")
        for r in run_["result"]["results"]:
            self.assertEqual(r["explanation"]["title"], "Explanation")
            self.assertEqual(r["explanation"]["status"], "available")
        stored = json.loads(self.h.storage.read_bytes(self.an.repo.run(self.alice.user_id, run_["id"])["result_key"]))
        self.assertNotIn("explanation", stored["results"][0])               # explanations are computed on read, never mixed into the stored calculation
        again = self.an.rerun(self.alice, run_["id"])
        self.assertTrue(again["reproduction"]["reproduced"])

    def test_the_result_hash_does_not_depend_on_the_explanation(self):
        from app.modules.analytics.evidence import result_hash
        run_ = self.run_ops([{"op": "frequency", "params": {"column": "region"}}])
        res = run_["result"]["results"]
        h1 = result_hash(run_["plan_hash"], res, run_["rows_used"])
        stripped = [{k: v for k, v in r.items() if k != "explanation"} for r in res]
        self.assertEqual(h1, result_hash(run_["plan_hash"], stripped, run_["rows_used"]))
        self.assertEqual(h1, run_["result_hash"])

    def test_exported_results_include_the_explanation_beside_the_numbers(self):
        run_ = self.run_ops([{"op": "frequency", "params": {"column": "region"}}])
        _name, _ctype, body = self.an.export(self.alice, run_["id"], what="result")
        doc = json.loads(body)
        self.assertEqual(doc["result"]["results"][0]["explanation"]["title"], "Explanation")
        self.assertIn("rows", doc["result"]["results"][0]["data"])

    def test_insights_carry_an_explanation(self):
        out = self.ws.insights.generate(self.alice, self.asset["id"])
        self.assertTrue(out["insights"])
        kinds = set()
        for ins in out["insights"]:
            self.assertEqual(ins["explanation"]["title"], "Explanation", ins["category"])
            kinds.add((ins["category"], ins["explanation"]["status"]))
        for category, status in kinds:
            self.assertEqual(status, "available", category)

    def test_assistant_answers_carry_an_explanation_and_say_what_was_checked(self):
        out = self.ws.assistant.ask(self.alice, self.asset["id"], "What is the average income by region?")
        self.assertEqual(out["status"], "answered")
        ex = out["answer"]["explanation"]
        self.assertEqual(ex["title"], "Explanation")
        self.assertEqual(ex["status"], "available", ex.get("reason"))
        self.assertTrue(any("cross-check" in p or "independent" in p for p in ex["points"]))
        convo = self.ws.assistant.conversation(self.alice, out["conversation_id"])
        self.assertEqual(convo["messages"][-1]["payload"]["explanation"]["title"], "Explanation")

    def test_dataset_profiles_carry_an_explanation(self):
        out = self.ws.profiles.generate(self.alice, self.asset["id"])
        self.assertEqual(out["explanation"]["title"], "Explanation")
        self.assertEqual(out["explanation"]["status"], "available", out["explanation"].get("reason"))
        self.assertEqual(self.ws.profiles.get(self.alice, self.asset["id"])["explanation"]["status"], "available")

    def test_another_users_run_is_still_a_404_and_leaks_no_explanation(self):
        from app.core.errors import AppError
        run_ = self.run_ops([{"op": "frequency", "params": {"column": "region"}}])
        with self.assertRaises(AppError) as cm:
            self.an.get_run(self.bob, run_["id"])
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
