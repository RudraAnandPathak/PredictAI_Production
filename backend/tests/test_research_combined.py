"""Research that combines sources: dataset + Wikipedia/arXiv/web, document + web, dataset + document, dataset + trained model, all three providers together, follow-up questions about what was
retrieved, provider failures, hostile text inside an article, consent, and the direct search/open/diagnose endpoints.

Every provider call is answered by a local FakeWeb in the documented response shapes; the live Wikipedia, arXiv and SearXNG are NOT contacted. A pass here shows the engine behaves correctly
on those shapes. It does not show how the live services answer, which has not been verified."""
import json
import unittest

from app.core.errors import AppError
from fixtures import pdf
from research_combined_support import INJECTED, QUESTION, SEATS_ARTICLE, CombinedBase
from research_data_support import CANARY
from research_support import RIVER_DOC
from test_predictive_service import FAST, wait_job
from test_workspace_uploads import upload
import analytics_support as sup
import ml_support as S


class DatasetAndExternal(CombinedBase):
    def setUp(self):
        super().setUp()
        self.air = self.airline()
        self.wikipedia()
        self.arxiv(relevant=False)
        page_url = self.aviation_page()
        self.searx([{"title": "Airline seat inventory", "url": page_url, "content": "business class seats availability"}, {"title": "Cooking pasta", "url": "https://food.test/pasta", "content": "boil water"}])
        self.allow()

    def test_the_airline_example_calculates_on_the_data_reads_outside_sources_and_keeps_them_apart(self):
        s, b = self.research(QUESTION, datasets=[self.air["id"]], web=True, providers=["wikipedia", "arxiv", "searxng"])
        self.assertTrue(s["needs_approval"])
        origins = {e["origin"] for e in b["evidence"]}
        self.assertEqual(origins, {"calculated", "external_research"})
        self.assertEqual(b["status"] in ("complete", "partial"), True)
        # what the data says is calculated; what Wikipedia and the web say is quoted, each with its own label
        calc, ext = self.by_origin(b, "calculated"), self.by_origin(b, "external_research")
        self.assertTrue(all(e["kind"] == "computed" for e in calc))
        classes = {x["source_label"] for x in b["sources"] if x["origin"] == "external_research" and x["status"] == "ok"}
        self.assertTrue(any(c.startswith("Wikipedia (encyclopedia") for c in classes), classes)
        self.assertIn("General web page", classes)
        # every conclusion rests on one kind of evidence; the report places calculated and outside passages side by side
        self.assertEqual(self.check(b, "calculated_vs_external")["status"], "pass")
        rep = b["report"]
        self.assertTrue(rep["separation_note"])
        self.assertTrue(rep["comparison"], "the data and an outside passage discuss the same variables, so a comparison is listed")
        cmp0 = rep["comparison"][0]
        self.assertIn("by words, not by meaning", cmp0["note"])
        self.assertTrue(cmp0["calculated"]["text"] and cmp0["external"][0]["label"])
        # both kinds of subtopic were answered
        scopes = {sec["scope"]: sec["status"] for sec in rep["sections"] if sec["status"] == "answered"}
        self.assertEqual(set(scopes), {"dataset", "general"})
        # the article was read section by section and cited with a link that opens that section
        wiki_ev = [e for e in ext if "wikipedia" in (next(x for x in b["sources"] if x["id"] == e["source"]).get("provider") or "")]
        self.assertTrue(wiki_ev)
        self.assertTrue(any(e["location"].get("section") and str(e["location"].get("deep_url", "")).startswith("https://en.wikipedia.org/") for e in wiki_ev), [e["location"] for e in wiki_ev])
        self.assertTrue(all(c["status"] != "fail" for c in b["quality"]["checks"]), [c for c in b["quality"]["checks"] if c["status"] == "fail"])

    def test_nothing_from_the_data_leaves_and_only_approved_topic_phrases_are_sent(self):
        s, b = self.research(QUESTION, datasets=[self.air["id"]], web=True, providers=["wikipedia", "arxiv", "searxng"])
        outbound = " ".join(self.web.hits).lower()
        for private in (CANARY.lower(), "ai0001", "flight_no", "delay_minutes", "passengers", "airline.csv"):
            self.assertNotIn(private, outbound)
        approved = set(b["web"]["approved_queries"])
        self.assertTrue(set(b["web"]["queries_sent"]) <= approved)
        self.assertEqual(self.check(b, "web_privacy")["status"], "pass")
        self.assertNotIn(CANARY, json.dumps(b))
        self.assertIn("No document text was sent", self.check(b, "web_privacy")["detail"])

    def test_outside_sources_never_change_a_calculated_number(self):
        s1, only = self.research(QUESTION, datasets=[self.air["id"]])
        self.web.html("/aviation-seats", "<html><head><title>Wrong</title></head><body><p>Business class price is exactly 99999 and passengers per flight are 1 according to the aviation authority.</p>"
                                       "<p>Seat availability in business class is unlimited on all routes.</p></body></html>")
        s2, both = self.research(QUESTION, datasets=[self.air["id"]], web=True, providers=["wikipedia", "searxng"])
        self.assertEqual({e["quote"] for e in self.by_origin(only, "calculated")} - {e["quote"] for e in self.by_origin(both, "calculated")}, set())
        self.assertFalse([c for c in both["conflicts"] if any(st["evidence"] in {e["id"] for e in self.by_origin(both, "calculated")} for st in c["statements"])])
        self.assertNotIn("99999", " ".join(e["quote"] for e in self.by_origin(both, "calculated")))

    def test_without_consent_the_dataset_part_still_runs_and_the_web_part_is_refused_and_said_so(self):
        s = self.svc.create_session(self.alice, self.pid, {"question": QUESTION, "options": {"datasets": [self.air["id"]], "web": True, "providers": ["wikipedia", "searxng"]}})
        self.svc.approve_plan(self.alice, s["id"], s["plan_hash"])
        self.svc.put_settings(self.alice, None, {"web_enabled": False})
        row = self.svc.repo.session(self.alice.user_id, s["id"])
        before = len(self.web.hits)
        out = self.svc.engine.run(self.alice, row, "job-x", lambda: False, lambda *a, **k: None)["bundle"]
        self.assertEqual((out["web"]["used"], len(self.web.hits) - before), (False, 0))
        self.assertTrue(self.by_origin(out, "calculated"))
        self.assertFalse(self.by_origin(out, "external_research"))
        self.assertTrue(any("not run" in w for w in out["quality"]["warnings"]))
        self.assertNotEqual(out["status"], "complete")

    def test_a_citation_opens_the_original_section_or_the_calculation(self):
        s, b = self.research(QUESTION, datasets=[self.air["id"]], web=True, providers=["wikipedia", "searxng"])
        wiki = next(e for e in self.by_origin(b, "external_research") if str(e["location"].get("deep_url", "")).startswith("https://en.wikipedia.org/"))
        got = self.svc.inspect_citation(self.alice, b["id"], wiki["id"])
        self.assertTrue(got["verification"]["verified"])
        self.assertEqual(got["open"]["type"], "external")
        self.assertTrue(got["open"]["url"].startswith("https://en.wikipedia.org/"))
        self.assertIn("may have changed", got["open"]["note"])
        self.assertTrue(got["source_label"].startswith("Wikipedia"))
        calc = next(e for e in self.by_origin(b, "calculated") if e["citation"].get("kind") == "calculation")
        got2 = self.svc.inspect_citation(self.alice, b["id"], calc["id"])
        self.assertEqual((got2["open"]["type"], got2["open"]["run_id"]), ("analysis_run", calc["citation"]["run_id"]))


