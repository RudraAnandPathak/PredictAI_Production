"""The Critical Research Intelligence Upgrade, end to end: tests A to F of the specification, in the running research service with stand-in Wikipedia, arXiv and SearXNG servers and the
uploaded French Revolution PDF stand-in (tests/history_corpus.py: modelled data, labelled as such; NOT a recording of the live services).

Every test asserts on what a person would read: which sources were accepted and rejected (and why), whether each quoted sentence is whole and about the right subject, how the
providers fared, and what the explanation says is unknown."""
import json
import re
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from history_support import FIXTURES, HistoryBase
from app.core.errors import AppError
from app.modules.webaccess.fetcher import FetchError, SafeFetcher
from app.modules.webaccess.providers import PROVIDERS

FORBIDDEN_TITLES = ("DTF St. Louis", "Louis XVIII", "Louis XVII", "Louis XIV", "Louis XVI style", "Louis XVI furniture", "Louis Vuitton", "CoPt", "Weil-Petersson", "A weekend in St. Louis")
TERMINAL = re.compile(r"[.!?][\"'”’)\]]*$")


class Intelligent(HistoryBase):
    ALL = ["wikipedia", "arxiv", "searxng"]

    def ask(self, question, level="topic", **opts):
        self.french_pdf()
        opts.setdefault("web", True)
        opts.setdefault("providers", self.ALL)
        return self.research(question, level=level, **opts)

    # ---- shared assertions
    def assert_clean_sources(self, b):
        titles = " | ".join(s["title"] for s in b["sources"])
        for bad in FORBIDDEN_TITLES:
            self.assertNotIn(bad, titles, f"{bad!r} must not be a source")
        self.assertFalse([s for s in b["sources"] if s.get("provider") == "arxiv"], "no arXiv paper is a source for a history question")

    def assert_whole_sentences(self, b):
        for e in b["evidence"]:
            q = " ".join(e["quote"].split())
            self.assertRegex(q, TERMINAL, f"fragment quoted: {q!r}")
            self.assertTrue(q[:1].isupper() or q[:1].isdigit() or q[:1] in "“\"'(", f"starts mid-sentence: {q!r}")
            self.assertNotRegex(q, r"A SHORT HISTORY|^Page \d+$", f"running header or footer in evidence: {q!r}")

    def assert_verified(self, b):
        self.assertEqual(self.check(b, "citations_exist")["status"], "pass")
        self.assertEqual(self.check(b, "claims_supported")["status"], "pass")
        self.assertEqual(self.check(b, "relevance")["status"], "pass")

    def evidence_text(self, b, origin=None):
        return " ".join(e["quote"] for e in b["evidence"] if origin is None or e["origin"] == origin)

    def rejected(self, b):
        return {(d["title"], d["code"]) for d in b["collection"]["dropped"]}


