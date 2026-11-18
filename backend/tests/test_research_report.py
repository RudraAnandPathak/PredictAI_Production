"""Rules for the combined report, the follow-up plan and the dataset-question test, with no database or network: plain inputs, exact outputs."""
import unittest

from app.modules.research.intents import asks_about_data
from app.modules.research.planner import build_plan
from app.modules.research.report import MAX_COMPARE, SEPARATION_NOTE, build_comparison, build_report
from app.modules.textkit import tokenize

WIKI = {"id": "S2", "kind": "web", "provider": "wikipedia", "origin": "external_research", "status": "ok", "title": "Business class"}
PAPER = {"id": "S3", "kind": "web", "provider": "arxiv", "origin": "external_research", "status": "ok", "title": "A paper", "meta": {}}
PAGE = {"id": "S4", "kind": "web", "provider": "searxng", "origin": "external_research", "status": "ok", "title": "A page"}
DATA = {"id": "S1", "kind": "dataset", "origin": "calculated", "status": "ok", "title": "Calculations on airline.csv"}


def calc(eid, text, **facts):
    return {"id": eid, "source": "S1", "origin": "calculated", "quote": text, "facts": {"dataset": "airline.csv", **facts}, "tokens": tokenize(text), "flags": {}, "score": 1.0, "location": {}, "citation": {}}


def ext(eid, source, text, **flags):
    return {"id": eid, "source": source, "origin": "external_research", "quote": text, "tokens": tokenize(text), "flags": flags, "score": 1.0, "location": {}, "citation": {}}


def concl(cid, evidence, sub, **kw):
    return {"id": cid, "text": f"text of {cid}", "evidence": evidence, "sources": ["S1"], "kind": "computed", "confidence": "calculated", "caveats": [], "subtopic": sub, **kw}


PLAN = {"subtopics": [{"id": "d1", "title": "Relationships", "scope": "dataset", "intent": "relationships"}, {"id": "t1", "title": "Answer", "scope": "general"}]}
NO_COLLECTION = {"searches": [], "failures": [], "dropped": []}


class Comparison(unittest.TestCase):
    SRC = {s["id"]: {**s, "source_label": f"label of {s['id']}"} for s in (DATA, WIKI, PAPER, PAGE)}

    def test_a_passage_naming_both_variables_is_a_closer_match_than_one_naming_a_single_variable(self):
        c = calc("E1", "Higher price is associated with higher seats_available.", x="price", y="seats_available", direction="positive")
        both = ext("E2", "S2", "Price and seats available move together on most routes.")
        one = ext("E3", "S3", "Seats available are limited on busy routes.")
        rows = build_comparison([c, both, one], self.SRC)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation"], "same_variables")
        self.assertEqual([i["evidence"] for i in rows[0]["external"]], ["E2", "E3"])
        self.assertEqual(rows[0]["calculated"], {"evidence": "E1", "text": c["quote"], "dataset": "airline.csv"})
        self.assertIn("by words, not by meaning", rows[0]["note"])
        self.assertEqual(rows[0]["external"][0]["label"], "label of S2")

    def test_direction_is_judged_from_wording_only_and_a_negated_passage_is_not_read_as_a_direction(self):
        c = calc("E1", "Higher price goes with higher seats_available.", x="price", y="seats_available", direction="positive")
        same = build_comparison([c, ext("E2", "S2", "Price and seats available both rise.", direction="up")], self.SRC)
        other = build_comparison([c, ext("E2", "S2", "Price and seats available move in opposite ways, one falls.", direction="down")], self.SRC)
        neg = build_comparison([c, ext("E2", "S2", "Price and seats available do not rise together.", direction="up", negated=True)], self.SRC)
        self.assertEqual(same[0]["external"][0]["alignment"], "similar_wording")
        self.assertIn("points the same way", same[0]["note"])
        self.assertEqual(other[0]["external"][0]["alignment"], "different_wording")
        self.assertIn("other way", other[0]["note"])
        self.assertIsNone(neg[0]["external"][0]["alignment"])                                        # "do not rise" is not read as "rises"

    def test_a_single_shared_variable_is_only_the_same_subject_and_no_direction_is_claimed(self):
        c = calc("E1", "Higher price goes with higher seats_available.", x="price", y="seats_available", direction="positive")
        rows = build_comparison([c, ext("E2", "S2", "Seats available are limited.", direction="down")], self.SRC)
        self.assertEqual(rows[0]["relation"], "same_subject")
        self.assertIsNone(rows[0]["external"][0]["alignment"])
        self.assertNotIn("same way", rows[0]["note"])

    def test_nothing_is_listed_without_both_a_calculated_and_an_outside_passage_that_share_a_variable(self):
        c = calc("E1", "Mean of delay_minutes is 12.", column="delay_minutes")
        self.assertEqual(build_comparison([c], self.SRC), [])
        self.assertEqual(build_comparison([ext("E2", "S2", "Delay is common.")], self.SRC), [])
        self.assertEqual(build_comparison([c, ext("E2", "S2", "Fares are high in business class.")], self.SRC), [])
        self.assertEqual(build_comparison([{**c, "facts": {}}, ext("E2", "S2", "Delay minutes are common.")], self.SRC), [])
        self.assertEqual(build_comparison([ext("E2", "S2", "Delay minutes are common.")], self.SRC), [])

    def test_the_list_is_capped_and_an_outside_passage_never_replaces_the_calculated_text(self):
        calcs = [calc(f"E{i}", f"Mean of col{i}x is {i}.", column=f"col{i}x") for i in range(1, 12)]
        outside = [ext(f"E{20 + i}", "S2", f"The col{i}x figure matters in aviation.") for i in range(1, 12)]
        rows = build_comparison(calcs + outside, self.SRC)
        self.assertEqual(len(rows), MAX_COMPARE)
        for r in rows:
            self.assertTrue(r["calculated"]["text"].startswith("Mean of col"))
            self.assertTrue(all(x["text"].startswith("The col") for x in r["external"]))


