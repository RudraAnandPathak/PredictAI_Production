"""How a question becomes search phrases, and how a plan lays out the parts of a question. Pure functions: no network, no database.

Root cause covered here (see docs/phase6/CORRECTIVE_REPORT.md): Phase 6 sent one pile of loose keywords, plus filler such as "background history definition", to every provider. Wikipedia then
ranked pages that merely contain those words, and arXiv received `all:a AND all:b AND ...` with six or more ANDed terms, which almost never matches. These tests pin the replacement:
topic phrases only, each provider's own syntax, precise to broad, and nothing but the person's own words (no operators, no data values).
Run from backend/:  python -m unittest test_research_queries"""
import re
import unittest

from app.modules.research.planner import LEVELS, build_plan
from app.modules.webaccess import queries as Q

AIRLINE = "Which factors are most associated with business-class seat availability, and what do external aviation sources say about these patterns?"
ALL = ["wikipedia", "arxiv", "searxng"]


class TopicFocus(unittest.TestCase):
    def test_a_question_is_reduced_to_its_topic_and_not_to_its_scaffolding(self):
        f = Q.topic_focus(AIRLINE)
        self.assertEqual(f.phrases[0], "business-class seat availability")
        self.assertIn("aviation", f.phrases)
        for word in ("factors", "associated", "external", "sources", "say", "which", "what"):
            self.assertNotIn(word, f.terms, word)

    def test_how_does_x_work_keeps_x_and_drops_the_verb_being_asked_about(self):
        f = Q.topic_focus("How does a transformer neural network work?")
        self.assertEqual(f.phrases, ["transformer neural network"])
        self.assertNotIn("work", f.terms)

    def test_a_question_with_no_topic_of_its_own_yields_nothing_to_search_for(self):
        self.assertEqual(Q.topic_focus("What does this dataset tell us?").phrases, [])
        self.assertEqual(Q.topic_focus("").phrases, [])

    def test_words_that_only_point_at_the_persons_own_material_are_not_a_topic_alone_but_stay_inside_a_longer_phrase(self):
        self.assertEqual(Q.topic_focus("Please summarise this document").phrases, [])
        self.assertEqual(Q.topic_focus("summarise this file").phrases, [])
        self.assertEqual(Q.topic_focus("Explain the periodic table").phrases, ["periodic table"])
        self.assertEqual(Q.topic_focus("How do document databases scale?").phrases, ["document databases scale"])

    def test_a_number_with_a_decimal_point_is_kept_whole(self):
        self.assertEqual(Q.topic_focus("What is the 2.4 GHz band?").phrases, ["2.4 GHz band"])

    def test_the_number_of_phrases_and_terms_is_bounded(self):
        f = Q.topic_focus("alpha beta; gamma delta; epsilon zeta; eta theta; iota kappa; lambda mu; nu xi; omicron pi; rho sigma; tau upsilon; phi chi; psi omega", max_phrases=4)
        self.assertLessEqual(len(f.phrases), 4)
        self.assertLessEqual(len(f.terms), 12)
        self.assertLessEqual(len(f.natural), 140)

    def test_a_long_run_of_words_is_split_so_no_phrase_exceeds_five_words(self):
        f = Q.topic_focus("one two three four five six seven eight nine ten eleven")
        self.assertTrue(all(len(p.split()) <= Q.MAX_PHRASE_WORDS for p in f.phrases))