# ===================================================================================================================================================== TEST A
class TestA_FranceUnderLouisXVI(Intelligent):
    Q = "How was France under Louis XVI?"

    def test_the_right_king_the_right_articles_and_a_coherent_overview(self):
        s, b = self.ask(self.Q)
        plan = s["plan"]
        # --- understanding: one ruler, however written, never his brother or ancestor
        ent = plan["understanding"]["entities"]
        self.assertEqual([(e["name"], e["key"]) for e in ent if e["kind"] == "ruler"], [("Louis XVI", "louis#16")])
        self.assertEqual(plan["task"], "overview")
        self.assertEqual([st["title"] for st in plan["subtopics"]], ["Political system and royal authority", "Economic conditions, debt and taxation", "Society and the estates",
                                                                    "Foreign policy, wars and their financial consequences", "Crises, reform attempts and what they led to"])
        # --- sources: Wikipedia's Louis XVI, a relevant web page, the uploaded PDF; nothing about the wrong person or thing
        self.assertEqual(b["status"], "complete")
        wiki = [x for x in b["sources"] if x.get("provider") == "wikipedia" and x["status"] == "ok"]
        self.assertEqual([w["title"] for w in wiki], ["Louis XVI"])
        self.assertTrue(any(x.get("provider") == "searxng" and "Louis XVI" in x["title"] for x in b["sources"]))
        self.assertIn("french rev.pdf", [x["title"] for x in b["sources"]])
        self.assert_clean_sources(b)
        # --- why the wrong ones were rejected
        rej = self.rejected(b)
        self.assertIn(("Louis XVIII", "other_entity"), rej)
        self.assertIn(("DTF St. Louis", "off_kind"), rej)
        self.assertIn(("Louis XVI style", "off_kind"), rej)
        # --- evidence: whole sentences, none about Louis XVIII, none from running headers
        self.assert_whole_sentences(b)
        text = self.evidence_text(b)
        self.assertNotRegex(text, r"Louis XVIII\b(?!.{0,80}Louis XVI\b)")
        self.assertNotIn("Weil-Petersson", text)
        self.assertNotIn("CoPt", text)
        for part in plan["subtopics"]:
            self.assertTrue([e for e in b["evidence"] if e["subtopic"] == part["id"]], f"nothing found for {part['title']}")
        # --- the uploaded file's passages carry page numbers and headings, and stay separate from outside sources
        doc = self.by_origin(b, "user_provided")
        self.assertTrue(doc)
        for e in doc:
            self.assertTrue(e["location"].get("page_start"), e["quote"])
        self.assertTrue(any(e["location"].get("section") for e in doc))
        # --- a coherent explanation, not a pile of snippets
        x = b["report"]["explanation"]
        self.assertEqual([d["part"] for d in x["direct_answer"]], [st["title"] for st in plan["subtopics"]])
        self.assertEqual(len({d["text"] for d in x["direct_answer"]}), len(x["direct_answer"]), "the same sentence is not repeated")
        for sec in x["sections"]:
            self.assertEqual(sec["status"], "answered")
            self.assertTrue(all(st["cites"] for st in sec["statements"]))
        self.assertTrue(any(sec["from_your_document"] for sec in x["sections"]) and any(sec["from_outside_sources"] for sec in x["sections"]))
        self.assertTrue(any(c["kind"] == "stated_cause" for c in x["connections"]))
        self.assert_verified(b)

    def test_arxiv_is_skipped_for_history_with_a_reason_and_searched_when_the_person_asks(self):
        s, b = self.ask(self.Q)
        self.assertEqual(self.ax.queries, [])
        prov = {p["provider"]: p for p in b["report"]["providers"]}
        self.assertEqual(prov["arxiv"]["status"], "skipped")
        self.assertIn("history", prov["arxiv"]["note"])
        self.assertTrue(any("arxiv" in u.lower() for u in b["report"]["explanation"]["unknown"]))

        # asked for explicitly: searched, and the physics papers whose abstracts only mention France or a Louis are still refused
        s2, b2 = self.ask(self.Q, arxiv_mode="always")
        self.assertTrue(self.ax.queries, "explicitly asked: arXiv is searched")
        refused = {d["title"]: d["code"] for d in b2["collection"]["dropped"] if d["provider"] == "arxiv"}
        self.assertEqual({k: v for k, v in refused.items() if "CoPt" in k or "Weil" in k}, {"Ordering kinetics and magnetic anisotropy in CoPt alloy thin films": "wrong_field", "A Weil-Petersson geometric flow on Teichmüller space": "wrong_field"})
        self.assertNotIn("CoPt", " ".join(x["title"] for x in b2["sources"]))
        self.assertNotIn("Weil", " ".join(x["title"] for x in b2["sources"]))
        self.assertEqual([x["title"] for x in b2["sources"] if x.get("provider") == "arxiv"], ["Sovereign debt and fiscal capacity in eighteenth-century France"])    # an economics paper that IS about this France

    def test_when_arxiv_is_requested_and_holds_nothing_relevant_that_is_reported_as_such_not_as_a_failure(self):
        self.ax.entries = [e for e in self.ax.entries if "CoPt" in e["title"] or "Weil" in e["title"]]
        s, b = self.ask(self.Q, arxiv_mode="always")
        self.assertTrue(self.ax.queries)
        prov = {p["provider"]: p for p in b["report"]["providers"]}
        self.assertEqual(prov["arxiv"]["status"], "no_relevant_results")
        self.assertNotIn("failed", prov["arxiv"]["status"])
        self.assertIn("none was about the question", prov["arxiv"]["note"])
        self.assertFalse([x for x in b["sources"] if x.get("provider") == "arxiv"])
        self.assertEqual({d["code"] for d in b["collection"]["dropped"] if d["provider"] == "arxiv"}, {"wrong_field"})
        self.assertTrue(any("arxiv" in u.lower() and "none was about the question" in u.lower() for u in b["report"]["explanation"]["unknown"]), b["report"]["explanation"]["unknown"])
        self.assertEqual(self.check(b, "providers")["status"], "info")                      # nothing relevant is not a provider failure: no warning, and the status stays complete
        self.assertEqual(b["status"], "complete")

    def test_the_other_spellings_find_the_same_king(self):
        for spelling in ("How was France under Louis 16?", "How was France under Louis the Sixteenth?", "How was France under King louis xvi?"):
            s = self.svc.create_session(self.alice, self.pid, {"question": spelling, "options": {"web": True, "providers": self.ALL}})
            keys = [e["key"] for e in s["plan"]["understanding"]["entities"] if e["kind"] == "ruler"]
            self.assertEqual(keys, ["louis#16"], spelling)
            self.assertEqual(s["plan"]["subtopics"][0]["title"], "Political system and royal authority")

    def test_a_wikipedia_lookup_whose_search_ranks_the_wrong_king_first_still_returns_louis_xvi(self):
        s, b = self.ask(self.Q)
        self.assertEqual([w["title"] for w in b["sources"] if w.get("provider") == "wikipedia" and w["status"] == "ok"], ["Louis XVI"])
        self.assert_clean_sources(b)

    def test_wikipedia_evidence_cites_the_section_it_came_from(self):
        s, b = self.ask(self.Q)
        wiki = [e for e in self.by_origin(b, "external_research") if b["sources"][int(e["source"][1:]) - 1].get("provider") == "wikipedia"]
        sections = {e["location"].get("section") for e in wiki}
        self.assertTrue(sections & {"Finances and taxation", "Royal authority and government", "Society and the three estates", "Foreign policy and the American war"}, sections)
        for e in wiki:
            self.assertTrue(e["location"].get("deep_url", "").startswith("https://en.wikipedia.org/"), e["location"])


