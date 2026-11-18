"""R2 discovery: provider parsing (canned answers in the documented shapes), normalisation, duplicate detection, ranking, rate limiting, search history, library, compare and import.
Live behaviour of the real services is NOT tested here (no network in the build sandbox); scripts/verify_phase10_live.py does that."""
import os
import unittest
from unittest import mock

from p10_discovery_support import ARXIV_ATOM, CROSSREF_WORKS, OPENALEX_WORKS, S2_SEARCH, Routed, SearxFake, ok
from p10_support import FakeFetcher, RIHarness, make_pdf, page
from app.modules.research_intelligence.discovery.citation import apa, bibtex
from app.modules.research_intelligence.discovery.providers import Arxiv, Crossref, OpenAlex, ProviderGate, Query, SemanticScholar, default_providers
from app.modules.research_intelligence.discovery.records import Record, finish, merge_records, normalise_arxiv, normalise_doi, normalise_type, parse_date, rank_records, title_key
from app.modules.research_intelligence.discovery.service import DiscoveryService
from app.modules.research_intelligence.security.urlguard import UrlRejected
from app.modules.workspace.errors import AppError


def http(status, retry_after=None):
    return UrlRejected("URL_HTTP_ERROR", f"The site answered with an error ({status}).", status=status, retry_after=retry_after)


def standard_fetcher():
    return Routed(("api.crossref.org/works", ok(CROSSREF_WORKS)), ("api.openalex.org/works", ok(OPENALEX_WORKS)), ("export.arxiv.org", ok(ARXIV_ATOM, "application/atom+xml")),
                  ("api.semanticscholar.org/graph/v1/paper/search", ok(S2_SEARCH)))


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class Base(unittest.TestCase):
    def setUp(self):
        self.h = RIHarness(searxng_url="http://127.0.0.1:8080")
        self.addCleanup(self.h.close)
        self.alice = self.h.user("alice@example.com")
        self.clock = Clock()
        self.gate = ProviderGate(clock=self.clock.now, sleep=self.clock.sleep)

    def svc(self, fetcher=None, searx=None, **kw):
        return DiscoveryService(self.h.ctx(), fetcher=fetcher or standard_fetcher(), gate=kw.pop("gate", self.gate), searx_fetcher_factory=(lambda s: searx or SearxFake()), **kw)

    def search(self, svc=None, **body):
        body.setdefault("query", "artificial intelligence employment")
        return (svc or self.svc()).search(self.alice, body)

    def find(self, res, needle):
        return next(i for i in res["items"] if needle.lower() in i["title"].lower())


class Normalisation(unittest.TestCase):
    def test_doi_arxiv_dates_types_titles(self):
        for raw in ("10.1000/ABC.1", "doi:10.1000/abc.1", "https://doi.org/10.1000/abc.1", "http://dx.doi.org/10.1000/ABC.1."):
            self.assertEqual(normalise_doi(raw), "10.1000/abc.1", raw)
        self.assertIsNone(normalise_doi("not a doi"))
        self.assertIsNone(normalise_doi(None))
        self.assertEqual(normalise_arxiv("http://arxiv.org/abs/2105.01234v2"), "2105.01234")
        self.assertEqual(normalise_arxiv("arXiv:hep-th/9901001v1"), "hep-th/9901001")
        self.assertEqual(parse_date("2021-5-3T10:00:00Z"), ("2021-05-03", 2021))
        self.assertEqual(parse_date(2020), ("2020", 2020))
        self.assertEqual(parse_date("yesterday"), (None, None))
        self.assertEqual(normalise_type("posted-content"), "preprint")
        self.assertEqual(normalise_type("JournalArticle"), "journal-article")
        self.assertEqual(normalise_type("weird"), "other")
        self.assertEqual(title_key("  Deep-Learning: A Survey!! "), "deep learning a survey")

    def test_finish_drops_unusable_and_cleans(self):
        self.assertIsNone(finish(Record(title="  <b> </b> ")))
        r = finish(Record(title="A <i>Title</i> &amp; more", authors=["A B", "A B", ""], abstract="<p>Text</p>", url="javascript:alert(1)", oa_url="ftp://x/y", citation_count=-4))
        self.assertEqual(r.title, "A Title & more")
        self.assertEqual(r.authors, ["A B"])
        self.assertEqual(r.abstract, "Text")
        self.assertIsNone(r.url)
        self.assertIsNone(r.oa_url)
        self.assertIsNone(r.citation_count)


