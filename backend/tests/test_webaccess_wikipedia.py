"""Wikipedia through the MediaWiki Action API: search, disambiguation, redirects, sections, tables, equations, attribution and every failure mode.
A local FakeWeb answers in the documented response shapes (formatversion=2); the real Wikipedia is NOT contacted, so these tests show that the adapter builds the right
requests and reads the documented answers, not that the live service behaves identically."""
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.modules.webaccess.fetcher import FetchError, SafeFetcher
from app.modules.webaccess.providers import PROVIDERS
from app.modules.webaccess.structure import compose, parse_article, section_at, select_sections

from web_support import FakeWeb, RoutedTransport

FIXTURES = Path(__file__).parent / "fixtures"
PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731
W = PROVIDERS["wikipedia"]


class Settings:
    searxng_url = ""
    research_contact = ""
    research_fetch_timeout_s = 5
    research_allow_private_network = False


def fetcher_for(web, **kw):
    return SafeFetcher(user_agent="PredictAI-Test/1 (test@example.org)", resolver=PUBLIC, transport=RoutedTransport(web), sleeper=lambda s: None, host_interval_s=0, **kw)


def qs(handler):
    return {k: v[0] for k, v in parse_qs(urlsplit(handler.path).query).items()}


def reply(body, status=200, headers=None):
    return status, {"Content-Type": "application/json; charset=utf-8", **(headers or {})}, json.dumps(body).encode()


def api(routes):
    """A /w/api.php route that dispatches on action/list/prop to `routes` callables(params)->body."""
    def handler(h):
        p = qs(h)
        key = ("search" if p.get("list") == "search" else p.get("prop", "").split("|")[0] or p.get("meta", "")) if p.get("action") == "query" else p.get("action")
        fn = routes.get(key) or routes.get(p.get("action"))
        if fn is None:
            return reply({"error": {"code": "badvalue", "info": "x"}})
        out = fn(p)
        return out if isinstance(out, tuple) else reply(out)
    return handler


SEARCH = {"batchcomplete": True, "query": {"searchinfo": {"totalhits": 3, "suggestion": "business class"}, "search": [
    {"ns": 0, "title": "Business class", "pageid": 101, "size": 9000, "wordcount": 1500, "snippet": "<span class=\"searchmatch\">Business</span> class is a travel class", "timestamp": "2026-01-02T03:04:05Z"},
    {"ns": 0, "title": "Business (disambiguation)", "pageid": 102, "wordcount": 80, "snippet": "Business may refer to", "timestamp": "2025-01-01T00:00:00Z"},
    {"ns": 0, "title": "Airline seat", "pageid": 103, "wordcount": 900, "snippet": "An airline seat", "timestamp": "2025-05-05T00:00:00Z"}]}}
DETAILS = {"batchcomplete": True, "query": {"pages": [
    {"pageid": 101, "ns": 0, "title": "Business class", "extract": "Business class is a travel class on airlines.", "fullurl": "https://en.wikipedia.org/wiki/Business_class",
     "canonicalurl": "https://en.wikipedia.org/wiki/Business_class", "pageprops": {"wikibase-shortdesc": "Class of airline service"},
     "categories": [{"ns": 14, "title": "Category:Airline cabin classes"}], "revisions": [{"revid": 555, "parentid": 554, "timestamp": "2026-03-04T05:06:07Z"}]},
    {"pageid": 102, "ns": 0, "title": "Business (disambiguation)", "extract": "Business may refer to:", "pageprops": {"disambiguation": ""}},
    {"pageid": 103, "ns": 0, "title": "Airline seat", "extract": "", "fullurl": "https://en.wikipedia.org/wiki/Airline_seat", "revisions": [{"revid": 600, "timestamp": "2025-06-06T00:00:00Z"}]}]}}