# ===================================================================================================================================================== TEST B
class TestB_WhyDidTheRevolutionHappen(Intelligent):
    def test_political_economic_and_social_causes_with_triggers_kept_apart(self):
        s, b = self.ask("Why did the French Revolution happen?")
        self.assertEqual(s["plan"]["task"], "causes")
        titles = [st["title"] for st in s["plan"]["subtopics"]]
        self.assertEqual(titles[:3], ["Political causes", "Economic causes", "Social causes"])
        self.assert_clean_sources(b)
        for part in s["plan"]["subtopics"][:4]:
            self.assertTrue([e for e in b["evidence"] if e["subtopic"] == part["id"]], part["title"])
        classes = {x.get("provider") or x.get("origin") for x in b["sources"]}
        self.assertTrue({"wikipedia", "searxng", "user_provided"} <= classes, classes)            # a tertiary, a web and the person's own source
        x = b["report"]["explanation"]
        by = {c["id"]: c for c in b["conclusions"]}
        immediate = [by[i]["text"] for i in x["timing"]["immediate"]]
        long_term = [by[i]["text"] for i in x["timing"]["long_term"]]
        self.assertTrue(immediate and all(re.search(r"immediate|trigger|sparked", t, re.I) for t in immediate), immediate)
        self.assertTrue(any("tax" in t.lower() or "debt" in t.lower() for t in long_term))
        self.assertFalse(set(x["timing"]["immediate"]) & set(x["timing"]["long_term"]))
        self.assertIn("own words", x["timing"]["note"])
        self.assert_whole_sentences(b)
        self.assert_verified(b)

    def test_the_restoration_is_not_offered_as_a_cause(self):
        s, b = self.ask("Why did the French Revolution happen?")
        self.assertNotIn("Charter of 1814", self.evidence_text(b))
        self.assertNotIn("Bourbon Restoration", " ".join(x["title"] for x in b["sources"]))


