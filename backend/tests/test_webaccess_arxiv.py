"""arXiv through its official API: query construction and validation, Atom parsing (metadata, versions, DOI, journal reference), the preprint label, full text from HTML and PDF,
the abstract fallback, robots, the terms-of-use gate and every failure mode.
A local FakeWeb answers in the documented shapes; the real arXiv is NOT contacted, so these tests show that the adapter builds the right requests and reads the documented
answers, not that the live service behaves identically."""
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.modules.webaccess.fetcher import FetchError, SafeFetcher
from app.modules.webaccess.gate import GATES, HostGate
from app.modules.webaccess.providers import PROVIDERS
from app.modules.webaccess.providers.arxiv import build_query, check_syntax, peer_review, split_version
from app.modules.webaccess.service import WebAccess, build_fetcher

from web_support import FakeWeb, RoutedTransport

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731
A = PROVIDERS["arxiv"]


class Settings:
    searxng_url = ""
    research_contact = ""
    research_fetch_timeout_s = 5
    research_allow_private_network = False
    ocr_enabled = False
    ocr_languages = "eng"


def fetcher_for(web, **kw):
    return SafeFetcher(user_agent="PredictAI-Test/1 (test@example.org)", resolver=PUBLIC, transport=RoutedTransport(web), sleeper=lambda s: None, host_interval_s=0, **kw)


def entry(ident="2101.00001v2", title="A study of attention", summary="We study attention. It helps.", extra=""):
    return (f"<entry><id>http://arxiv.org/abs/{ident}</id><updated>2021-03-02T00:00:00Z</updated><published>2021-01-01T00:00:00Z</published><title>{title}</title><summary>{summary}</summary>"
            f"<author><name>Ada L.</name><arxiv:affiliation>Univ A</arxiv:affiliation></author><author><name>Alan T.</name></author>"
            f"<arxiv:comment>12 pages</arxiv:comment><link href=\"http://arxiv.org/abs/{ident}\" rel=\"alternate\" type=\"text/html\"/>"
            f"<link title=\"pdf\" href=\"http://arxiv.org/pdf/{ident}\" rel=\"related\" type=\"application/pdf\"/>"
            f"<arxiv:primary_category term=\"cs.LG\" scheme=\"http://arxiv.org/schemas/atom\"/><category term=\"cs.LG\"/><category term=\"stat.ML\"/>{extra}</entry>")


def feed(*entries, total=None):
    t = f"<opensearch:totalResults>{total if total is not None else len(entries)}</opensearch:totalResults>"
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\" "
            f"xmlns:opensearch=\"http://a9.com/-/spec/opensearch/1.1/\">{t}{''.join(entries)}</feed>").encode()


def atom(body, status=200):
    return status, {"Content-Type": "application/atom+xml; charset=utf-8"}, body


class Gated(unittest.TestCase):
    """The 3-second gate is a real protection; tests switch the spacing off (never the cool-down logic) so they do not sleep."""

    def setUp(self):
        self._spacing = {k: g.min_interval_s for k, g in GATES.items()}
        for g in GATES.values():
            g.min_interval_s = 0.0
            g.reset()

    def tearDown(self):
        for k, g in GATES.items():
            g.min_interval_s = self._spacing[k]
            g.reset()