class Search(unittest.TestCase):
    def test_hits_carry_lead_text_description_categories_revision_and_the_tertiary_label(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": lambda p: SEARCH, "extracts": lambda p: DETAILS, "links": lambda p: {"query": {"pages": []}}})
            hits = W.search(fetcher_for(web), Settings(), "business class", 5)
            calls = [urlsplit(h).query for h in web.hits]
        self.assertEqual([h.title for h in hits], ["Business class", "Airline seat"])             # the disambiguation page is never an answer
        first = hits[0]
        self.assertEqual(first.url, "https://en.wikipedia.org/wiki/Business_class")
        self.assertEqual(first.text, "Business class is a travel class on airlines.")
        self.assertEqual(first.modified, "2026-03-04T05:06:07Z")                                   # the last REVISION time, not a search-index time
        self.assertEqual((first.extra["description"], first.extra["categories"], first.extra["revid"], first.extra["tier"]), ("Class of airline service", ["Airline cabin classes"], 555, "tertiary"))
        self.assertEqual((first.source_type, first.license, first.publisher), ("encyclopedia", "CC BY-SA 4.0", "Wikipedia"))
        self.assertIsNone(hits[1].text)                                                           # an empty extract is never replaced by invented text
        self.assertNotIn("<span", first.snippet)
        self.assertIn("srnamespace=0", calls[0])                                                  # articles only: no talk, user or template pages
        self.assertIn("srinfo=suggestion%7Crewrittenquery", calls[0])
        self.assertEqual([h.rank for h in hits], [1, 2])

    def test_a_disambiguation_page_is_followed_to_the_best_matching_articles(self):
        search = {"query": {"search": [{"title": "Mercury", "pageid": 7, "snippet": "x", "timestamp": "2025-01-01T00:00:00Z"}]}}
        det_search = {"query": {"pages": [{"pageid": 7, "title": "Mercury", "extract": "Mercury may refer to:", "pageprops": {"disambiguation": ""}}]}}
        links = {"query": {"pages": [{"pageid": 7, "title": "Mercury", "links": [{"ns": 0, "title": "Mercury (planet)"}, {"ns": 0, "title": "Mercury (element)"}, {"ns": 0, "title": "Freddie"}]}]}}
        targets = {"query": {"pages": [{"pageid": 8, "title": "Mercury (planet)", "extract": "Mercury is the planet closest to the Sun.", "canonicalurl": "https://en.wikipedia.org/wiki/Mercury_(planet)"}]}}

        def extracts(p):
            return targets if "titles" in p else det_search
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": lambda p: search, "extracts": extracts, "links": lambda p: links})
            hits = W.search(fetcher_for(web), Settings(), "mercury planet", 3)
        self.assertEqual([h.title for h in hits], ["Mercury (planet)"])
        self.assertEqual(hits[0].extra["resolved_from_disambiguation"], "Mercury")

    def test_a_spelling_suggestion_is_used_only_when_nothing_matched_and_is_reported(self):
        calls = []

        def search(p):
            calls.append(p["srsearch"])
            if p["srsearch"] == "buisness class":
                return {"query": {"searchinfo": {"suggestion": "business class"}, "search": []}}
            return SEARCH
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": search, "extracts": lambda p: DETAILS})
            hits = W.search(fetcher_for(web), Settings(), "buisness class", 3)
        self.assertEqual(calls, ["buisness class", "business class"])
        self.assertEqual(hits[0].extra["corrected_from"], "buisness class")

    def test_no_results_is_an_empty_list_not_an_error(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": lambda p: {"query": {"search": []}}})
            self.assertEqual(W.search(fetcher_for(web), Settings(), "zzzzqqqq", 3), [])

    def test_duplicate_titles_and_pages_without_ids_are_removed(self):
        search = {"query": {"search": [{"title": "A", "pageid": 1, "snippet": "x"}, {"title": "a", "pageid": 2, "snippet": "x"}, {"title": "no id"}, {"title": "A", "pageid": 1}]}}
        det = {"query": {"pages": [{"pageid": 1, "title": "A", "extract": "Alpha."}, {"pageid": 2, "title": "a", "extract": "Alpha again."}]}}
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": lambda p: search, "extracts": lambda p: det})
            hits = W.search(fetcher_for(web), Settings(), "a", 5)
        self.assertEqual(len(hits), 1)

    def test_detail_failure_still_returns_snippet_hits(self):
        with FakeWeb() as web:
            def extracts(p):
                return 500, {"Content-Type": "text/plain"}, b"boom"
            web.routes["/w/api.php"] = api({"search": lambda p: SEARCH, "extracts": extracts})
            hits = W.search(fetcher_for(web), Settings(), "business class", 5)
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(h.text is None for h in hits))                                        # nothing invented

    def test_only_the_query_is_sent_and_the_client_identifies_itself(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"search": lambda p: {"query": {"search": []}}})
            W.search(fetcher_for(web), Settings(), "public topic", 3)
            seen = web.headers_seen[0]
        self.assertTrue(seen["user-agent"].startswith("PredictAI-Test"))
        self.assertNotIn("cookie", seen)
        self.assertNotIn("authorization", seen)