class ProviderParsing(Base):
    def test_crossref_record_and_open_access_only_with_cc_licence(self):
        res = Crossref().search(standard_fetcher(), Query(q="ai employment"), self.h.ctx().settings)
        a, b = res.records
        self.assertEqual((a.doi, a.year, a.pub_type, a.venue), ("10.1000/ai.jobs.1", 2021, "journal-article", "Journal of Labour Economics"))
        self.assertEqual(a.authors, ["Maria Silva", "John Okafor"])
        self.assertTrue(a.abstract.startswith("We study how"), a.abstract)                      # JATS markup and the leading word "Abstract" removed
        self.assertEqual(a.oa_url, "https://example.org/ai-jobs.pdf")                           # CC licence + PDF link
        self.assertIsNone(b.oa_url)                                                              # a PDF link without an open licence is NOT claimed as open
        self.assertEqual((a.citation_count, a.citation_source), (120, "crossref"))
        self.assertEqual(b.authors, ["Research Consortium"])

    def test_crossref_query_uses_filters_and_polite_mailto(self):
        f = standard_fetcher()
        s = self.h.ctx().settings
        import dataclasses
        s = dataclasses.replace(s, ri_crossref_mailto="me@example.org")
        Crossref().search(f, Query(q="ai employment", author="Silva", year_from=2020, year_to=2022, pub_type="journal-article", sort="citations"), s)
        url = f.calls[0][0]
        for part in ("query.author=Silva", "from-pub-date%3A2020-01-01", "until-pub-date%3A2022-12-31", "type%3Ajournal-article", "sort=is-referenced-by-count", "mailto=me%40example.org"):
            self.assertIn(part, url)

    def test_openalex_abstract_rebuilt_and_key_sent_but_never_reported(self):
        f = standard_fetcher()
        with mock.patch.dict(os.environ, {"PREDICTAI_RI_OPENALEX_API_KEY": "SECRET-KEY-123"}):
            res = OpenAlex().search(f, Query(q="ai employment", year_from=2020), self.h.ctx().settings)
        a, b = res.records
        self.assertEqual(a.abstract, "We study how artificial intelligence changed employment.")
        self.assertEqual((a.doi, a.arxiv_id, a.pub_type, a.oa_url), ("10.1000/ai.jobs.1", None, "journal-article", "https://repo.example/ai-jobs.pdf"))
        self.assertIsNone(b.abstract)
        self.assertEqual(b.pub_type, "preprint")
        self.assertIn("api_key=SECRET-KEY-123", f.calls[0][0])
        self.assertNotIn("SECRET-KEY-123", repr(res.public()))
        self.assertEqual(res.notes, [])

    def test_openalex_without_a_key_says_so(self):
        with mock.patch.dict(os.environ, {"PREDICTAI_RI_OPENALEX_API_KEY": ""}):
            res = OpenAlex().search(standard_fetcher(), Query(q="x"), self.h.ctx().settings)
        self.assertTrue(any("API key" in n for n in res.notes))

    def test_openalex_author_search_resolves_profiles_first(self):
        f = Routed(("api.openalex.org/authors", ok({"results": [{"id": "https://openalex.org/A123"}, {"id": "bad"}]})), ("api.openalex.org/works", ok(OPENALEX_WORKS)))
        res = OpenAlex().search(f, Query(author="Maria Silva"), self.h.ctx().settings)
        self.assertEqual(len(res.records), 2)
        self.assertIn("authorships.author.id%3AA123", f.calls[1][0])
        self.assertTrue(any("different people can share a name" in n for n in res.notes))
        none = OpenAlex().search(Routed(("authors", ok({"results": []}))), Query(author="Nobody"), self.h.ctx().settings)
        self.assertEqual((none.status, none.records), ("ok", []))

    def test_arxiv_entries_are_always_open_preprints_and_xml_entities_are_refused(self):
        res = Arxiv().search(standard_fetcher(), Query(q="ai employment", field="econ.GN", year_from=2021), self.h.ctx().settings)
        a, b = res.records
        self.assertEqual((a.arxiv_id, a.pub_type, a.oa_url), ("2105.01234", "preprint", "https://arxiv.org/pdf/2105.01234"))
        self.assertEqual(b.doi, "10.1000/nn.labour.1")
        self.assertIn("preprints", " ".join(res.notes))
        bad = Routed(("arxiv", ok('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed/>', "application/atom+xml")))
        gate = ProviderGate(clock=self.clock.now, sleep=self.clock.sleep)
        out = Arxiv().run(bad, Query(q="x"), self.h.ctx().settings, gate)
        self.assertEqual(out.status, "bad_answer")
        skipped = Arxiv().search(standard_fetcher(), Query(q="x", pub_type="journal-article"), self.h.ctx().settings)
        self.assertEqual(skipped.status, "skipped")
        f = standard_fetcher()
        Arxiv().search(f, Query(q="deep learning", author="Chen", title="Hiring"), self.h.ctx().settings)
        self.assertIn("au%3A%22Chen%22", f.calls[0][0])
        self.assertIn("ti%3A%22Hiring%22", f.calls[0][0])

    def test_semantic_scholar_parses_sends_key_in_a_header_and_cannot_search_by_author_alone(self):
        f = standard_fetcher()
        with mock.patch.dict(os.environ, {"PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY": "S2-SECRET"}):
            res = SemanticScholar().search(f, Query(q="ai employment", year_from=2018, year_to=2021, pub_type="journal-article", open_access=True), self.h.ctx().settings)
        a, b = res.records
        self.assertEqual((a.doi, a.oa_url, a.citation_count, a.citation_source), ("10.1000/ai.jobs.1", "https://s2.example/ai-jobs.pdf", 140, "semanticscholar"))
        self.assertEqual(b.pub_type, "review")
        url, headers = f.calls[0]
        self.assertEqual(headers.get("x-api-key"), "S2-SECRET")
        self.assertNotIn("S2-SECRET", url)
        self.assertIn("year=2018-2021", url)
        self.assertTrue(url.endswith("&openAccessPdf"))
        self.assertEqual(SemanticScholar().search(f, Query(author="Silva"), self.h.ctx().settings).status, "skipped")

    def test_doi_lookup_uses_each_providers_record_endpoint(self):
        f = Routed(("api.crossref.org/works/10.1000", ok({"message": CROSSREF_WORKS["message"]["items"][0]})), ("api.semanticscholar.org/graph/v1/paper/DOI:", ok(S2_SEARCH["data"][0])),
                   ("api.openalex.org/works", ok(OPENALEX_WORKS)))
        s = self.h.ctx().settings
        q = Query(doi="https://doi.org/10.1000/AI.JOBS.1")
        q.doi = normalise_doi(q.doi)
        self.assertEqual(Crossref().search(f, q, s).records[0].doi, "10.1000/ai.jobs.1")
        self.assertEqual(SemanticScholar().search(f, q, s).records[0].title[:10], "Artificial")
        self.assertEqual(Arxiv().search(f, q, s).status, "skipped")

    def test_searxng_results_are_web_pages_not_papers(self):
        res = self.search(self.svc(), providers=["searxng"])
        self.assertEqual(len(res["items"]), 1)
        it = res["items"][0]
        self.assertEqual((it["kind"], it["type"], it["access"]), ("web", "web-page", "abstract"))
        self.assertNotIn("utm_source", it["url"])
        self.assertTrue(any("not peer-reviewed" in n for n in res["notes"]))