class Syntax(unittest.TestCase):
    def test_modes_build_documented_field_queries(self):
        self.assertEqual(build_query("title", "Attention is all you need"), 'ti:"Attention is all you need"')
        self.assertEqual(build_query("author", "Vaswani"), "au:Vaswani")
        self.assertEqual(build_query("abstract", "graph neural networks"), 'abs:"graph neural networks"')
        self.assertEqual(build_query("category", "", "cs.LG"), "cat:cs.LG")
        self.assertIn("AND cat:stat.ML", build_query("keywords", "neural nets", "stat.ML"))
        self.assertEqual(build_query("title", 'say "hi"'), 'ti:"say hi"')                          # a quote typed by the person cannot unbalance the query

    def test_bad_input_is_a_clear_error_not_a_blank_search(self):
        for mode, text, cat in [("keywords", "the of and", ""), ("category", "", ""), ("title", "x", "not a category!")]:
            with self.assertRaises(FetchError, msg=(mode, text)) as cm:
                build_query(mode, text, cat)
            self.assertEqual(cm.exception.code, "PROVIDER_ERROR")

    def test_check_syntax_accepts_field_queries_and_refuses_everything_else(self):
        self.assertEqual(check_syntax('ti:"business class" AND (abs:airlines OR abs:aviation)'), 'ti:"business class" AND (abs:airlines OR abs:aviation)')
        for bad in ['ti:"unbalanced', "(ti:a", "xx:foo", "ti:a; DROP TABLE", "ti:a\x00", "a" * 500, "TI:foo"]:
            with self.assertRaises(FetchError, msg=bad):
                check_syntax(bad)

    def test_versions_and_peer_review_labels(self):
        self.assertEqual(split_version("2101.00001v3"), ("2101.00001", 3))
        self.assertEqual(split_version("hep-th/9901001"), ("hep-th/9901001", None))
        self.assertEqual(peer_review({})[0], "preprint")
        status, note = peer_review({"journal_ref": "Nature 5 (2020) 1"})
        self.assertEqual(status, "published_claim")
        self.assertIn("not verified", note)
        self.assertEqual(peer_review({"doi": "10.1/x"})[0], "published_claim")