class Failures(unittest.TestCase):
    def search_error(self, route):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = route
            with self.assertRaises(FetchError) as cm:
                W.search(fetcher_for(web), Settings(), "x", 3)
            return cm.exception

    def test_api_errors_are_named(self):
        self.assertEqual(self.search_error(api({"search": lambda p: {"error": {"code": "badvalue", "info": "secret internals"}}})).code, "PROVIDER_ERROR")
        busy = self.search_error(api({"search": lambda p: {"error": {"code": "maxlag", "info": "Waiting for host: 7 seconds lagged"}}}))
        self.assertEqual((busy.code, busy.retryable), ("RATE_LIMITED", True))
        self.assertNotIn("secret", self.search_error(api({"search": lambda p: {"error": {"code": "badvalue", "info": "secret internals"}}})).message)

    def test_malformed_answers_are_reported_not_hidden(self):
        self.assertEqual(self.search_error(lambda h: (200, {"Content-Type": "application/json"}, b"{not json")).code, "PROVIDER_BAD_ANSWER")
        self.assertEqual(self.search_error(lambda h: (200, {"Content-Type": "application/json"}, b"[1,2]")).code, "PROVIDER_BAD_ANSWER")
        self.assertEqual(self.search_error(lambda h: (200, {"Content-Type": "text/html"}, b"<html>maintenance</html>")).code, "PROVIDER_BAD_ANSWER")

    def test_refusal_names_the_contact_setting_and_rate_limits_are_retried_then_reported(self):
        e = self.search_error(lambda h: (403, {"Content-Type": "text/plain"}, b"Please set a user-agent"))
        self.assertEqual(e.code, "PROVIDER_REFUSED")
        self.assertIn("PREDICTAI_RESEARCH_CONTACT", e.message)
        e = self.search_error(lambda h: (429, {"Content-Type": "text/plain", "Retry-After": "2"}, b"slow down"))
        self.assertEqual((e.code, e.retryable), ("RATE_LIMITED", True))
        with FakeWeb() as web:
            n = {"c": 0}

            def flaky(h):
                n["c"] += 1
                return (429, {"Retry-After": "1"}, b"x") if n["c"] == 1 else reply({"query": {"search": []}})
            web.routes["/w/api.php"] = flaky
            self.assertEqual(W.search(fetcher_for(web), Settings(), "x", 3), [])                  # one 429 is retried (Retry-After honoured), then succeeds
            self.assertEqual(n["c"], 2)

    def test_a_timeout_is_reported_as_such(self):
        class Slow:
            def request(self, *a, **k):
                raise TimeoutError()
        f = SafeFetcher(user_agent="t", resolver=PUBLIC, transport=Slow(), sleeper=lambda s: None, host_interval_s=0)
        with self.assertRaises(FetchError) as cm:
            W.search(f, Settings(), "x", 3)
        self.assertEqual(cm.exception.code, "TIMEOUT")


PARSE = {"parse": {"title": "Business class", "pageid": 101, "revid": 555, "displaytitle": "Business class", "text": (FIXTURES / "wikipedia_business_class.html").read_text(),
                   "categories": [{"sortkey": "", "category": "Airline_cabin_classes", "hidden": False}, {"sortkey": "", "category": "All_articles_with_unsourced_statements", "hidden": True}],
                   "properties": {"wikibase-shortdesc": "Class of airline service"}}}
REVS = {"query": {"pages": [{"pageid": 101, "title": "Business class", "revisions": [{"revid": 555, "timestamp": "2026-03-04T05:06:07Z"}]}]}}


def article_routes(parse=PARSE):
    return api({"parse": lambda p: parse, "revisions": lambda p: REVS})