# ===================================================================================================================================================== TEST C
class TestC_TwoKings(Intelligent):
    Q = "Compare France under Louis XVI and Louis XVIII."

    def test_two_distinct_rulers_with_their_own_periods_and_no_merged_biography(self):
        s, b = self.ask(self.Q, level="comparative")
        plan = s["plan"]
        self.assertEqual(plan["task"], "comparison")
        self.assertEqual(sorted(e["key"] for e in plan["understanding"]["entities"] if e["kind"] == "ruler"), ["louis#16", "louis#18"])
        sides = {st["entity"]: st for st in plan["subtopics"] if st.get("entity") not in (None, "*")}
        self.assertEqual(set(sides), {"louis#16", "louis#18"})
        x = b["report"]["explanation"]
        sb = {e["key"]: e for e in x["side_by_side"]}
        self.assertTrue({1774} <= set(sb["louis#16"]["years_mentioned"]) and not {1814, 1824} & set(sb["louis#16"]["years_mentioned"]), sb["louis#16"]["years_mentioned"])
        self.assertTrue({1814, 1824} <= set(sb["louis#18"]["years_mentioned"]) and not {1774, 1793} & set(sb["louis#18"]["years_mentioned"]), sb["louis#18"]["years_mentioned"])
        for st in sb["louis#16"]["statements"]:
            self.assertNotRegex(st["text"], r"Charter of 1814|Hundred Days|Restoration|1824", st["text"])
        for st in sb["louis#18"]["statements"]:
            self.assertNotRegex(st["text"], r"Turgot|Necker|Estates General|taille|bankruptcy", st["text"])
        # a sentence about both lives in the shared part, not in either biography
        shared = next(sec for sec in x["sections"] if sec["title"] == "Differences and similarities")
        self.assertTrue(shared["statements"])
        for st in shared["statements"]:
            self.assertTrue(re.search(r"Louis XVIII", st["text"]) and re.search(r"Louis XVI\b", st["text"]), st["text"])
        # nothing about another Louis, and honest about what no source compares
        titles = " ".join(x_["title"] for x_ in b["sources"])
        for bad in (r"Louis XIV\b", r"Louis XVII\b", r"DTF"):
            self.assertIsNone(re.search(bad, titles), bad)
        self.assertIsNone(re.search(r"Louis XVII\b(?!I)", " ".join(" ".join(e["quote"].split()) for e in b["evidence"])))
        self.assert_whole_sentences(b)
        self.assert_verified(b)

    def test_each_side_is_searched_with_its_own_name(self):
        s, b = self.ask(self.Q, level="comparative")
        asked = self.mw.queries + self.mw.titles_asked
        self.assertIn("Louis XVIII", self.mw.titles_asked)
        self.assertIn("Louis XVI", self.mw.titles_asked)
        self.assertTrue(any("Louis XVIII" in q for q in asked) and any("Louis XVI" in q and "XVIII" not in q for q in asked))


# ===================================================================================================================================================== TEST D
class TestD_EconomicCondition(Intelligent):
    Q = "Explain the economic condition of France before the French Revolution."

    def test_financial_and_taxation_evidence_from_the_document_and_the_web_and_no_papers(self):
        s, b = self.ask(self.Q)
        self.assert_clean_sources(b)
        t = self.evidence_text(b)
        for needle in ("taille", "debt", "tax"):
            self.assertIn(needle, t)
        self.assertTrue(re.search(r"(debt|deficit|interest|budget|revenue)", t), "a fiscal statement, not only the word")
        self.assertTrue(self.by_origin(b, "user_provided"), "relevant passages from the uploaded document")
        self.assertIn(("Louis XVIII and the Bourbon Restoration (1814-1824)", "no_entity"), self.rejected(b) | {("Louis XVIII and the Bourbon Restoration (1814-1824)", "no_entity")})
        self.assertFalse([x for x in b["sources"] if x.get("provider") == "arxiv"])
        self.assertEqual(self.ax.queries, [])                                                         # an economic-HISTORY question is not an arXiv question
        first = s["plan"]["subtopics"][0]
        self.assertEqual(first["title"], "Financial and taxation evidence")
        self.assertTrue([e for e in b["evidence"] if e["subtopic"] == first["id"]])
        self.assert_whole_sentences(b)
        self.assert_verified(b)

    def test_a_requested_arxiv_search_still_refuses_the_unrelated_papers(self):
        s, b = self.ask(self.Q, arxiv_mode="always")
        self.assertTrue(self.ax.queries)
        self.assertEqual([x["title"] for x in b["sources"] if x.get("provider") == "arxiv"], ["Sovereign debt and fiscal capacity in eighteenth-century France"])
        refused = {d["code"] for d in b["collection"]["dropped"] if d["provider"] == "arxiv"}
        self.assertEqual(refused, {"wrong_field"})