class Search(Gated):
    def test_the_query_uses_field_syntax_and_sends_the_documented_parameters(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry()))
            hits = A.search(fetcher_for(web), Settings(), "business class seat availability airlines", 5)
            q = parse_qs(urlsplit(web.hits[0]).query)
        self.assertEqual(len(hits), 1)
        self.assertIn("ti:", q["search_query"][0])
        self.assertIn("abs:", q["search_query"][0])
        self.assertEqual((q["max_results"], q["start"], q["sortBy"]), (["5"], ["0"], ["relevance"]))

    def test_hits_carry_the_full_metadata_and_the_preprint_label(self):
        extra = "<arxiv:doi>10.1000/xyz</arxiv:doi><arxiv:journal_ref>J. Tests 5 (2022) 1-9</arxiv:journal_ref>"
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry(extra=extra), entry("hep-th/9901001v1", "Old style id")))
            hits = A.search(fetcher_for(web), Settings(), "attention", 5)
        h = hits[0]
        self.assertEqual((h.url, h.title, h.provider, h.source_type, h.publisher), ("https://arxiv.org/abs/2101.00001v2", "A study of attention", "arxiv", "preprint", "arXiv"))
        self.assertEqual((h.published, h.modified), ("2021-01-01T00:00:00Z", "2021-03-02T00:00:00Z"))
        e = h.extra
        self.assertEqual((e["arxiv_id"], e["version"], e["authors"], e["categories"], e["primary_category"]), ("2101.00001", 2, ["Ada L.", "Alan T."], ["cs.LG", "stat.ML"], "cs.LG"))
        self.assertEqual((e["doi"], e["journal_ref"], e["comment"], e["affiliations"]), ("10.1000/xyz", "J. Tests 5 (2022) 1-9", "12 pages", ["Univ A"]))
        self.assertEqual((e["pdf_url"], e["html_url"], e["abstract_url"]), ("https://arxiv.org/pdf/2101.00001v2", "https://arxiv.org/html/2101.00001v2", "https://arxiv.org/abs/2101.00001v2"))
        self.assertEqual((e["first_submitted"], e["latest_revision"]), ("2021-01-01T00:00:00Z", "2021-03-02T00:00:00Z"))
        self.assertEqual(e["peer_review"], "published_claim")                                      # a journal reference is the authors' claim, never "peer reviewed"
        self.assertIn("not verified", e["peer_review_note"])
        self.assertEqual(e["content"], "abstract")                                                 # a search hit is the abstract, and says so
        self.assertEqual(hits[1].extra["arxiv_id"], "hep-th/9901001")                              # old-style identifiers work
        self.assertEqual(hits[1].extra["peer_review"], "preprint")

    def test_the_query_is_loosened_only_when_the_first_form_finds_nothing(self):
        seen = []

        def route(h):
            q = parse_qs(urlsplit(h.path).query)["search_query"][0]
            seen.append(q)
            return atom(feed(entry()) if q.startswith("all:") else feed(total=0))
        with FakeWeb() as web:
            web.routes["/api/query"] = route
            hits = A.search(fetcher_for(web), Settings(), "attention in transformers", 5)
        self.assertEqual(len(hits), 1)
        self.assertTrue(seen[0].startswith("(ti:"))
        self.assertTrue(seen[-1].startswith("all:"))
        self.assertGreater(len(seen), 1)

    def test_an_identifier_or_url_is_looked_up_with_id_list(self):
        for query in ["2101.00001", "https://arxiv.org/abs/2101.00001v2", "arXiv:2101.00001v2", "https://arxiv.org/pdf/2101.00001v2"]:
            with FakeWeb() as web:
                web.routes["/api/query"] = lambda h: atom(feed(entry()))
                hits = A.search(fetcher_for(web), Settings(), query, 5)
                q = parse_qs(urlsplit(web.hits[0]).query)
            self.assertEqual(len(hits), 1, query)
            self.assertIn("id_list", q, query)
            self.assertNotIn("search_query", q, query)

    def test_explicit_field_queries_are_sent_as_given_after_validation(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry()))
            A.search(fetcher_for(web), Settings(), 'au:Vaswani AND ti:"attention"', 3)
            self.assertEqual(parse_qs(urlsplit(web.hits[0]).query)["search_query"], ['au:Vaswani AND ti:"attention"'])
        with FakeWeb() as web:
            for bad in ('ti:"unbalanced', "(ti:a", "ti:a; DROP TABLE"):
                with self.assertRaises(FetchError, msg=bad):
                    A.search(fetcher_for(web), Settings(), bad, 3)
            self.assertEqual(web.hits, [])                                                         # a query that cannot be valid is never sent
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry()))
            A.search(fetcher_for(web), Settings(), "zz:nope", 3)                                   # an unknown prefix is just words: it is searched as words, never as a field
            self.assertNotIn("zz", parse_qs(urlsplit(web.hits[0]).query)["search_query"][0])

    def test_no_results_is_an_empty_list_not_an_error(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(total=0))
            self.assertEqual(A.search(fetcher_for(web), Settings(), "zxqv", 5), [])

    def test_arxivs_own_error_message_is_reported(self):
        err = ("<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_1234</id><title>Error</title><summary>incorrect id format for 1234</summary>"
               "<updated>2021-01-01T00:00:00Z</updated></entry>")
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(err))
            with self.assertRaises(FetchError) as cm:
                A.search(fetcher_for(web), Settings(), "2101.00001", 3)
        self.assertEqual(cm.exception.code, "PROVIDER_ERROR")
        self.assertIn("incorrect id format", cm.exception.message)

    def test_non_arxiv_and_titleless_entries_never_become_hits(self):
        junk = "<entry><id>http://evil.example/abs/2101.00009</id><title>Fake</title><summary>x</summary></entry>"
        untitled = "<entry><id>http://arxiv.org/abs/2101.00008v1</id><title></title><summary>x</summary></entry>"
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(junk, untitled, entry()))
            hits = A.search(fetcher_for(web), Settings(), "attention", 5)
        self.assertEqual([h.extra["arxiv_id"] for h in hits], ["2101.00001"])

    def test_malformed_and_hostile_feeds(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>&a;</id></entry></feed>'
        for body in (bomb, b"<feed", b"not xml at all", b"<html><body>Service unavailable</body></html>"):
            with FakeWeb() as web:
                web.routes["/api/query"] = lambda h, b=body: atom(b)
                with self.assertRaises(FetchError, msg=body[:20]) as cm:
                    A.search(fetcher_for(web), Settings(), "attention", 3)
            self.assertEqual(cm.exception.code, "PROVIDER_BAD_ANSWER", body[:20])

    def test_the_agent_is_identified_and_nothing_private_is_sent(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry()))
            A.search(fetcher_for(web), Settings(), "attention", 3)
            seen = web.headers_seen[0]
        self.assertTrue(seen["user-agent"].startswith("PredictAI-Test"))
        self.assertNotIn("cookie", seen)
        self.assertNotIn("authorization", seen)