class Report(unittest.TestCase):
    def build(self, *, sources, evidence=(), conclusions=(), gaps=(), conflicts=(), notes=(), calculations=(), plan=PLAN, web=None, collection=NO_COLLECTION):
        return build_report(plan=plan, sources=list(sources), evidence=list(evidence), conclusions=list(conclusions), conflicts=list(conflicts), gaps=list(gaps), calculations=list(calculations),
                            dataset_notes=list(notes), collection=collection, web=web or {"requested": True, "used": True})

    def test_each_part_of_the_plan_gets_a_block_and_a_part_with_no_conclusion_says_so(self):
        c = concl("C1", ["E1"], "d1")
        r = self.build(sources=[DATA], evidence=[calc("E1", "x", x="a", y="b")], conclusions=[c], gaps=[{"subtopic": "t1", "title": "Answer", "note": "Nothing relevant was found for this part."}])
        self.assertEqual([(s["subtopic"], s["status"]) for s in r["sections"]], [("d1", "answered"), ("t1", "not_found")])
        self.assertEqual(r["sections"][1]["note"], "Nothing relevant was found for this part.")
        self.assertEqual([a["conclusion"] for a in r["answer"]], ["C1"])                            # "in short" holds only parts that have an answer
        self.assertTrue(any("Answer: Nothing relevant" in u for u in r["uncertainty"]))              # and what is missing is listed among the uncertainties

    def test_calculated_and_outside_material_are_kept_apart_and_the_separation_note_appears_only_when_both_are_present(self):
        both = self.build(sources=[DATA, WIKI], evidence=[calc("E1", "x", x="a", y="b"), ext("E2", "S2", "Something.")], conclusions=[concl("C1", ["E1"], "d1")])
        self.assertEqual(both["separation_note"], SEPARATION_NOTE)
        self.assertIn("never combined into one claim", both["separation_note"])
        data_only = self.build(sources=[DATA], evidence=[calc("E1", "x", x="a", y="b")], conclusions=[concl("C1", ["E1"], "d1")], web={"requested": False, "used": False})
        self.assertIsNone(data_only["separation_note"])
        web_only = self.build(sources=[WIKI], plan={"subtopics": [{"id": "t1", "title": "Answer", "scope": "general"}]})
        self.assertIsNone(web_only["separation_note"])

    def test_every_kind_of_outside_source_is_grouped_and_described_for_what_it_is(self):
        r = self.build(sources=[DATA, WIKI, PAPER, PAGE])
        self.assertEqual(r["external"]["by_class"], {"wikipedia": ["S2"], "arxiv_preprint": ["S3"], "web": ["S4"]})
        text = " ".join(r["uncertainty"])
        self.assertIn("encyclopedia", text)
        self.assertIn("preprints", text)
        self.assertIn("not been peer reviewed", text)

    def test_sources_that_could_not_be_read_in_full_are_counted_and_never_hidden(self):
        partial = {**PAGE, "id": "S5", "status": "snippet_only", "failure": "The page could not be opened."}
        failed = {**WIKI, "id": "S6", "status": "failed", "failure": "Wikipedia refused the request."}
        r = self.build(sources=[PAGE, partial, failed])
        self.assertEqual({u["source"] for u in r["external"]["unread"]}, {"S5", "S6"})
        text = " ".join(r["uncertainty"])
        self.assertIn("1 outside source(s) could only be read as the short summary", text)
        self.assertIn("2 outside source(s) could not be opened", text)

    def test_an_association_is_never_described_as_a_cause_and_a_sample_and_a_model_are_flagged(self):
        model = {"id": "S7", "kind": "model", "origin": "model_prediction", "status": "ok", "title": "Model"}
        r = self.build(sources=[DATA, model], notes=["A random sample of 100,000 rows was used for airline.csv."])
        text = " ".join(r["uncertainty"])
        self.assertIn("do not show that one thing causes another", text)
        self.assertIn("random sample", text)
        self.assertIn("estimates learned from past data", text)
        self.assertNotRegex(" ".join(a["text"] for a in r["answer"]) + SEPARATION_NOTE, r"\b(proves?|causes it|because of)\b")

    def test_with_data_and_outside_sources_but_no_shared_variable_the_report_says_it_cannot_compare(self):
        r = self.build(sources=[DATA, WIKI], evidence=[calc("E1", "Mean of delay_minutes is 12.", column="delay_minutes"), ext("E2", "S2", "Fares are high.")], conclusions=[concl("C1", ["E1"], "d1")])
        self.assertEqual(r["comparison"], [])
        self.assertTrue(any("cannot say whether outside sources agree or disagree" in u for u in r["uncertainty"]))

    def test_web_research_that_was_asked_for_but_not_run_is_reported_not_implied(self):
        r = self.build(sources=[DATA], evidence=[calc("E1", "x", x="a", y="b")], conclusions=[concl("C1", ["E1"], "d1")], web={"requested": True, "used": False})
        self.assertEqual((r["external"]["requested"], r["external"]["used"], r["external"]["present"]), (True, False, False))

    def test_dataset_calculations_are_listed_per_dataset_with_the_runs_behind_them(self):
        calcs = [{"asset_id": "A1", "dataset": "airline.csv", "run_id": "R1", "kind": "dashboard", "operations": ["dataset_summary"], "rows_used": 600, "evidence_status": "VERIFIED", "result_hash": "h1"},
                 {"asset_id": "A1", "dataset": "airline.csv", "run_id": "R2", "kind": "relationships", "operations": ["discover"], "rows_used": 600, "evidence_status": "VERIFIED", "result_hash": "h2"}]
        r = self.build(sources=[DATA], calculations=calcs)
        self.assertEqual(len(r["dataset"]["datasets"]), 1)
        self.assertEqual([c["run_id"] for c in r["dataset"]["datasets"][0]["calculations"]], ["R1", "R2"])
        self.assertTrue(r["dataset"]["present"])