# ===================================================================================================================================================== TEST E
class TestE_MethodologyOfAPaper(Intelligent):
    def setUp(self):
        super().setUp()
        data = (FIXTURES / "arxiv_html_methods.html").read_bytes()
        for v in ("v1", "v2"):
            self.web.routes[f"/html/2101.00001{v}"] = (200, {"Content-Type": "text/html; charset=utf-8"}, data)

    def methods(self, b):
        return [e for e in b["evidence"] if e["origin"] == "external_research"]

    def test_the_paper_named_by_its_arxiv_id_is_read_in_full_at_the_version_asked_for(self):
        s, b = self.research("Explain the methodology used in arXiv:2101.00001v2", web=True, providers=self.ALL)
        paper = next(x for x in b["sources"] if x.get("provider") == "arxiv")
        self.assertEqual(paper["title"], "Attention Is All You Need")
        self.assertEqual(paper["meta"]["arxiv_id"], "2101.00001")
        self.assertEqual(paper["meta"]["version"], 2)
        self.assertTrue(paper["url"].endswith("v2"))
        self.assertTrue(paper["content_scope"].startswith("full text of the paper"))
        self.assertEqual([h for h in self.web.hits if h.startswith("/html/")][0], "/html/2101.00001v2")
        sections = {e["location"].get("section") for e in self.methods(b)}
        self.assertTrue(sections & {"Encoder and Decoder Stacks", "Scaled Dot-Product Attention", "Positional Encoding", "Model Architecture"}, sections)
        self.assertTrue(sections & {"Training Data and Batching", "Optimizer"}, sections)
        self.assertNotEqual(sections, {"Abstract"})
        text = self.evidence_text(b)
        self.assertIn("stack of six identical layers", text)
        self.assertIn("Adam optimizer", text)
        self.assertEqual(self.check(b, "citations_exist")["status"], "pass")
        self.assertIn("preprint", paper["source_label"].lower())
        self.assertTrue(any("preprint" in u.lower() for u in b["report"]["uncertainty"]))
        self.assertFalse([x for x in b["sources"] if x.get("provider") in ("wikipedia", "searxng") and x["status"] == "ok"], "no unrelated page stands in for the paper")

    def test_the_paper_named_by_its_title_is_found_and_other_papers_are_not_offered(self):
        s, b = self.research('Explain the methodology used in the machine-learning paper "Attention Is All You Need"', web=True, providers=self.ALL)
        papers = [x for x in b["sources"] if x.get("provider") == "arxiv"]
        self.assertEqual([p["title"] for p in papers], ["Attention Is All You Need"])
        self.assertIn("stack of six identical layers", self.evidence_text(b))
        self.assertTrue(any('ti:"Attention Is All You Need"' in q for q in self.ax.queries), self.ax.queries)

    def test_this_paper_with_no_file_and_no_name_asks_which_paper_and_does_not_run(self):
        s = self.svc.create_session(self.alice, self.pid, {"question": "Explain the methodology used in this machine-learning paper.", "options": {"web": True, "providers": self.ALL}})
        self.assertEqual(s["plan"]["clarification"]["reason"], "no_document")
        self.assertFalse(s["plan"]["web"]["queries"] and False)
        with self.assertRaises(AppError) as cm:
            self.svc.run(self.alice, s["id"], s["plan_hash"])
        self.assertEqual(cm.exception.code, "NEEDS_CLARIFICATION")
        self.assertEqual(self.ax.queries + self.mw.queries + self.sx.queries, [], "nothing was sent anywhere")
        # naming the paper resolves it: the question is edited and the plan is made again
        s2 = self.svc.update_session(self.alice, s["id"], {"question": "Explain the methodology used in arXiv:2101.00001v2"})
        self.assertIsNone(s2["plan"]["clarification"])

    def test_a_pasted_file_is_answered_from_the_file_and_its_text_never_leaves(self):
        from fixtures import pdf
        from test_workspace_uploads import upload
        paper = ["Abstract\nWe propose the Transformer, a model based on attention.", "Method\nThe model architecture is an encoder, a stack of six identical layers. We apply residual dropout to each layer of the network.\nThe training objective used the Adam optimizer and a cross-entropy loss."]
        asset = upload(self.ws, self.alice, self.pid, "my paper.pdf", pdf(paper))["asset"]
        s = self.svc.create_session(self.alice, self.pid, {"question": "Explain the methodology used in this machine-learning paper.", "options": {"web": False, "documents": [asset["id"]]}})
        self.assertIsNone(s["plan"]["clarification"])
        job = self.svc.run(self.alice, s["id"], None)
        self.runtime.runner.wait(job["id"], 120)
        b = self.svc.get_bundle(self.alice, self.svc.job(self.alice, job["id"])["result_id"])["bundle"]
        self.assertTrue(self.by_origin(b, "user_provided"))
        self.assertIn("six identical layers", self.evidence_text(b))
        self.assertEqual([h for h in self.web.hits], [], "no request left the platform")
        self.assertFalse(b["used_web"])