class Throttle(Gated):
    def test_a_rate_limit_is_reported_as_such_and_retried_once(self):
        calls = []

        def route(h):
            calls.append(1)
            return (429, {"Content-Type": "text/plain", "Retry-After": "1"}, b"slow down") if len(calls) == 1 else atom(feed(entry()))
        with FakeWeb() as web:
            web.routes["/api/query"] = route
            hits = A.search(fetcher_for(web), Settings(), "attention", 3)
        self.assertEqual((len(hits), len(calls)), (1, 2))
        with FakeWeb() as web:
            web.routes["/api/query"] = (429, {"Content-Type": "text/plain", "Retry-After": "2"}, b"slow down")
            with self.assertRaises(FetchError) as cm:
                A.search(fetcher_for(web), Settings(), "attention", 3)
        self.assertEqual(cm.exception.code, "RATE_LIMITED")

    def test_a_refusal_pauses_every_arxiv_request_for_a_while(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = (403, {"Content-Type": "text/plain"}, b"forbidden")
            with self.assertRaises(FetchError) as cm:
                A.search(fetcher_for(web), Settings(), "attention", 3)
            self.assertEqual(cm.exception.code, "PROVIDER_REFUSED")
            first = len(web.hits)
            with self.assertRaises(FetchError) as cm2:
                A.search(fetcher_for(web), Settings(), "something else", 3)
            self.assertEqual(cm2.exception.code, "PROVIDER_COOLDOWN")
            self.assertEqual(len(web.hits), first)                                                 # the second call never reached arXiv

    def test_a_refusal_from_the_website_pauses_the_api_too(self):
        with FakeWeb() as web:
            routes_for_paper(web)
            web.routes["/html/2101.00001v2"] = (403, {"Content-Type": "text/plain"}, b"no")
            A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
            with self.assertRaises(FetchError) as cm:
                A.search(fetcher_for(web), Settings(), "attention", 3)
        self.assertEqual(cm.exception.code, "PROVIDER_COOLDOWN")

    def test_the_gate_serialises_and_spaces_requests(self):
        now = [100.0]
        slept = []
        gate = HostGate(3.0, clock=lambda: now[0], sleeper=lambda s: (slept.append(round(s, 3)), now.__setitem__(0, now[0] + s)), label="t")
        with gate:
            pass
        now[0] += 1.0
        with gate:
            pass
        self.assertEqual(slept, [2.0])                                                              # one second had passed, so two more are waited
        gate.trip(60)
        with self.assertRaises(FetchError) as cm:
            gate.check()
        self.assertEqual(cm.exception.code, "PROVIDER_COOLDOWN")
        now[0] += 61
        gate.check()                                                                                # the pause ends by itself

    def test_the_shipped_gate_really_is_three_seconds(self):
        self.assertEqual(self._spacing, {"arxiv": 3.0, "arxiv-web": 15.0})                           # what the gates hold before this test file relaxes them: the API's 3 s and the website's 15 s crawl delay


def routes_for_paper(web, *, html=None, pdf=None, versions_page=None, latest=2, robots=None, entries=None):
    web.routes["/api/query"] = lambda h: atom(feed(*(entries or [entry(f"2101.00001v{latest}")])))
    if robots is not None:
        web.text("/robots.txt", robots)
    for v in range(1, latest + 1):
        if html is not None:
            web.routes[f"/html/2101.00001v{v}"] = (200, {"Content-Type": "text/html; charset=utf-8"}, html if isinstance(html, bytes) else html.encode())
        if pdf is not None:
            web.routes[f"/pdf/2101.00001v{v}"] = (200, {"Content-Type": "application/pdf"}, pdf)
    if versions_page is not None:
        web.routes["/abs/2101.00001"] = (200, {"Content-Type": "text/html; charset=utf-8"}, versions_page.encode())


VERSIONS_PAGE = ("<html><body><h1>Submission history</h1><p>[v1] Fri, 1 Jan 2021 00:00:00 UTC (100 KB)</p><p>[v2] Tue, 2 Mar 2021 00:00:00 UTC (110 KB)</p></body></html>")


class Load(Gated):
    html = (FIXTURES / "arxiv_html_paper.html").read_bytes()
    pdf = (FIXTURES / "arxiv_paper.pdf").read_bytes()

    def test_full_text_comes_from_the_html_version_with_sections_equations_and_tables(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, versions_page=VERSIONS_PAGE)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual((paper["kind"], paper["retrieved_via"], paper["full_text"], paper["tier"]), ("paper", "arxiv-html", True, "preprint"))
        self.assertEqual(paper["ref"], {"arxiv_id": "2101.00001", "version": 2})
        heads = [s["heading"] for s in paper["structure"]["sections"] if s["heading"]]
        self.assertEqual(heads[:3], ["Abstract", "Introduction", "Related Work"])
        self.assertIn("Conclusion", heads)
        eqs = [e for s in paper["structure"]["sections"] for e in s["equations"]]
        self.assertTrue(any("softmax" in (e.get("tex") or "") for e in eqs))                      # the equation arrives as TeX, not as a picture or garbage
        tables = [t for s in paper["structure"]["sections"] for t in s["tables"]]
        self.assertTrue(any("Transformer (big)" in json.dumps(t) and "28.4" in json.dumps(t) for t in tables))
        self.assertEqual(len(paper["structure"]["references"]), 1)
        self.assertEqual((paper["url"], paper["pdf_url"], paper["html_url"]), ("https://arxiv.org/abs/2101.00001v2", "https://arxiv.org/pdf/2101.00001v2", "https://arxiv.org/html/2101.00001v2"))
        self.assertIn("not endorsed by arXiv", paper["attribution"])
        self.assertIn("arXiv:2101.00001v2", paper["attribution"])

    def test_the_latest_version_is_marked_and_dated_from_the_api_with_no_extra_request(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, versions_page=VERSIONS_PAGE)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
            abs_page = [h for h in web.hits if h.startswith("/abs/")]
        self.assertTrue(paper["meta"]["is_latest"])
        self.assertEqual([(v["version"], v["latest"], v["submitted"]) for v in paper["versions"]], [(1, False, "2021-01-01T00:00:00Z"), (2, True, "2021-03-02T00:00:00Z")])
        self.assertEqual(abs_page, [])                                                              # the history page is another arxiv.org request (15 s crawl delay): only on request

    def test_the_full_submission_history_is_read_when_asked_for(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, versions_page=VERSIONS_PAGE)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001", "history": True})
        self.assertEqual([(v["version"], v["latest"]) for v in paper["versions"]], [(1, False), (2, True)])
        self.assertEqual(paper["versions"][0]["submitted"], "Fri, 1 Jan 2021 00:00:00 UTC")

    def test_middle_versions_are_reported_as_unlisted_not_invented(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, latest=4)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual([v["version"] for v in paper["versions"]], [1, 4])
        self.assertTrue(paper["versions"][0].get("partial"))

    def test_asking_for_an_older_version_reads_that_version_and_says_it_is_not_the_latest(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, versions_page=VERSIONS_PAGE)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001v1"})
            html_calls = [h for h in web.hits if h.startswith("/html/")]
        self.assertEqual(paper["ref"]["version"], 1)
        self.assertFalse(paper["meta"]["is_latest"])
        self.assertEqual(paper["meta"]["latest_version"], 2)
        self.assertIn("NOT_LATEST_VERSION", paper["warnings"])
        self.assertEqual(html_calls, ["/html/2101.00001v1"])                                       # the pinned version's text, not the latest's
        self.assertTrue(paper["url"].endswith("v1"))

    def test_the_pdf_is_read_when_there_is_no_html_version(self):
        with FakeWeb() as web:
            routes_for_paper(web, pdf=self.pdf)                                                      # /html/... is a 404 on the fake server
            paper = A.load(fetcher_for(web), Settings(), {"url": "https://arxiv.org/abs/2101.00001"})
        self.assertEqual((paper["retrieved_via"], paper["full_text"]), ("arxiv-pdf", True))
        canon = [s.get("canonical") for s in paper["structure"]["sections"]]
        for name in ("abstract", "introduction", "related work", "methodology", "experiments", "results", "discussion", "conclusion"):
            self.assertIn(name, canon)
        self.assertIn("EQUATIONS_APPROXIMATE", paper["warnings"])                                  # a formula read from a PDF is never presented as exact
        self.assertIn("HTML_NOT_FOUND", paper["warnings"])
        self.assertEqual(len(paper["structure"]["references"]), 2)

    def test_a_stub_html_page_falls_through_to_the_pdf(self):
        stub = "<html><body><article><h1>Conversion unavailable</h1><p>HTML is not available for this paper.</p></article></body></html>"
        with FakeWeb() as web:
            routes_for_paper(web, html=stub, pdf=self.pdf)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual(paper["retrieved_via"], "arxiv-pdf")
        self.assertIn("HTML_HTML_NO_TEXT", paper["warnings"])

    def test_a_scanned_pdf_falls_back_to_the_abstract_and_says_so(self):
        with FakeWeb() as web:
            routes_for_paper(web, pdf=(FIXTURES / "scanned_paper.pdf").read_bytes())
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual((paper["retrieved_via"], paper["full_text"]), ("arxiv-abstract", False))   # an abstract is never presented as the paper
        self.assertIn("FULL_TEXT_UNAVAILABLE", paper["warnings"])
        self.assertIn("PDF_PDF_NO_TEXT", paper["warnings"])
        self.assertIn("ABSTRACT_ONLY", paper["structure"]["warnings"])
        self.assertEqual([s["heading"] for s in paper["structure"]["sections"] if s["heading"]], ["Abstract"])

    def test_a_pdf_without_headings_is_kept_as_full_text_with_a_warning(self):
        with FakeWeb() as web:
            routes_for_paper(web, pdf=(FIXTURES / "no_headings.pdf").read_bytes())
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual(paper["retrieved_via"], "arxiv-pdf")
        self.assertIn("NO_SECTION_HEADINGS", paper["warnings"])
        self.assertIn("Full text", [s["heading"] for s in paper["structure"]["sections"]])

    def test_a_download_that_is_not_a_pdf_is_not_parsed(self):
        with FakeWeb() as web:
            routes_for_paper(web)
            web.routes["/pdf/2101.00001v2"] = (200, {"Content-Type": "application/pdf"}, b"<html>captcha</html>")
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        self.assertEqual(paper["retrieved_via"], "arxiv-abstract")
        self.assertIn("PDF_PDF_UNREADABLE", paper["warnings"])

    def test_robots_txt_is_honoured_for_the_paper_files_but_not_for_the_api(self):
        with FakeWeb() as web:
            routes_for_paper(web, html=self.html, pdf=self.pdf, robots="User-agent: *\nDisallow: /html/\nDisallow: /pdf/\n")
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
            fetched = [h for h in web.hits if h.startswith(("/html/", "/pdf/"))]
        self.assertEqual(paper["retrieved_via"], "arxiv-abstract")
        self.assertEqual(fetched, [])                                                               # never fetched what robots.txt forbids
        self.assertIn("HTML_ROBOTS_DISALLOWED", paper["warnings"])
        self.assertIn("PDF_ROBOTS_DISALLOWED", paper["warnings"])

    def test_the_abstract_in_the_result_comes_from_the_api_and_is_labelled(self):
        with FakeWeb() as web:
            routes_for_paper(web, pdf=(FIXTURES / "scanned_paper.pdf").read_bytes())
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
        abstract = [s for s in paper["structure"]["sections"] if s["heading"] == "Abstract"][0]
        self.assertEqual(abstract["text"], "We study attention. It helps.")

    def test_a_missing_paper_and_a_bad_identifier(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(total=0))
            with self.assertRaises(FetchError) as cm:
                A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.99999"})
            self.assertEqual(cm.exception.code, "NOT_FOUND")
            for bad in ("", "not an id", "../../etc/passwd", "https://evil.example/abs/2101.00001", "http://169.254.169.254/abs/2101.00001"):
                with self.assertRaises(FetchError, msg=bad) as cm:
                    A.load(fetcher_for(web), Settings(), {"arxiv_id": bad})
                self.assertEqual(cm.exception.code, "PROVIDER_ERROR", bad)

    def test_a_refusal_while_reading_the_paper_stops_further_arxiv_requests(self):
        with FakeWeb() as web:
            routes_for_paper(web)
            web.routes["/html/2101.00001v2"] = (403, {"Content-Type": "text/plain"}, b"no")
            before = len(web.hits)
            paper = A.load(fetcher_for(web), Settings(), {"arxiv_id": "2101.00001"})
            pdf_calls = [h for h in web.hits[before:] if h.startswith("/pdf/")]
        self.assertEqual(pdf_calls, [])                                                             # after a refusal the PDF is not tried
        self.assertEqual(paper["retrieved_via"], "arxiv-abstract")


class Diagnose(Gated):
    def test_ok_and_failures_are_explained_in_plain_words(self):
        with FakeWeb() as web:
            web.routes["/api/query"] = lambda h: atom(feed(entry(), total=2_000_000))
            ok = A.diagnose(fetcher_for(web), Settings())
        self.assertEqual((ok["status"], ok["ok"], ok["total_results"]), ("ok", True, 2_000_000))
        with FakeWeb() as web:
            web.routes["/api/query"] = (500, {"Content-Type": "text/plain"}, b"x")
            bad = A.diagnose(fetcher_for(web), Settings())
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["message"] and bad["fix"] is not None)