class Article(unittest.TestCase):
    def load(self, ref=None, parse=PARSE):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = article_routes(parse)
            doc = W.load(fetcher_for(web), Settings(), ref or {"title": "Business class"})
            return doc, [urlsplit(h).query for h in web.hits]

    def test_whole_article_keeps_headings_tables_lists_equations_and_references(self):
        doc, calls = self.load()
        sections = {s["heading"]: s for s in doc["structure"]["sections"]}
        self.assertIn("History", sections)
        self.assertEqual(sections["Early years"]["path"], ["History", "Early years"])               # the heading hierarchy is preserved
        self.assertIn("Seat comparison", sections)
        rows = sections["Seat comparison"]["tables"][0]
        self.assertEqual(rows["rows"][1], ["Alpha Air", "60", "20"])
        self.assertIn("Airline: Alpha Air; Pitch (in): 60; Width (in): 20", sections["Seat comparison"]["text"])    # a table row is one citable sentence
        self.assertEqual(sections["Early years"]["equations"][0]["tex"], "x^{2}+y")
        self.assertIn("Lie-flat seats.", sections["Early years"]["text"])
        self.assertEqual(len(doc["structure"]["references"]), 2)
        self.assertNotIn("Navbox junk", json.dumps(doc["structure"]))
        self.assertNotIn("[1]", sections[None]["text"])                                           # citation markers are not text
        self.assertEqual(doc["categories"], ["Airline cabin classes"])                            # hidden maintenance categories are dropped
        self.assertIn("redirects=1", calls[0])
        self.assertIn("action=parse", calls[0])

    def test_attribution_permalink_licence_and_tertiary_label(self):
        doc, _ = self.load()
        self.assertEqual(doc["tier"], "tertiary")
        self.assertEqual(doc["modified"], "2026-03-04T05:06:07Z")
        self.assertEqual(doc["permalink"], "https://en.wikipedia.org/w/index.php?title=Business_class&oldid=555")
        self.assertIn("CC BY-SA 4.0", doc["attribution"])
        self.assertIn("oldid=555", doc["attribution"])
        self.assertEqual(doc["url"], "https://en.wikipedia.org/wiki/Business_class")

    def test_a_redirect_is_reported_with_the_name_asked_for(self):
        doc, _ = self.load({"title": "Club class"})
        self.assertEqual((doc["title"], doc["redirected_from"]), ("Business class", "Club class"))

    def test_a_disambiguation_article_is_flagged(self):
        page = {"parse": {**PARSE["parse"], "title": "Business (disambiguation)", "properties": {"disambiguation": ""}}}
        doc, _ = self.load(parse=page)
        self.assertTrue(doc["disambiguation"])
        self.assertIn("DISAMBIGUATION_PAGE", doc["warnings"])

    def test_legacy_answer_shapes_are_accepted(self):
        page = {"parse": {**PARSE["parse"], "text": {"*": PARSE["parse"]["text"]}, "properties": [{"name": "wikibase-shortdesc", "*": "x"}]}}
        doc, _ = self.load(parse=page)
        self.assertTrue(doc["structure"]["sections"])

    def test_missing_page_and_malformed_parse_are_errors(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"parse": lambda p: {"error": {"code": "missingtitle", "info": "x"}}})
            with self.assertRaises(FetchError) as cm:
                W.load(fetcher_for(web), Settings(), {"title": "Nope"})
            self.assertEqual(cm.exception.code, "NOT_FOUND")
        for bad in ({"parse": "oops"}, {"parse": {"title": "T", "text": ""}}):
            with FakeWeb() as web:
                web.routes["/w/api.php"] = api({"parse": lambda p, b=bad: b})
                with self.assertRaises(FetchError) as cm:
                    W.load(fetcher_for(web), Settings(), {"title": "T"})
                self.assertEqual(cm.exception.code, "PROVIDER_BAD_ANSWER")

    def test_a_failing_revision_lookup_does_not_lose_the_article(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"parse": lambda p: PARSE, "revisions": lambda p: (500, {}, b"x")})
            doc = W.load(fetcher_for(web), Settings(), {"title": "Business class"})
        self.assertIsNone(doc["modified"])
        self.assertTrue(doc["structure"]["sections"])

    def test_long_articles_are_read_by_the_sections_that_match_the_question(self):
        doc, _ = self.load()
        art = doc["structure"]
        chosen = select_sections(art, "seat width pitch comparison", max_chars=900)
        text, offsets = compose(art, chosen)
        headings = [o["heading"] for o in offsets]
        self.assertIn("Seat comparison", headings)
        self.assertNotIn("History", headings)                                                      # not asked about, so not read
        pos = text.index("Beta Jet")
        self.assertEqual(section_at(offsets, pos)["anchor"], "Seat_comparison")


class SelfTest(unittest.TestCase):
    def test_diagnose_ok_refused_and_unreachable(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = api({"siteinfo": lambda p: {"query": {"general": {"sitename": "Wikipedia"}}}})
            d = W.diagnose(fetcher_for(web), Settings())
        self.assertEqual((d["ok"], d["status"]), (True, "ok"))
        self.assertIn("PREDICTAI_RESEARCH_CONTACT", d["fix"])                                      # advice, not a failure
        with FakeWeb() as web:
            web.routes["/w/api.php"] = lambda h: (403, {}, b"no")
            d = W.diagnose(fetcher_for(web), Settings())
        self.assertEqual((d["ok"], d["status"]), (False, "refused"))
        self.assertIn("PREDICTAI_RESEARCH_CONTACT", d["fix"])
        class Down:
            def request(self, *a, **k):
                raise ConnectionRefusedError()
        d = W.diagnose(SafeFetcher(user_agent="t", resolver=PUBLIC, transport=Down(), sleeper=lambda s: None, host_interval_s=0), Settings())
        self.assertEqual((d["ok"], d["status"]), (False, "unreachable"))


if __name__ == "__main__":
    unittest.main()