class ProviderQueries(unittest.TestCase):
    def setUp(self):
        self.focus = Q.topic_focus(AIRLINE)

    def test_wikipedia_is_asked_for_short_topic_names_most_specific_first(self):
        qs = Q.build_provider_queries("wikipedia", self.focus, AIRLINE)
        self.assertEqual(qs[0], {"query": "business-class seat availability", "role": "primary"})
        self.assertLessEqual(len(qs), 3)
        for q in qs:
            self.assertNotRegex(q["query"], r"[:\"()]")
            self.assertNotRegex(q["query"], r"\b(AND|OR)\b")

    def test_arxiv_searches_the_phrase_in_title_or_abstract_first_and_never_ands_a_pile_of_loose_words(self):
        qs = Q.build_provider_queries("arxiv", self.focus, AIRLINE)
        self.assertEqual(qs[0]["role"], "primary")
        self.assertIn('ti:"business-class seat availability"', qs[0]["query"])
        self.assertTrue(all(q["role"] == "fallback" for q in qs[1:]))
        self.assertLessEqual(len(qs), 3)
        for q in qs:
            self.assertLessEqual(q["query"].count(" AND "), 2, q["query"])            # the Phase 6 form ANDed six or more words and found nothing
        long_question = "Which statistical machine learning optimisation techniques improve forecasting accuracy for airline revenue management systems?"
        for q in Q.build_provider_queries("arxiv", Q.topic_focus(long_question), long_question):
            self.assertLessEqual(q["query"].count(" AND "), 2, q["query"])

    def test_nothing_but_plain_words_and_the_providers_own_field_names_can_reach_arxiv_or_wikipedia(self):
        hostile = 'seats; DROP TABLE users -- ti:evil OR all:x "quoted" (paren) cat:cs.AI +must -never'
        focus = Q.topic_focus(hostile)
        for q in Q.build_provider_queries("arxiv", focus, hostile):
            self.assertEqual(set(re.findall(r"\b([A-Za-z]+):", q["query"])) - {"ti", "abs", "all"}, set(), q["query"])
            self.assertNotRegex(q["query"], r"[;<>\\|&]", q["query"])
            self.assertNotRegex(q["query"], r"(^|[\s(])[-+]", q["query"])               # a leading + or - would be a search operator
        for q in Q.build_provider_queries("wikipedia", focus, hostile):
            self.assertRegex(q["query"], r"^[A-Za-z0-9 .\-]+$")

    def test_general_web_search_gets_the_topic_phrases_then_the_question_as_a_plain_sentence(self):
        qs = Q.build_provider_queries("searxng", self.focus, AIRLINE)
        self.assertEqual(qs[0]["role"], "primary")
        self.assertTrue(qs[0]["query"].startswith("business-class seat availability"))
        self.assertEqual(qs[1]["role"], "fallback")
        self.assertNotRegex(qs[1]["query"], r"[?,]")

    def test_a_part_of_the_plan_adds_at_most_one_word_to_the_general_search(self):
        plain = Q.build_provider_queries("searxng", self.focus, AIRLINE, "")[0]["query"]
        with_facet = Q.build_provider_queries("searxng", self.focus, AIRLINE, "Key facts and figures")[0]["query"]
        self.assertEqual(with_facet, plain + " statistics")
        self.assertEqual(Q.build_provider_queries("searxng", self.focus, AIRLINE, "Some unknown part")[0]["query"], plain)

    def test_a_question_with_no_topic_sends_nothing_to_anyone_not_even_the_question_itself(self):
        empty = Q.topic_focus("What does this dataset tell us?")
        for provider in ALL:
            self.assertEqual(Q.build_provider_queries(provider, empty, "What does this dataset tell us?"), [], provider)

    def test_a_single_word_topic_is_searched_by_its_own_name(self):
        f = Q.topic_focus("What is photosynthesis?")
        self.assertEqual(Q.build_provider_queries("wikipedia", f, "")[0]["query"], "photosynthesis")
        self.assertEqual(Q.build_provider_queries("arxiv", f, "")[0]["query"], "ti:photosynthesis OR abs:photosynthesis")

    def test_which_providers_suit_which_part_and_the_persons_own_choice_always_bounds_it(self):
        self.assertEqual(Q.providers_for("evidence and studies", ["wikipedia", "searxng"]), ["searxng"])
        self.assertEqual(Q.providers_for("evidence and studies", ["wikipedia"]), ["wikipedia"], "when none suit the part, all chosen are used: a gap is worse than a loose search")
        self.assertEqual(Q.providers_for("a part nobody listed", ["arxiv"]), ["arxiv"])
        self.assertEqual(Q.providers_for("Overview", ALL), ALL)
        self.assertNotIn("arxiv", Q.providers_for("Overview", ["wikipedia", "searxng"]))

    def test_an_arxiv_identifier_is_recognised_in_text_and_in_the_sites_own_addresses(self):
        cases = {"2101.00001v2": "2101.00001v2", "https://arxiv.org/abs/2101.00001v3": "2101.00001v3", "https://arxiv.org/pdf/2101.00001v1.pdf": "2101.00001v1",
                 "hep-th/9901001": "hep-th/9901001", "see arXiv:2101.00001 for details": "2101.00001", "no identifier 1234": "", "12345.678901": "", "": ""}
        for text, want in cases.items():
            self.assertEqual(Q.arxiv_id_in(text), want, text)


