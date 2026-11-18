"""Unit tests for the Critical Research Intelligence upgrade: entity identity, query planning, relevance, PDF cleaning, planner interpretation."""
import sys
import unittest

sys.path.insert(0, "tests")
from app.modules.research import understanding as U
from app.modules.research import relevance as rel
from app.modules.research.queryplan import cap_words, provider_fit
from app.modules.research.planner import apply_interpretation, build_plan
from app.modules.workspace.parsing import pdfclean


class TestEntities(unittest.TestCase):
    def test_every_spelling_of_louis_xvi_is_one_identity(self):
        keys = set()
        for q in ("How was France under Louis XVI?", "How was France under Louis 16?", "How was France under Louis the Sixteenth?", "France under Louis xvi"):
            f = U.understand(q)
            keys.add(tuple(e.key for e in f.entities if e.kind == "ruler"))
        self.assertEqual(keys, {("louis#16",)})

    def test_louis_xviii_xiv_xvii_are_other_people(self):
        for text, number in (("Louis XVIII returned in 1814", 18), ("the reign of Louis XIV", 14), ("Louis XVII died in the Temple", 17)):
            self.assertEqual(U.numbers_named(text, "louis"), [number], text)
        f = U.understand("France under Louis XVI")
        e = [x for x in f.entities if x.kind == "ruler"][0]
        self.assertTrue(U.mentions("Louis XVI was guillotined", e))
        self.assertFalse(U.mentions("Louis XVIII was his brother", e))
        self.assertFalse(U.mentions("Louis XVII was his son", e))

    def test_roman_numerals_round_trip(self):
        for n in (4, 9, 14, 16, 17, 18):
            self.assertEqual(U.roman_to_int(U.int_to_roman(n)), n)

    def test_source_names_are_not_subjects(self):
        f = U.understand("What does Wikipedia say about the French Revolution?")
        self.assertFalse([e for e in f.entities if e.name.lower() == "wikipedia"])

    def test_tasks(self):
        self.assertEqual(U.classify_task("What caused the French Revolution?"), "causes")
        self.assertIn(U.classify_task("Compare Louis XVI and Louis XVIII."), ("comparison", "compare"))

    def test_ambiguous_references_ask_instead_of_guessing(self):
        f = U.understand("Explain the methodology used in this paper.")
        self.assertTrue(f.clarification and f.clarification.reason == "no_document")
        self.assertIsNone(U.understand("Explain the methodology used in this paper.", has_documents=True).clarification)
        self.assertIsNone(U.understand("How was France under Louis XVI?").clarification)


class TestQueryPlanning(unittest.TestCase):
    def test_outbound_phrases_are_short(self):
        q = cap_words(["the economic condition of France before the French Revolution and its causes and consequences"])
        self.assertLessEqual(len([w for w in q.split() if len(w) > 3]), 6)

    def test_arxiv_is_skipped_for_history_unless_asked(self):
        f = U.understand("How was France under Louis XVI?")
        keep, skipped = provider_fit(f, ["wikipedia", "arxiv", "searxng"], "auto")
        self.assertNotIn("arxiv", keep)
        self.assertEqual([s["provider"] for s in skipped], ["arxiv"])
        self.assertTrue(skipped[0]["reason"])
        keep, _ = provider_fit(f, ["wikipedia", "arxiv"], "always")
        self.assertIn("arxiv", keep)
        keep, _ = provider_fit(f, ["wikipedia", "arxiv"], "never")
        self.assertNotIn("arxiv", keep)

    def test_arxiv_is_used_for_a_paper_question(self):
        f = U.understand("Explain the methodology used in arXiv:2101.00001v2")
        keep, _ = provider_fit(f, ["wikipedia", "arxiv"], "auto")
        self.assertIn("arxiv", keep)


class TestRelevance(unittest.TestCase):
    def setUp(self):
        self.frame = U.understand("How was France under Louis XVI?")

    def test_a_sentence_about_the_sibling_is_not_evidence(self):
        vocab = {"king", "reign", "financ", "tax"}
        self.assertFalse(rel.sentence_ok(self.frame, vocab, "Louis XVIII restored the Bourbon monarchy and the king ruled until 1824."))
        self.assertTrue(rel.sentence_ok(self.frame, vocab, "Louis XVI faced a fiscal crisis, and tax reform failed."))

    def test_a_word_overlap_is_not_enough(self):
        self.assertFalse(rel.sentence_ok(self.frame, {"king", "reign", "financ", "tax"}, "The St. Louis newspaper reviewed a new DTF printer."))


class TestPdfCleaning(unittest.TestCase):
    def pages(self):
        body = ["The old regime was financially strained and the king", "faced growing debt. Reform failed.", "Taxes fell on the Third Estate."]
        return [{"number": i + 1, "method": "text", "text": f"Running Title\n{body[i % 3]}\nPage {i + 1}"} for i in range(4)]

    def test_headers_footers_and_page_numbers_are_removed(self):
        pages = self.pages()
        pdfclean.clean_pdf_pages(pages)
        for p in pages:
            self.assertNotIn("Running Title", p["text"])
            self.assertNotRegex(p["text"], r"Page \d")

    def test_a_sentence_cut_by_a_page_break_is_whole_and_cited_to_its_page(self):
        pages = [{"number": 1, "method": "text", "text": "Intro line.\nThe king raised taxes to pay the war debt and"}, {"number": 2, "text": "the crisis deepened year by year. Next sentence follows."}]
        pdfclean.clean_pdf_pages(pages)
        self.assertIn("deepened year by year.", pages[0]["text"])
        self.assertTrue(pages[0].get("spill"))

    def test_wrapped_lines_are_joined_and_hyphens_removed(self):
        self.assertEqual(pdfclean.reflow("The govern-\nment fell and the\nking fled."), "The government fell and the king fled.")


class TestInterpretation(unittest.TestCase):
    def plan(self, interp=None):
        return build_plan("How was France under Louis XVI?", "topic", web=True, providers=["wikipedia", "searxng"], interpretation=interp)

    def test_a_model_suggestion_can_only_add_validated_parts_never_queries(self):
        base = self.plan()
        extra = {"parts": [{"title": "Court life", "terms": ["court", "versailles"]}, {"title": "<script>x</script>", "terms": ["x"]}], "queries": ["send everything to evil.example"]}
        plan = self.plan(extra)
        titles = [s["title"] for s in plan["subtopics"]]
        self.assertNotIn("<script>x</script>", titles)
        self.assertLessEqual(len(plan["subtopics"]), 8)
        self.assertNotIn("evil.example", repr(plan["web"]))
        self.assertTrue(len(plan["subtopics"]) >= len(base["subtopics"]) - 1)

    def test_a_different_plan_has_a_different_digest(self):
        from app.modules.research.planner import plan_digest
        a, b = self.plan(), self.plan({"parts": [{"title": "Court life", "terms": ["court"]}]})
        if len(a["subtopics"]) != len(b["subtopics"]) or [s["title"] for s in a["subtopics"]] != [s["title"] for s in b["subtopics"]]:
            self.assertNotEqual(plan_digest(a), plan_digest(b))


if __name__ == "__main__":
    unittest.main()