class Facade(Gated):
    def test_webaccess_loads_a_paper_and_caches_only_the_extracted_text(self):
        from app.modules.storage import LocalStorage
        from app.modules.webaccess.cache import WebCache
        from support import Harness

        h = Harness()
        self.addCleanup(h.close)
        alice, _ = h.make_user("alice@example.com")
        bob, _ = h.make_user("bob@example.com")
        ws = h.workspace()
        pa, pb = ws.projects.create(alice, "A")["id"], ws.projects.create(bob, "B")["id"]
        cache = WebCache(h.conn, LocalStorage(h.settings.storage_dir), h.clock, ttl_hours=24, max_bytes=10_000_000)
        with FakeWeb() as web:
            routes_for_paper(web, html=Load.html)
            access = WebAccess(fetcher_for(web), Settings(), h.clock, cache, alice.user_id, pa)
            first = access.load("arxiv", {"arxiv_id": "2101.00001"})
            calls = len(web.hits)
            second = access.load("arxiv", {"arxiv_id": "2101.00001"})
            self.assertEqual(len(web.hits), calls)                                                  # the second read is served from the cache, with no request
            other = WebAccess(fetcher_for(web), Settings(), h.clock, cache, bob.user_id, pb).load("arxiv", {"arxiv_id": "2101.00001"})
            self.assertGreater(len(web.hits), calls)                                                # another person's project never sees this person's cache
        self.assertEqual((first["cache_hit"], second["cache_hit"], other["cache_hit"]), (False, True, False))
        self.assertEqual(second["retrieved_via"], "arxiv-html")
        self.assertNotIn("%PDF", json.dumps(second))
        with self.assertRaises(FetchError) as cm:
            access.load("searxng", {"url": "https://example.org"})
        self.assertEqual(cm.exception.code, "PROVIDER_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