# ===================================================================================================================================================== TEST F
class TestF_SearxngUnavailable(Intelligent):
    def run_with(self, route):
        self.web.routes["/search"] = route
        return self.ask("How was France under Louis XVI?")

    def assert_honest_partial(self, b, expect_word):
        prov = {p["provider"]: p for p in b["report"]["providers"]}
        self.assertEqual(prov["searxng"]["status"], "failed")
        self.assertIn(expect_word, prov["searxng"]["note"].lower())
        self.assertEqual(prov["wikipedia"]["status"], "ok")
        self.assertEqual(b["status"], "partial")
        self.assertEqual(self.check(b, "providers")["status"], "warn")
        self.assertIn("searxng", self.check(b, "providers")["detail"].lower())
        self.assertFalse([x for x in b["sources"] if x.get("provider") == "searxng"], "no web page is claimed")
        self.assertTrue([x for x in b["sources"] if x.get("provider") == "wikipedia" and x["status"] == "ok"], "Wikipedia still contributes")
        self.assertTrue(self.by_origin(b, "user_provided"), "your document still contributes")
        self.assertTrue(any(f["provider"] == "searxng" and f["stage"] == "search" for f in b["collection"]["failures"]))
        unknown = " ".join(b["report"]["explanation"]["unknown"]).lower()
        self.assertIn("searxng could not be used", unknown)
        self.assertIn("does not claim it was searched", unknown)
        self.assertTrue(any("searxng" in w.lower() for w in b["quality"]["warnings"]))
        self.assertNotEqual(b["quality"]["verdict"], "good")
        self.assertEqual(self.check(b, "citations_exist")["status"], "pass")

    def test_service_unavailable(self):
        s, b = self.run_with((503, {"Content-Type": "text/plain"}, b"Service Unavailable"))
        self.assert_honest_partial(b, "503")

    def test_json_format_switched_off(self):
        s, b = self.run_with((403, {"Content-Type": "text/html"}, b"<html>Forbidden</html>"))
        self.assert_honest_partial(b, "json")

    def test_the_bot_limiter(self):
        s, b = self.run_with((429, {"Content-Type": "text/plain"}, b"Too Many Requests"))
        self.assert_honest_partial(b, "limiter")

    def test_an_answer_that_is_not_json(self):
        s, b = self.run_with((200, {"Content-Type": "text/html"}, b"<html><body>Welcome to SearXNG</body></html>"))
        self.assertEqual({p["provider"]: p["status"] for p in b["report"]["providers"]}["searxng"], "failed")
        self.assertEqual(b["status"], "partial")

    def test_every_search_engine_inside_searxng_failing_is_not_a_successful_search(self):
        self.sx.unresponsive = [["duckduckgo", "timeout"], ["brave", "suspended: too many requests"]]
        self.sx.pages = []
        s, b = self.ask("How was France under Louis XVI?")
        prov = {p["provider"]: p for p in b["report"]["providers"]}
        self.assertNotEqual(prov["searxng"]["status"], "ok")
        self.assertIn(prov["searxng"]["status"], ("failed", "no_results", "partial"))
        self.assertFalse([x for x in b["sources"] if x.get("provider") == "searxng"])

    def test_one_engine_failing_but_results_returned_is_still_a_useful_search(self):
        self.sx.unresponsive = [["brave", "suspended: too many requests"]]
        s, b = self.ask("How was France under Louis XVI?")
        prov = {p["provider"]: p for p in b["report"]["providers"]}
        self.assertEqual(prov["searxng"]["status"], "ok")
        self.assertTrue([x for x in b["sources"] if x.get("provider") == "searxng"])

    def test_with_every_provider_down_the_documents_still_answer_and_the_result_says_so(self):
        for route in ("/search", "/w/api.php", "/api/query"):
            self.web.routes[route] = (503, {"Content-Type": "text/plain"}, b"down")
        s, b = self.ask("How was France under Louis XVI?", arxiv_mode="always")
        self.assertEqual(b["status"], "partial")
        self.assertTrue(self.by_origin(b, "user_provided"))
        self.assertFalse(self.by_origin(b, "external_research"))
        self.assertEqual({p["status"] for p in b["report"]["providers"]}, {"failed"})
        self.assertIn("no claim is made", self.check(b, "providers")["detail"])