class DocumentCombinations(CombinedBase):
    def test_a_document_and_wikipedia_are_cited_with_their_own_labels_and_page_numbers(self):
        doc = upload(self.ws, self.alice, self.pid, "seats.pdf", pdf(["Business class seats are limited to eight per aircraft on this airline.\nSeat availability is checked at booking.",
                                                                        "Fares for business class are higher than economy fares."]))["asset"]["id"]
        self.wikipedia()
        self.allow(("wikipedia",))
        s, b = self.research("How does business class seat availability compare with what Wikipedia says about business class fares?", documents=[doc], web=True, providers=["wikipedia"])
        self.assertEqual({e["origin"] for e in b["evidence"]}, {"user_provided", "external_research"})
        d = next(e for e in self.by_origin(b, "user_provided"))
        self.assertTrue(d["location"].get("page_start"))
        self.assertEqual(self.check(b, "citations_exist")["status"], "pass")
        labels = {x["source_label"] for x in b["sources"]}
        self.assertIn("Your document", labels)
        self.assertTrue(any(l.startswith("Wikipedia") for l in labels))

    def test_a_dataset_and_a_document_are_used_together_without_the_web(self):
        air = self.airline()
        doc = upload(self.ws, self.alice, self.pid, "seats.pdf", pdf(["Business class seats are limited to eight per aircraft on this airline.\nSeat availability is checked at booking."]))["asset"]["id"]
        s, b = self.research("What does the airline dataset show about business class seats, and what does the seat policy document say?", datasets=[air["id"]], documents=[doc])
        self.assertEqual({e["origin"] for e in b["evidence"]}, {"calculated", "user_provided"})
        self.assertFalse(b["web"]["used"])
        kinds = {tuple(sorted({(next(x for x in b["sources"] if x["id"] == i)["kind"]) for i in [b_ev["source"] for b_ev in b["evidence"] if b_ev["id"] in c["evidence"]]})) for c in b["conclusions"]}
        self.assertTrue(all(len(k) == 1 for k in kinds), kinds)                                     # one kind of evidence per conclusion

    def test_a_dataset_and_its_trained_model_are_reported_together_with_their_own_labels(self):
        asset = upload(self.ws, self.alice, self.pid, "customers.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        wait_job(self.ws.predictive, self.alice, self.ws.predictive.start_training(self.alice, asset["id"], FAST, label="Baseline")["id"])
        s, b = self.research("What drives bought, and how good is the model at predicting it?", datasets=[asset["id"]])
        self.assertEqual({e["origin"] for e in b["evidence"]}, {"calculated", "model_prediction"})
        self.assertTrue(all(e["kind"] == "prediction" for e in self.by_origin(b, "model_prediction")))
        self.assertNotIn("calculated_vs_external", {c["id"] for c in b["quality"]["checks"]})
        self.assertEqual(self.check(b, "numerical_accuracy")["status"], "pass")


class AllThreeProviders(CombinedBase):
    QUESTION = "How does the attention mechanism work in transformer neural networks?"

    def setUp(self):
        super().setUp()
        self.wikipedia()                                                                           # answers with the business-class article: not about attention, so it is not used
        self.arxiv(relevant=True)
        url = "https://blog.test/attention"
        self.web.html("/attention", "<html><head><title>Attention explained</title></head><body><p>The attention mechanism in transformer networks lets each token weigh every other token when it builds its representation.</p>"
                                    "<p>Scaled dot product attention divides the scores by the square root of the key size before the softmax.</p></body></html>")
        self.searx([{"title": "Attention explained", "url": url, "content": "attention mechanism transformer"}])
        self.allow()

    def test_wikipedia_arxiv_and_the_web_are_searched_independently_and_labelled(self):
        s, b = self.research(self.QUESTION, level="technical", web=True, providers=["wikipedia", "arxiv", "searxng"])
        searched = {x["provider"] for x in b["collection"]["searches"]}
        self.assertEqual(searched, {"wikipedia", "arxiv", "searxng"})
        paper = next(x for x in b["sources"] if x.get("provider") == "arxiv" and x["status"] == "ok")
        self.assertTrue(paper["content_scope"].startswith("full text of the paper"), paper["content_scope"])       # an abstract is never presented as the paper
        self.assertEqual(paper["source_label"], "arXiv preprint (not peer reviewed)")
        self.assertTrue(paper["meta"]["is_latest"] and paper["meta"]["pdf_url"].startswith("https://arxiv.org/pdf/"))
        self.assertTrue(set(paper["sections_used"]) & {"Model Architecture", "Attention", "Results", "Conclusion", "Introduction"})
        ev = [e for e in b["evidence"] if e["source"] == paper["id"]]
        self.assertTrue(ev)
        self.assertTrue(any(e["location"].get("section") for e in ev))
        self.assertTrue(any("arxiv.org/html" in str(e["location"].get("deep_url", "")) for e in ev))
        self.assertIn("arxiv_preprint", b["report"]["external"]["by_class"])
        self.assertIn("web", b["report"]["external"]["by_class"])
        self.assertTrue(any("preprints" in u for u in b["report"]["uncertainty"]))
        dropped = {d["title"] for d in b["collection"]["dropped"]}
        self.assertTrue(dropped, "hits that match none of the question's topic words are listed as dropped, not read")
        self.assertEqual(self.check(b, "citations_exist")["status"], "pass")

    def test_a_follow_up_about_the_retrieved_paper_reuses_the_saved_copy_without_new_requests(self):
        s, b = self.research(self.QUESTION, level="technical", web=True, providers=["wikipedia", "arxiv", "searxng"])
        before = list(self.web.hits)
        f = self.svc.create_follow_up(self.alice, s["id"], {"question": "What results does the transformer paper report?", "options": {"web": False}})
        self.assertEqual(f["plan"]["local"]["reuse_sources_of"], s["id"])
        self.assertFalse(f["needs_approval"])
        job = self.svc.run(self.alice, f["id"], None)
        self.runtime.runner.wait(job["id"], 120)
        done = self.svc.job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error_message"))
        fb = self.svc.get_bundle(self.alice, done["result_id"])["bundle"]
        self.assertEqual(self.web.hits, before, "no new request was made: the saved copies were reused")
        reused = [x for x in fb["sources"] if x.get("reused_from_session") == s["id"]]
        self.assertTrue(reused)
        paper = next(x for x in reused if x.get("provider") == "arxiv")
        self.assertEqual(paper["source_label"], "arXiv preprint (not peer reviewed)")
        self.assertTrue([e for e in fb["evidence"] if e["source"] == paper["id"]])
        self.assertEqual(self.check(fb, "citations_exist")["status"], "pass")
        self.assertTrue(any(i["kind"] == "earlier_sources" for i in fb["context"]["included"]))

    def test_a_follow_up_with_web_on_searches_again_but_reads_the_paper_from_the_cache_not_the_site(self):
        s, b = self.research(self.QUESTION, level="technical", web=True, providers=["wikipedia", "arxiv", "searxng"])
        before = len(self.web.hits)
        f = self.svc.create_follow_up(self.alice, s["id"], {"question": "What results does the transformer paper report?", "options": {"web": True, "providers": ["arxiv"]}})
        self.assertTrue(f["needs_approval"])                                                         # a new set of outbound phrases is shown and approved again
        self.assertEqual([st["title"] for st in f["plan"]["subtopics"]], ["Answer"])                  # the question is answered as asked, not re-split into the level's fixed outline
        job = self.svc.run(self.alice, f["id"], f["plan_hash"])
        self.runtime.runner.wait(job["id"], 120)
        done = self.svc.job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error_message"))
        fb = self.svc.get_bundle(self.alice, done["result_id"])["bundle"]
        new = self.web.hits[before:]
        self.assertTrue(new and all(h.startswith("/api/query") for h in new), new)                   # only arXiv searches: the paper itself came from the saved cache
        paper = next(x for x in fb["sources"] if x.get("provider") == "arxiv")
        self.assertTrue(paper["cache_hit"])
        self.assertTrue(any(e["source"] == paper["id"] and e["location"].get("section") == "Conclusion" for e in fb["evidence"]))
        self.assertEqual(fb["gaps"], [])

    def test_an_article_is_reached_even_when_the_search_phrase_is_broad(self):
        self.wikipedia()
        s, b = self.research("What is business class on airlines?", web=True, providers=["wikipedia"])
        art = next(x for x in b["sources"] if x.get("provider") == "wikipedia" and x["status"] == "ok")
        self.assertEqual(art["title"], "Business class")
        self.assertTrue(art["content_scope"].startswith("the article's lead"))
        self.assertTrue(art["meta"]["permalink"].startswith("https://en.wikipedia.org/"))
        self.assertIn("CC BY-SA", art["license"])


class WhenThingsGoWrong(CombinedBase):
    def test_a_refused_arxiv_and_a_misconfigured_searxng_are_reported_and_nothing_is_made_up(self):
        self.wikipedia()
        self.web.routes["/api/query"] = (403, {"Content-Type": "text/plain"}, b"forbidden")
        self.web.routes["/search"] = (403, {"Content-Type": "text/html"}, b"<html>Forbidden</html>")
        self.allow()
        s, b = self.research("What is business class on airlines?", web=True, providers=["wikipedia", "arxiv", "searxng"], arxiv_mode="always")             # arXiv is not searched for this subject unless asked
        msgs = " ".join(f["message"] for f in b["collection"]["failures"])
        self.assertTrue(any(f["provider"] == "arxiv" for f in b["collection"]["failures"] if f.get("stage") == "search"), b["collection"]["failures"])
        self.assertTrue(any(f["provider"] == "searxng" for f in b["collection"]["failures"] if f.get("stage") == "search"))
        self.assertIn("json", msgs.lower())                                                         # the SearXNG failure says what to change, not just "blocked"
        self.assertFalse([x for x in b["sources"] if x.get("provider") in ("arxiv", "searxng")])
        self.assertTrue([x for x in b["sources"] if x.get("provider") == "wikipedia"])               # one provider failing does not lose the others

    def test_nothing_found_is_said_plainly_and_no_conclusion_is_invented(self):
        self.web.routes["/w/api.php"] = (200, {"Content-Type": "application/json"}, json.dumps({"batchcomplete": True, "query": {"search": []}}).encode())
        self.web.routes["/api/query"] = lambda h: (200, {"Content-Type": "application/atom+xml"}, b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom' xmlns:opensearch='http://a9.com/-/spec/opensearch/1.1/'><opensearch:totalResults>0</opensearch:totalResults></feed>")
        self.searx([])
        self.allow()
        s, b = self.research("What is quantum chromodynamics lattice gauge theory?", web=True, providers=["wikipedia", "arxiv", "searxng"])
        self.assertEqual(b["conclusions"], [])
        self.assertEqual(b["status"], "insufficient")
        self.assertTrue(b["gaps"])
        self.assertEqual(b["quality"]["verdict"], "insufficient")

    def test_instructions_hidden_in_an_article_are_set_aside_and_never_used(self):
        self.wikipedia(SEATS_ARTICLE.replace("<p>Fare class strongly", f"<p>{INJECTED}</p><p>Fare class strongly"))
        self.allow(("wikipedia",))
        s, b = self.research("What is business class on airlines and how do fares and seats differ?", web=True, providers=["wikipedia"])
        blob = json.dumps({"e": b["evidence"], "c": b["conclusions"], "r": b["report"]})
        self.assertNotIn("Ignore all previous", blob)
        self.assertTrue(b["removed"])
        self.assertEqual(self.check(b, "untrusted_content")["status"], "warn")


class DirectProviderEndpoints(CombinedBase):
    def setUp(self):
        super().setUp()
        self.wikipedia()
        self.arxiv(relevant=True)
        self.searx([{"title": "Airline seat inventory", "url": self.aviation_page(), "content": "business class seats"}])

    def test_each_endpoint_is_refused_until_web_research_is_switched_on(self):
        for call in (lambda: self.svc.provider_search(self.alice, self.pid, {"provider": "wikipedia", "query": "business class"}),
                     lambda: self.svc.provider_open(self.alice, self.pid, {"provider": "wikipedia", "ref": {"title": "Business class"}}),
                     lambda: self.svc.diagnose_providers(self.alice, self.pid, {})):
            with self.assertRaises(AppError) as cm:
                call()
            self.assertEqual(cm.exception.code, "WEB_NOT_ALLOWED")
        self.assertEqual(self.web.hits, [])

    def test_search_and_open_wikipedia_arxiv_and_a_page(self):
        self.allow()
        hits = self.svc.provider_search(self.alice, self.pid, {"provider": "wikipedia", "query": "business class"})
        self.assertEqual(hits["hits"][0]["title"], "Business class")
        self.assertTrue(hits["hits"][0]["source_label"].startswith("Wikipedia"))
        art = self.svc.provider_open(self.alice, self.pid, {"provider": "wikipedia", "ref": {"title": "Business class"}})
        self.assertIn("Fares and seats", [s["heading"] for s in art["sections"]])
        self.assertTrue(art["attribution"] and art["permalink"])
        papers = self.svc.provider_search(self.alice, self.pid, {"provider": "arxiv", "query": "attention transformer"})
        self.assertEqual(papers["hits"][0]["source_label"], "arXiv preprint (not peer reviewed)")
        paper = self.svc.provider_open(self.alice, self.pid, {"provider": "arxiv", "ref": {"arxiv_id": "2101.00001"}})
        self.assertTrue(paper["full_text"])
        self.assertIn("Conclusion", [s["heading"] for s in paper["sections"]])
        self.assertTrue(paper["pdf_url"].endswith("2101.00001v2"))
        pg = self.svc.provider_open(self.alice, self.pid, {"provider": "searxng", "ref": {"url": "https://aviation.test/aviation-seats"}})
        self.assertIn("business class", pg["sections"][0]["text"].lower())

    def test_a_private_looking_search_is_refused_before_anything_is_sent(self):
        self.allow()
        before = len(self.web.hits)
        for q in ("my account number 1234567890123 balance", "email me at alice@example.com about seats"):
            with self.assertRaises(AppError) as cm:
                self.svc.provider_search(self.alice, self.pid, {"provider": "wikipedia", "query": q})
            self.assertEqual(cm.exception.status_code, 422)
        self.assertEqual(len(self.web.hits), before)

    def test_a_provider_that_is_not_switched_on_is_refused_and_another_person_cannot_use_my_project(self):
        self.allow(("wikipedia",))
        with self.assertRaises(AppError) as cm:
            self.svc.provider_search(self.alice, self.pid, {"provider": "arxiv", "query": "attention"})
        self.assertEqual(cm.exception.code, "PROVIDER_NOT_ALLOWED")
        with self.assertRaises(AppError) as cm:
            self.svc.provider_search(self.bob, self.pid, {"provider": "wikipedia", "query": "business class"})
        self.assertEqual(cm.exception.status_code, 404)

    def test_diagnostics_report_each_provider_and_skip_the_ones_that_are_off(self):
        self.allow(("wikipedia", "searxng"))
        self.web.json("/search", {"results": [{"title": "t", "url": "https://x.test/a", "content": "c"}]})
        out = self.svc.diagnose_providers(self.alice, self.pid, {})
        by = {r["provider"]: r for r in out["results"]}
        self.assertEqual(by["arxiv"]["status"], "not_allowed")
        self.assertIn(by["wikipedia"]["status"], ("ok", "unreachable", "unexpected", "refused", "empty"))
        self.assertIn("fixed phrase", out["note"])


if __name__ == "__main__":
    unittest.main()