class Merging(unittest.TestCase):
    def rec(self, title, provider, **kw):
        return Record(title=title, providers={}, **kw)

    def test_same_doi_across_providers_becomes_one_record_with_both_providers(self):
        a = finish(Record(title="Paper X", doi="10.1000/x", abstract="short", citation_count=5, citation_source="crossref"))
        b = finish(Record(title="Paper X", doi="https://doi.org/10.1000/X", abstract="a much longer abstract here", oa_url="https://o.example/x.pdf", citation_count=9, citation_source="openalex"))
        m = merge_records([("crossref", [a]), ("openalex", [b])])
        self.assertEqual(len(m), 1)
        r = m[0]
        self.assertEqual(sorted(r.providers), ["crossref", "openalex"])
        self.assertEqual(r.abstract, "a much longer abstract here")
        self.assertEqual((r.oa_url, r.citation_count, r.citation_source), ("https://o.example/x.pdf", 9, "openalex"))

    def test_preprint_and_published_version_merge_and_take_the_published_type(self):
        pre = finish(Record(title="Artificial Intelligence and Employment: Evidence from Firms", authors=["Maria Silva"], arxiv_id="2105.01234", pub_type="preprint", published="2021-05-03"))
        pub = finish(Record(title="Artificial intelligence and employment: evidence from firms", authors=["Silva, Maria"], doi="10.1000/ai.jobs.1", pub_type="journal-article", published="2021"))
        m = merge_records([("arxiv", [pre]), ("crossref", [pub])])
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0].pub_type, m[0].doi, m[0].arxiv_id), ("journal-article", "10.1000/ai.jobs.1", "2105.01234"))

    def test_different_works_are_not_merged(self):
        a = finish(Record(title="Automation and Wages in Manufacturing", authors=["A Smith"], year=2019))
        b = finish(Record(title="Automation and Wages in Manufacturing", authors=["A Smith"], year=2005))       # same title, 14 years apart: not the same work
        c = finish(Record(title="Automation and Wages in Services", authors=["A Smith"], year=2019))
        d = finish(Record(title="Automation and Wages in Manufacturing", authors=["B Jones"], year=2019))
        self.assertEqual(len(merge_records([("p", [a, b, c, d])])), 4)

    def test_doi_never_replaced_by_a_different_doi(self):
        a = finish(Record(title="Same Long Title Of A Paper", authors=["A B"], doi="10.1000/one", year=2020))
        b = finish(Record(title="Same Long Title Of A Paper", authors=["A B"], doi="10.1000/two", year=2020))
        m = merge_records([("p", [a]), ("q", [b])])
        self.assertEqual(m[0].doi, "10.1000/one")

    def test_ranking_is_transparent_and_sortable(self):
        t = finish(Record(title="Artificial intelligence employment effects", abstract="x", providers={"a": {"rank": 5}}))
        ab = finish(Record(title="Labour markets", abstract="artificial intelligence and employment in firms", providers={"a": {"rank": 1}}, citation_count=500))
        none = finish(Record(title="Unrelated gardening", providers={"a": {"rank": 2}}))
        ranked = rank_records([none, ab, t], ["artificial", "intelligence", "employment"])
        self.assertEqual([r.title for r, _, _ in ranked][0], "Artificial intelligence employment effects")
        self.assertEqual(ranked[-1][0].title, "Unrelated gardening")
        self.assertIn("all search terms in the title", ranked[0][2])
        by_cit = rank_records([none, ab, t], [], sort="citations")
        self.assertEqual(by_cit[0][0].title, "Labour markets")