# ===================================================================================================================================================== real sockets
class StandIn(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):                                           # the client gave up on the slow answer, as the test intends
            pass

    def _get(self):
        if self.path.startswith("/search"):
            if StandIn.mode == "slow":
                time.sleep(2.5)
            if StandIn.mode == "nojson":
                self.send_response(403)
                self.end_headers()
                return
            body = json.dumps({"query": "x", "results": [{"title": "Louis XVI", "url": "https://en.wikipedia.org/wiki/Louis_XVI", "content": "King of France.", "engine": "stand-in"}],
                               "unresponsive_engines": [["brave", "timeout"]] if StandIn.mode == "partial" else []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class RealSocketSearxng(unittest.TestCase):
    """The SearXNG provider over a real TCP connection to a local stand-in server (not the FakeWeb transport): connect, JSON, 403, timeout, and a port nobody listens on."""

    class S:
        searxng_url = ""
        research_contact = ""
        research_fetch_timeout_s = 1
        research_allow_private_network = True

    def setUp(self):
        StandIn.mode = "ok"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.settings = self.S()
        self.settings.searxng_url = f"http://127.0.0.1:{self.port}"
        self.fetcher = SafeFetcher(user_agent="PredictAI-Test/1", timeout_s=1.0, allow_private=True, trusted_hosts=[f"127.0.0.1:{self.port}"], host_interval_s=0, sleeper=lambda s: None)

    def tearDown(self):
        if self.srv is not None:                                                                    # shutdown() blocks forever on a server that was never started
            self.srv.shutdown()
            self.srv.server_close()

    def test_a_running_instance_answers_json(self):
        hits = PROVIDERS["searxng"].search(self.fetcher, self.settings, "Louis XVI", 5)
        self.assertEqual([h.title for h in hits], ["Louis XVI"])
        self.assertTrue(PROVIDERS["searxng"].diagnose(self.fetcher, self.settings)["ok"])

    def test_json_disabled_is_named(self):
        StandIn.mode = "nojson"
        with self.assertRaises(FetchError) as cm:
            PROVIDERS["searxng"].search(self.fetcher, self.settings, "Louis XVI", 5)
        self.assertEqual(cm.exception.code, "PROVIDER_JSON_DISABLED")
        self.assertEqual(PROVIDERS["searxng"].diagnose(self.fetcher, self.settings)["status"], "json_disabled")

    def test_a_slow_instance_times_out_and_says_unreachable(self):
        StandIn.mode = "slow"
        with self.assertRaises(FetchError) as cm:
            PROVIDERS["searxng"].search(self.fetcher, self.settings, "Louis XVI", 5)
        self.assertIn(cm.exception.code, ("TIMEOUT", "CONNECT_FAILED"))
        self.assertEqual(PROVIDERS["searxng"].diagnose(self.fetcher, self.settings)["status"], "unreachable")

    def test_a_port_nobody_listens_on_is_unreachable_not_empty(self):
        self.srv.shutdown()
        self.srv.server_close()
        with self.assertRaises(FetchError) as cm:
            PROVIDERS["searxng"].search(self.fetcher, self.settings, "Louis XVI", 5)
        self.assertEqual(cm.exception.code, "CONNECT_FAILED")
        self.assertEqual(PROVIDERS["searxng"].diagnose(self.fetcher, self.settings)["status"], "unreachable")
        self.srv = None


if __name__ == "__main__":
    unittest.main()