class PlanLayout(unittest.TestCase):
    def phrases_sent(self, plan, provider=None):
        return [(p, v["query"]) for w in plan["web"]["queries"] for p, vs in w["by_provider"].items() if provider in (None, p) for v in vs]

    def test_no_filler_from_the_old_outline_reaches_wikipedia_or_arxiv(self):
        for level in ("topic", "technical", "academic", "in_depth"):
            plan = build_plan("What is gradient boosting?", level, web=True, providers=ALL)
            for provider, query in self.phrases_sent(plan):
                if provider in ("wikipedia", "arxiv"):
                    self.assertNotRegex(query, r"\b(definition|background|history|statistics|facts|mechanism|methodology|criticism|controversy)\b", (level, provider, query))

    def test_the_same_phrase_is_never_planned_twice_for_the_same_provider(self):
        plan = build_plan(AIRLINE, "in_depth", web=True, providers=ALL)
        sent = [(p, q.lower()) for p, q in self.phrases_sent(plan)]
        self.assertEqual(len(sent), len(set(sent)))

    def test_every_provider_named_in_the_plan_was_chosen_by_the_person(self):
        plan = build_plan(AIRLINE, "topic", web=True, providers=["wikipedia", "searxng"])
        self.assertEqual({p for p, _ in self.phrases_sent(plan)} - {"wikipedia", "searxng"}, set())
        self.assertNotIn("arxiv", plan["web"]["providers"])

    def test_with_web_research_off_nothing_is_planned_to_leave_the_server(self):
        plan = build_plan(AIRLINE, "in_depth", web=False, providers=ALL)
        self.assertEqual(plan["web"], {"enabled": False, "providers": [], "queries": [], "max_pages": LEVELS["in_depth"]["web_pages"], "freshness_days": None, "arxiv_mode": "auto", "skipped_providers": []})

    def test_a_question_that_only_points_at_the_persons_document_plans_no_web_search_and_says_why(self):
        plan = build_plan("Please summarise this document", "basic", web=True, providers=ALL)
        self.assertFalse(plan["web"]["enabled"])
        self.assertTrue(any("no topic words to search for" in note for note in plan["limitations"]))

    def test_a_comparison_is_split_into_the_things_compared_and_their_differences(self):
        plan = build_plan("Compare solar power and wind power for India", "comparative", web=True, providers=ALL)
        self.assertEqual(plan["entities"], ["solar power for India", "wind power for India"])
        self.assertEqual([s["title"] for s in plan["subtopics"]], ["solar power for India", "wind power for India", "Differences and similarities"])

    def test_the_differences_part_searches_for_both_things_without_repeating_the_shared_qualifier(self):
        plan = build_plan("Compare solar power and wind power for India", "comparative", web=True, providers=ALL, arxiv_mode="always")             # arXiv is searched only when the subject fits or it is asked for
        differences = next(w for w in plan["web"]["queries"] if w["subtopic"] == "t3")["by_provider"]
        self.assertEqual(differences["wikipedia"][0]["query"], "solar power wind power")
        self.assertEqual(differences["searxng"][0]["query"], "solar power wind power")
        self.assertIn('(ti:"solar power" OR abs:"solar power") AND (ti:"wind power" OR abs:"wind power")', differences["arxiv"][0]["query"])
        for provider, query in self.phrases_sent(plan):
            if "difference" in query:
                self.fail((provider, query))

    def test_a_comparison_that_names_nothing_to_compare_is_treated_as_an_overview_and_says_so(self):
        plan = build_plan("Compare it", "comparative", web=False, providers=ALL)
        self.assertEqual(plan["entities"], [])
        self.assertTrue(any("treated as a topic overview" in note for note in plan["limitations"]))

    def test_a_multi_step_question_becomes_one_part_per_step(self):
        plan = build_plan("Find the history of the transformer architecture, then summarise its main limitations; then list recent alternatives", "multi_step", web=True, providers=ALL)
        self.assertEqual(len(plan["subtopics"]), 3)
        self.assertTrue(all(s["title"].startswith("Step ") for s in plan["subtopics"]))
        self.assertLessEqual(len(plan["subtopics"]), LEVELS["multi_step"]["subtopics"])

    def test_each_level_has_its_own_shape_and_stays_within_its_limits(self):
        shapes = {}
        for level, spec in LEVELS.items():
            plan = build_plan("How does gradient boosting work on tabular data?", level, web=True, providers=ALL)
            self.assertLessEqual(len(plan["subtopics"]), max(spec["subtopics"], 4), level)
            self.assertEqual(plan["rounds"], spec["rounds"])
            shapes[level] = tuple(s["title"] for s in plan["subtopics"])
        self.assertGreater(len(set(shapes.values())), 3, "the levels must not all produce the same outline")

    def test_the_approved_phrases_are_pinned_by_the_plan_hash_so_changing_them_needs_a_new_approval(self):
        a = build_plan(AIRLINE, "topic", web=True, providers=ALL)
        self.assertEqual(a["plan_hash"], build_plan(AIRLINE, "topic", web=True, providers=ALL)["plan_hash"])
        self.assertNotEqual(a["plan_hash"], build_plan(AIRLINE, "topic", web=True, providers=["searxng"])["plan_hash"])
        self.assertNotEqual(a["plan_hash"], build_plan(AIRLINE + " in 2024", "topic", web=True, providers=ALL)["plan_hash"])

    def test_only_words_of_the_question_are_ever_planned_to_be_sent(self):
        plan = build_plan(AIRLINE, "topic", web=True, providers=ALL, datasets=["ds-secret-1"], documents=["doc-secret-1"])
        blob = repr(plan["web"])
        for secret in ("ds-secret-1", "doc-secret-1"):
            self.assertNotIn(secret, blob)
        facet_words = {w for word in Q.FACET_WORDS.values() for w in word.split()}                  # the one fixed word a part of the plan may add ("statistics", "overview", ...)
        question_words = set(re.findall(r"[a-z0-9]+", AIRLINE.lower())) | facet_words | {"ti", "abs", "all", "and", "or"}
        for _, query in self.phrases_sent(plan):
            self.assertEqual(set(re.findall(r"[a-z0-9]+", query.lower())) - question_words, set(), query)


if __name__ == "__main__":
    unittest.main()