class Searching(Base):
    def test_search_merges_providers_and_reports_each_status(self):
        res = self.search()
        self.assertEqual({p["provider"]: p["status"] for p in res["providers"]}, {"crossref": "ok", "openalex": "ok", "arxiv": "ok", "semanticscholar": "ok", "searxng": "ok"})
        top = self.find(res, "Evidence from Firms")
        self.assertEqual(sorted(top["providers"]), ["arxiv", "crossref", "openalex", "semanticscholar"])
        self.assertEqual(top["type"], "journal-article")
        self.assertEqual(top["doi"], "10.1000/ai.jobs.1")
        self.assertEqual(top["arxiv_id"], "2105.01234")
        self.assertEqual(res["items"][0]["rid"], top["rid"])
        titles = [i["title"] for i in res["items"]]
        self.assertEqual(len(titles), len(set(t.lower() for t in titles)))
        self.assertIn("(2021)", top["citation"]["apa"])
        self.assertTrue(top["citation"]["bibtex"].startswith("@article{silva2021"))

    def test_one_provider_failing_does_not_hide_the_others_and_is_explained(self):
        f = Routed(("api.crossref.org", http(429, retry_after=45)), ("api.openalex.org", http(403)), ("export.arxiv.org", ok(ARXIV_ATOM, "application/atom+xml")),
                   ("api.semanticscholar.org", UrlRejected("URL_FETCH_FAILED", "The page could not be retrieved.")))
        with mock.patch.dict(os.environ, {"PREDICTAI_RI_OPENALEX_API_KEY": "KEEP-ME-SECRET"}):
            res = self.search(self.svc(f), providers=["crossref", "openalex", "arxiv", "semanticscholar"])
        st = {p["provider"]: p for p in res["providers"]}
        self.assertEqual(st["crossref"]["status"], "rate_limited")
        self.assertEqual(st["crossref"]["retry_after_s"], 45)
        self.assertEqual(st["openalex"]["status"], "key_rejected")
        self.assertIn("PREDICTAI_RI_OPENALEX_API_KEY", st["openalex"]["fix"])
        self.assertEqual(st["semanticscholar"]["status"], "unreachable")
        self.assertEqual(st["arxiv"]["status"], "ok")
        self.assertEqual(len(res["items"]), 2)
        self.assertTrue(any("incomplete" in n for n in res["notes"]))
        self.assertNotIn("KEEP-ME-SECRET", repr(res))

    def test_all_providers_failing_says_so_plainly(self):
        f = Routed(("", http(503)))
        res = self.search(self.svc(f), providers=["crossref", "openalex"])
        self.assertEqual(res["items"], [])
        self.assertTrue(any("No provider answered" in n for n in res["notes"]))

    def test_unparseable_answer_is_reported_not_raised(self):
        f = Routed(("api.crossref.org", ok(b"<html>maintenance</html>", "application/json")), ("api.openalex.org", ok({"results": [{"display_name": 5, "authorships": "x"}]})))
        res = self.search(self.svc(f), providers=["crossref", "openalex"])
        self.assertEqual({p["provider"]: p["status"] for p in res["providers"]}["crossref"], "bad_answer")

    def test_a_rate_limited_provider_is_not_called_again_during_its_cool_down(self):
        f = Routed(("api.crossref.org", [http(429, retry_after=60), ok(CROSSREF_WORKS)]))
        svc = self.svc(f)
        first = self.search(svc, providers=["crossref"])
        second = self.search(svc, providers=["crossref"])
        self.assertEqual(f.count("api.crossref.org"), 1)
        self.assertEqual(second["providers"][0]["status"], "rate_limited")
        self.clock.t += 120
        third = self.search(svc, providers=["crossref"])
        self.assertEqual(third["providers"][0]["status"], "ok")
        self.assertEqual(first["items"], [])

    def test_arxiv_polite_interval_waits_briefly_then_refuses_rather_than_hanging(self):
        svc = self.svc()
        self.search(svc, providers=["arxiv"])
        self.search(svc, providers=["arxiv"])
        self.assertTrue(any(2.9 <= s <= 3.1 for s in self.clock.slept), self.clock.slept)       # waited the 3 s the arXiv manual asks for
        gate = ProviderGate(clock=self.clock.now, sleep=self.clock.sleep, max_wait_s=1.0)
        svc2 = self.svc(gate=gate)
        self.search(svc2, providers=["arxiv"])
        r = self.search(svc2, providers=["arxiv"])
        self.assertEqual(r["providers"][0]["status"], "rate_limited")
        self.assertIn("polite request interval", r["providers"][0]["message"])

    def test_validation(self):
        svc = self.svc()
        for body, field in (({}, "query"), ({"query": "x", "year_from": 2022, "year_to": 2020}, "year_to"), ({"query": "x", "type": "poem"}, "type"), ({"query": "x", "sort": "magic"}, "sort"),
                            ({"doi": "nonsense"}, "doi"), ({"query": "x", "providers": ["bing"]}, "providers"), ({"query": "x" * 500}, "query")):
            with self.assertRaises(AppError, msg=str(body)):
                svc.search(self.alice, body)
        self.assertEqual(self.h.ctx().conn.execute("SELECT COUNT(*) FROM ri_discovery_searches").fetchone()[0], 0)

    def test_filters_that_providers_cannot_apply_run_afterwards_and_are_counted(self):
        res = self.search(author="Okafor", providers=["crossref", "openalex"])
        self.assertTrue(res["items"])
        self.assertTrue(all(any("okafor" in a.lower() for a in i["authors"]) for i in res["items"]))
        self.assertTrue(any("author filter" in n for n in res["notes"]))
        res = self.search(year_from=2020, providers=["crossref", "semanticscholar"])
        self.assertTrue(all(i["year"] and i["year"] >= 2020 for i in res["items"]))
        res = self.search(field="computer", providers=["openalex"])
        self.assertEqual([i["title"] for i in res["items"]], ["Deep Learning for Hiring Prediction"])
        res = self.search(open_access=True, providers=["crossref", "openalex"])
        self.assertTrue(all(i["oa_url"] for i in res["items"]))

    def test_pagination_filters_and_sorting_come_from_the_stored_search(self):
        f = standard_fetcher()
        svc = self.svc(f)
        s = self.search(svc)
        calls = len(f.calls)
        total = s["total"]
        self.assertGreaterEqual(total, 5)
        p1 = svc.get_search(self.alice, s["search_id"], page=1, page_size=5)
        p2 = svc.get_search(self.alice, s["search_id"], page=2, page_size=5)
        self.assertEqual((p1["page"], p1["pages"], len(p1["items"])), (1, -(-total // 5), 5))
        self.assertFalse({i["rid"] for i in p1["items"]} & {i["rid"] for i in p2["items"]})
        self.assertEqual(svc.get_search(self.alice, s["search_id"], page=99, page_size=5)["page"], p1["pages"])
        oa = svc.get_search(self.alice, s["search_id"], open_access=True)
        self.assertTrue(oa["items"] and all(i["oa_url"] for i in oa["items"]))
        cited = svc.get_search(self.alice, s["search_id"], sort="citations")["items"]
        counts = [i["citation_count"] if i["citation_count"] is not None else -1 for i in cited]
        self.assertEqual(counts, sorted(counts, reverse=True))
        only = svc.get_search(self.alice, s["search_id"], provider="arxiv")
        self.assertTrue(only["items"] and all("arxiv" in i["providers"] for i in only["items"]))
        self.assertEqual(svc.get_search(self.alice, s["search_id"], within="robots")["total"], 1)
        self.assertEqual(len(f.calls), calls)                                                    # no provider was asked again
        with self.assertRaises(AppError):
            svc.get_search(self.alice, s["search_id"], sort="nope")
        bob = self.h.user("bob@example.com")
        with self.assertRaises(AppError):
            svc.get_search(bob, s["search_id"])

    def test_history_lists_deletes_clears_and_is_trimmed(self):
        h = RIHarness(searxng_url="", ri_discovery_history_max=10)
        self.addCleanup(h.close)
        alice = h.user("a@example.com")
        svc = DiscoveryService(h.ctx(), fetcher=standard_fetcher(), gate=self.gate)
        ids = [svc.search(alice, {"query": f"topic {i}", "providers": ["crossref"]})["search_id"] for i in range(12)]
        items = svc.history(alice)["items"]
        self.assertEqual(len(items), 10)
        self.assertNotIn(ids[0], [i["id"] for i in items])
        self.assertEqual(items[0]["query"]["query"], "topic 11")
        self.assertEqual(items[0]["providers"][0]["status"], "ok")
        svc.delete_search(alice, ids[-1])
        self.assertEqual(len(svc.history(alice)["items"]), 9)
        self.assertEqual(svc.clear_history(alice)["deleted"], 9)
        self.assertEqual(svc.history(alice)["items"], [])


class Library(Base):
    def setUp(self):
        super().setUp()
        self.svc_ = self.svc()
        self.res = self.search(self.svc_)

    def test_save_copies_the_servers_record_and_is_idempotent(self):
        top = self.find(self.res, "Evidence from Firms")
        saved = self.svc_.save(self.alice, self.res["search_id"], top["rid"])
        self.assertFalse(saved["already_saved"])
        self.assertEqual((saved["doi"], saved["title"]), (top["doi"], top["title"]))
        again = self.svc_.save(self.alice, self.res["search_id"], top["rid"])
        self.assertTrue(again["already_saved"])
        self.assertEqual(again["library_id"], saved["library_id"])
        self.assertEqual(self.svc_.library(self.alice)["total"], 1)
        self.assertTrue(self.svc_.get_search(self.alice, self.res["search_id"])["items"][0]["saved"] or any(i["saved"] for i in self.svc_.get_search(self.alice, self.res["search_id"])["items"]))
        with self.assertRaises(AppError):
            self.svc_.save(self.alice, self.res["search_id"], "0000000000000000")
        bob = self.h.user("bob@example.com")
        with self.assertRaises(AppError):
            self.svc_.save(bob, self.res["search_id"], top["rid"])
        self.assertEqual(self.svc_.library(bob)["total"], 0)
        with self.assertRaises(AppError):
            self.svc_.library_get(bob, saved["library_id"])

    def test_library_search_remove_and_compare(self):
        ids = [self.svc_.save(self.alice, self.res["search_id"], i["rid"])["library_id"] for i in self.res["items"][:3]]
        self.assertEqual(self.svc_.library(self.alice, query="employment")["total"], len([i for i in self.res["items"][:3] if "employment" in i["title"].lower()]))
        cmp = self.svc_.compare(self.alice, ids)
        self.assertEqual(len(cmp["items"]), 3)
        self.assertIn("titles, abstracts and bibliographic data only", cmp["note"])
        for bad in ([], ids[:1], [ids[0], ids[0]], ids + ["x", "y", "z", "w"], "nope"):
            with self.assertRaises(AppError):
                self.svc_.compare(self.alice, bad)
        with self.assertRaises(AppError):
            self.svc_.compare(self.h.user("bob@example.com"), ids[:2])
        self.assertEqual(self.svc_.library_remove(self.alice, ids[0])["deleted"], True)
        self.assertEqual(self.svc_.library(self.alice)["total"], 2)


class Importing(Base):
    def setUp(self):
        super().setUp()
        self.svc_ = self.svc()
        self.res = self.search(self.svc_)
        self.wid = self.h.workspace(self.alice)
        self.lib = {k: self.svc_.save(self.alice, self.res["search_id"], self.find(self.res, k)["rid"])["library_id"] for k in ("Evidence from Firms", "Automation and Wages", "Hiring Prediction")}

    def do_import(self, key, full_text, fetcher=None):
        out = self.svc_.import_to_workspace(self.alice, self.wid, self.lib[key], full_text, self.h.sources())
        self.h.run_jobs(fetcher=fetcher)
        return out, self.h.sources().get(self.alice, self.wid, out["id"])

    def test_abstract_import_downloads_nothing_and_says_so(self):
        fetcher = FakeFetcher()
        out, src = self.do_import("Evidence from Firms", False, fetcher)
        self.assertIn("full text is not downloaded", out["import_plan"])
        self.assertEqual(fetcher.calls, [])
        self.assertEqual((src["status"], src["access"], src["origin"], src["doi"]), ("ready", "abstract_only", "discovery", "10.1000/ai.jobs.1"))
        self.assertTrue(src["chunk_count"] >= 1)
        self.assertTrue(any("full text was not downloaded" in w for w in src["warnings"]))
        link = self.h.ctx().conn.execute("SELECT library_id FROM ri_library_links WHERE source_id = ?", (out["id"],)).fetchone()
        self.assertEqual(link["library_id"], self.lib["Evidence from Firms"])
        self.assertEqual(self.svc_.library_get(self.alice, self.lib["Evidence from Firms"])["in_workspaces"][0]["workspace_id"], self.wid)

    def test_record_without_abstract_or_open_text_is_metadata_only(self):
        _, src = self.do_import("Automation and Wages", True, FakeFetcher())
        self.assertEqual((src["status"], src["access"], src["chunk_count"]), ("ready", "metadata_only", 0))

    def test_open_full_text_is_downloaded_indexed_and_keeps_the_providers_metadata(self):
        pdf = make_pdf(["Methods. We surveyed 3,000 firms between 2015 and 2020 about robots and hiring.", "Results. Employment rose by 4 percent in adopting firms."])
        fetcher = FakeFetcher({"https://example.org/ai-jobs.pdf": page(pdf, "application/pdf", "https://example.org/ai-jobs.pdf")})
        out, src = self.do_import("Evidence from Firms", True, fetcher)
        self.assertIn("will try to download", out["import_plan"])
        self.assertEqual(fetcher.calls, ["https://example.org/ai-jobs.pdf"])
        self.assertEqual((src["status"], src["access"], src["format"]), ("ready", "full_text", "pdf"))
        self.assertEqual(src["title"], "Artificial Intelligence and Employment: Evidence from Firms")
        self.assertEqual(src["authors"], ["Maria Silva", "John Okafor"])
        self.assertEqual(src["doi"], "10.1000/ai.jobs.1")
        self.assertEqual(src["venue"], "Journal of Labour Economics")

    def test_when_the_open_text_cannot_be_used_the_abstract_is_kept_and_the_failure_is_stated(self):
        for err, label in ((http(403), "paywall"), (UrlRejected("URL_TYPE_NOT_ALLOWED", "That kind of file cannot be imported from a web address."), "type")):
            h = RIHarness(searxng_url="")
            self.addCleanup(h.close)
            alice = h.user("c@example.com")
            svc = DiscoveryService(h.ctx(), fetcher=standard_fetcher(), gate=ProviderGate(clock=self.clock.now, sleep=self.clock.sleep))
            res = svc.search(alice, {"query": "ai employment", "providers": ["crossref"]})
            lib = svc.save(alice, res["search_id"], self.find(res, "Evidence from Firms")["rid"])["library_id"]
            wid = h.workspace(alice)
            out = svc.import_to_workspace(alice, wid, lib, True, h.sources())
            h.run_jobs(fetcher=FakeFetcher({"https://example.org/ai-jobs.pdf": err}))
            src = h.sources().get(alice, wid, out["id"])
            self.assertEqual((src["status"], src["access"]), ("ready", "abstract_only"), label)
            self.assertTrue(any("open full text could not be used" in w for w in src["warnings"]), src["warnings"])
            self.assertIsNone(h.ctx().conn.execute("SELECT stored_name FROM ri_sources WHERE id = ?", (out["id"],)).fetchone()["stored_name"])

    def test_a_scanned_pdf_falls_back_to_the_abstract(self):
        fetcher = FakeFetcher({"https://example.org/ai-jobs.pdf": page(make_pdf([""]), "application/pdf", "https://example.org/ai-jobs.pdf")})
        _, src = self.do_import("Evidence from Firms", True, fetcher)
        self.assertEqual((src["status"], src["access"]), ("ready", "abstract_only"))

    def test_an_unsafe_open_access_address_is_never_fetched(self):
        conn = self.h.ctx().conn
        row = conn.execute("SELECT record FROM ri_library WHERE id = ?", (self.lib["Evidence from Firms"],)).fetchone()
        import json
        rec = json.loads(row["record"])
        rec["oa_url"] = "http://127.0.0.1:8080/secret.pdf"
        conn.execute("UPDATE ri_library SET record = ? WHERE id = ?", (json.dumps(rec), self.lib["Evidence from Firms"]))
        fetcher = FakeFetcher()
        out, src = self.do_import("Evidence from Firms", True, fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(src["access"], "abstract_only")

    def test_duplicates_in_the_workspace_are_refused_and_other_users_cannot_import(self):
        self.do_import("Evidence from Firms", False)
        with self.assertRaises(AppError) as cm:
            self.svc_.import_to_workspace(self.alice, self.wid, self.lib["Evidence from Firms"], False, self.h.sources())
        self.assertEqual(cm.exception.code, "DUPLICATE_SOURCE")
        bob = self.h.user("bob@example.com")
        with self.assertRaises(AppError):
            self.svc_.import_to_workspace(bob, self.wid, self.lib["Evidence from Firms"], False, self.h.sources())
        bob_ws = self.h.workspace(bob)
        with self.assertRaises(AppError):
            self.svc_.import_to_workspace(bob, bob_ws, self.lib["Evidence from Firms"], False, self.h.sources())

    def test_import_counts_against_the_daily_import_limit(self):
        h = RIHarness(searxng_url="", ri_imports_per_day_standard=1)
        self.addCleanup(h.close)
        alice = h.user("d@example.com")
        svc = DiscoveryService(h.ctx(), fetcher=standard_fetcher(), gate=ProviderGate(clock=self.clock.now, sleep=self.clock.sleep))
        res = svc.search(alice, {"query": "ai employment", "providers": ["crossref"]})
        wid = h.workspace(alice)
        libs = [svc.save(alice, res["search_id"], i["rid"])["library_id"] for i in res["items"]]
        svc.import_to_workspace(alice, wid, libs[0], False, h.sources())
        with self.assertRaises(AppError) as cm:
            svc.import_to_workspace(alice, wid, libs[1], False, h.sources())
        self.assertEqual(cm.exception.code, "DAILY_LIMIT_REACHED")

    def test_deleting_a_workspace_and_a_user_removes_discovery_rows(self):
        self.do_import("Evidence from Firms", False)
        conn = self.h.ctx().conn
        from app.modules.research_intelligence.core.purge import purge_user
        purge_user(conn, self.h.ctx().settings, self.h.ctx().clock.iso, self.alice.user_id)
        for t in ("ri_library", "ri_library_links", "ri_discovery_searches"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)


class Citations(unittest.TestCase):
    def test_citation_text_uses_only_reported_fields(self):
        r = finish(Record(title="A Study of Things", authors=["Maria Silva", "Okafor, John"], year=2021, venue="Journal X", doi="10.1000/x"))
        self.assertEqual(apa(r), "Silva, M., & Okafor, J. (2021). A Study of Things. Journal X. https://doi.org/10.1000/x")
        bare = finish(Record(title="Untitled Notes"))
        self.assertEqual(apa(bare), "(n.d.). Untitled Notes.")
        self.assertNotIn("author", bibtex(bare))
        self.assertIn("doi = {10.1000/x}", bibtex(r))
        self.assertTrue(bibtex(r).startswith("@misc{silva2021study") or bibtex(r).startswith("@misc"))


if __name__ == "__main__":
    unittest.main()