class FollowUpPlans(unittest.TestCase):
    PARENT = {"session_id": "S0", "question": "How does the attention mechanism work in transformer networks?"}

    def test_a_follow_up_is_answered_as_asked_not_split_into_the_levels_fixed_outline(self):
        plain = build_plan("What results does the transformer paper report?", "technical", web=False, providers=[])
        follow = build_plan("What results does the transformer paper report?", "technical", web=False, providers=[], parent=self.PARENT)
        self.assertEqual(len(plain["subtopics"]), 4)
        self.assertEqual([s["title"] for s in follow["subtopics"]], ["Answer"])
        self.assertEqual(follow["local"]["reuse_sources_of"], "S0")
        self.assertEqual(follow["follow_up"]["question"], self.PARENT["question"])

    def test_a_follow_up_with_web_on_plans_its_own_phrases_for_approval(self):
        follow = build_plan("What results does the transformer paper report?", "technical", web=True, providers=["arxiv", "wikipedia"], parent=self.PARENT)
        self.assertTrue(follow["web"]["enabled"])
        self.assertTrue(follow["web"]["queries"])
        self.assertNotEqual(follow["plan_hash"], build_plan("What results does the transformer paper report?", "technical", web=False, providers=[], parent=self.PARENT)["plan_hash"])

    def test_a_follow_up_with_no_topic_words_of_its_own_inherits_the_earlier_topic_and_says_so(self):
        follow = build_plan("And what are its limitations?", "topic", web=True, providers=["wikipedia"], parent=self.PARENT)
        self.assertTrue(follow["follow_up"]["carried"])
        sent = " ".join(v["query"] for q in follow["web"]["queries"] for v in q["by_provider"]["wikipedia"]).lower()
        self.assertIn("attention", sent)                                                              # the earlier topic, not the bare pronoun, is what would be searched

    def test_datasets_are_left_out_of_a_follow_up_that_is_not_about_them_and_the_plan_says_so(self):
        parent = {**self.PARENT, "datasets_left_out": 1}
        plan = build_plan("What does the article say about fares?", "topic", web=False, providers=[], parent=parent)
        self.assertEqual([s["scope"] for s in plan["subtopics"]], ["general"])
        self.assertTrue(any("was not used again" in n for n in plan["limitations"]))
        self.assertEqual(plan["local"]["datasets"], [])

    def test_a_follow_up_that_asks_about_the_data_keeps_the_dataset_parts(self):
        plan = build_plan("Which columns in the dataset are most related to delay?", "topic", web=False, providers=[], datasets=["A1"], parent=self.PARENT)
        self.assertTrue(plan["subtopics"] and all(s["scope"] == "dataset" for s in plan["subtopics"]))
        self.assertEqual(plan["local"]["datasets"], ["A1"])


class AsksAboutData(unittest.TestCase):
    def test_questions_about_data_are_recognised_and_others_are_not(self):
        for q in ("What does this dataset tell us?", "Which columns are most related to delay?", "Are there missing values?", "Compare the two files", "What are the strongest relationships?",
                  "What variables are useful for prediction?", "Explain these statistical results"):
            self.assertTrue(asks_about_data(q), q)
        for q in ("What does the article say about fares?", "What results does the paper report?", "Who wrote it and when?", "", None):
            self.assertFalse(asks_about_data(q or ""), q)


if __name__ == "__main__":
    unittest.main()
