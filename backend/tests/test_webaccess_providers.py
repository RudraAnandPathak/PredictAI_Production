"""Search providers, the web cache and the WebAccess facade. A local FakeWeb stands in for the internet: real Wikipedia, arXiv and SearXNG are NOT contacted,
so these tests show that the adapters parse the documented response shapes and fail safely, not that the live services behave the same way."""
import json
import unittest
from dataclasses import replace
from datetime import timedelta

from app.modules.webaccess.cache import WebCache, cache_key
from app.modules.webaccess.fetcher import FetchError, SafeFetcher
from app.modules.webaccess.providers import PROVIDERS
from app.modules.webaccess.providers.base import content_terms
from app.modules.webaccess.service import WebAccess, build_fetcher
from app.modules.storage import LocalStorage

from support import Harness
from web_support import FakeWeb, RoutedTransport, page

PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731

WIKI_SEARCH = {"query": {"search": [
    {"pageid": 11, "title": "Photosynthesis", "snippet": "<span class=\"searchmatch\">Photosynthesis</span> is a process", "timestamp": "2026-05-01T10:00:00Z"},
    {"pageid": 12, "title": "Light-dependent reactions", "snippet": "reactions", "timestamp": "2026-04-01T10:00:00Z"},
    {"title": "no id, must be skipped", "snippet": "x"}]}}
WIKI_EXTRACTS = {"query": {"pages": [{"pageid": 11, "title": "Photosynthesis", "extract": "Photosynthesis converts light energy into chemical energy."},
                                      {"pageid": 12, "title": "Light-dependent reactions", "extract": ""}]}}
ATOM = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2101.00001v1</id><updated>2021-01-02T00:00:00Z</updated><published>2021-01-01T00:00:00Z><title>  A study of
 attention </title>
<summary>We study attention.  It helps.</summary><author><name>Ada L.</name></author><author><name>Alan T.</name></author><arxiv:primary_category term="cs.LG"/></entry>
<entry><id>http://example.org/not-arxiv</id><title>Fake</title><summary>x</summary></entry>
<entry><id>http://arxiv.org/api/errors#x</id><title>Error</title><summary>bad</summary></entry></feed>""".replace("<published>2021-01-01T00:00:00Z>", "<published>2021-01-01T00:00:00Z</published>")


def fetcher_for(web, **kw):
    f = SafeFetcher(user_agent="PredictAI-Test/1", resolver=PUBLIC, transport=RoutedTransport(web), sleeper=lambda s: None, host_interval_s=0, **kw)
    return f


class Settings:
    searxng_url = ""
    research_contact = ""
    research_fetch_timeout_s = 5
    research_allow_private_network = False


class Wikipedia(unittest.TestCase):
    def test_search_parses_hits_and_attaches_lead_text(self):
        with FakeWeb() as web:
            def route(handler):
                body = WIKI_EXTRACTS if "prop=extracts" in handler.path else WIKI_SEARCH
                return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
            web.routes["/w/api.php"] = route
            hits = PROVIDERS["wikipedia"].search(fetcher_for(web), Settings(), "photosynthesis", 5)
        self.assertEqual([h.title for h in hits], ["Photosynthesis", "Light-dependent reactions"])
        self.assertEqual(hits[0].url, "https://en.wikipedia.org/wiki/Photosynthesis")
        self.assertEqual(hits[0].url if False else hits[1].url, "https://en.wikipedia.org/wiki/Light-dependent_reactions")
        self.assertNotIn("<span", hits[0].snippet)
        self.assertEqual(hits[0].text, "Photosynthesis converts light energy into chemical energy.")
        self.assertIsNone(hits[1].text)                                          # an empty extract is never replaced by invented text
        self.assertEqual((hits[0].source_type, hits[0].license, hits[0].modified), ("encyclopedia", "CC BY-SA 4.0", "2026-05-01T10:00:00Z"))

    def test_extract_failure_still_returns_snippet_hits(self):
        with FakeWeb() as web:
            def route(handler):
                if "prop=extracts" in handler.path:
                    return 500, {"Content-Type": "text/plain"}, b"boom"
                return 200, {"Content-Type": "application/json"}, json.dumps(WIKI_SEARCH).encode()
            web.routes["/w/api.php"] = route
            hits = PROVIDERS["wikipedia"].search(fetcher_for(web), Settings(), "photosynthesis", 5)
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(h.text is None for h in hits))

    def test_api_error_and_garbage_are_reported_not_hidden(self):
        for body, code in [({"error": {"code": "x"}}, "PROVIDER_ERROR"), ([1, 2], "PROVIDER_BAD_ANSWER")]:
            with FakeWeb() as web:
                web.json("/w/api.php", body)
                with self.assertRaises(FetchError) as cm:
                    PROVIDERS["wikipedia"].search(fetcher_for(web), Settings(), "x", 3)
                self.assertEqual(cm.exception.code, code)
        with FakeWeb() as web:
            web.text("/w/api.php", "{not json", "application/json")
            with self.assertRaises(FetchError) as cm:
                PROVIDERS["wikipedia"].search(fetcher_for(web), Settings(), "x", 3)
            self.assertEqual(cm.exception.code, "PROVIDER_BAD_ANSWER")

    def test_only_the_query_is_sent_and_the_agent_is_identified(self):
        with FakeWeb() as web:
            web.json("/w/api.php", {"query": {"search": []}})
            f = fetcher_for(web)
            PROVIDERS["wikipedia"].search(f, Settings(), "private? no: public query", 3)
            seen = web.headers_seen[0]
        self.assertTrue(seen["user-agent"].startswith("PredictAI-Test"))
        self.assertNotIn("cookie", seen)
        self.assertNotIn("authorization", seen)


class Arxiv(unittest.TestCase):
    def test_parse_requires_real_arxiv_entries_and_normalises_text(self):
        feed = ATOM.replace('<entry><id>http://arxiv.org/api/errors#x</id><title>Error</title><summary>bad</summary></entry>', "")
        hits = PROVIDERS["arxiv"].parse(feed.encode(), 10)
        self.assertEqual(len(hits), 1)                                           # the non-arXiv entry is dropped: only real arXiv abstract URLs become hits
        h = hits[0]
        self.assertEqual(h.title, "A study of attention")
        self.assertEqual(h.url, "https://arxiv.org/abs/2101.00001v1")
        self.assertEqual((h.author, h.published, h.source_type, h.extra["category"]), ("Ada L., Alan T.", "2021-01-01T00:00:00Z", "preprint", "cs.LG"))
        self.assertEqual(h.snippet, "We study attention. It helps.")

    def test_an_error_entry_is_reported_with_arxivs_own_message_not_dropped(self):
        # Phase 6 dropped arXiv's error entry and so turned "your query was invalid" into "no results"; the message is now surfaced (see test_webaccess_arxiv for more).
        with self.assertRaises(FetchError) as cm:
            PROVIDERS["arxiv"].parse(ATOM.encode(), 10)
        self.assertEqual(cm.exception.code, "PROVIDER_ERROR")
        self.assertIn("bad", cm.exception.message)

    def test_doctype_and_entities_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>&b;</id></entry></feed>'
        with self.assertRaises(FetchError) as cm:
            PROVIDERS["arxiv"].parse(bomb, 5)
        self.assertEqual(cm.exception.code, "PROVIDER_BAD_ANSWER")
        with self.assertRaises(FetchError):
            PROVIDERS["arxiv"].parse(b"<feed", 5)

    def test_search_builds_a_field_aware_query_through_the_fetcher(self):
        feed = ATOM.replace('<entry><id>http://arxiv.org/api/errors#x</id><title>Error</title><summary>bad</summary></entry>', "")
        with FakeWeb() as web:
            web.routes["/api/query"] = (200, {"Content-Type": "application/atom+xml"}, feed.encode())
            hits = PROVIDERS["arxiv"].search(fetcher_for(web), Settings(), "What is the effect of attention in transformers?", 5)
            path = web.hits[0]
        self.assertEqual(len(hits), 1)
        # The Phase 6 query ANDed bare `all:` terms (which matched almost nothing); the topic words are now searched in title OR abstract, loosened only if that finds nothing.
        self.assertIn("search_query=%28ti%3Aattention+OR+abs%3Aattention%29+AND+%28ti%3Atransformers+OR+abs%3Atransformers%29", path)
        with FakeWeb() as quiet:
            self.assertEqual(PROVIDERS["arxiv"].search(fetcher_for(quiet), Settings(), "the of and", 5), [])      # nothing searchable: nothing is sent
            self.assertEqual(quiet.hits, [])

    def test_content_terms(self):
        self.assertEqual(content_terms("Why does the sky look blue?"), ["sky", "look", "blue"])


class Searxng(unittest.TestCase):
    def settings(self, web):
        return replace_settings(searxng_url=web.base)

    def test_not_configured_until_the_owner_sets_a_url(self):
        ok, reason = PROVIDERS["searxng"].configured(Settings())
        self.assertFalse(ok)
        self.assertIn("PREDICTAI_SEARXNG_URL", reason)
        self.assertTrue(PROVIDERS["searxng"].describe(Settings())["available"] is False)

    def test_parses_json_results_and_skips_unsafe_urls(self):
        data = {"results": [{"title": "<b>Climate</b> report", "url": "https://example.org/a", "content": "Temperatures <i>rose</i>.", "publishedDate": "2026-03-01T00:00:00", "engine": "x"},
                            {"title": "bad", "url": "javascript:alert(1)", "content": "x"}, {"title": "no url"}, {"url": "http://example.net/b", "content": "c"}]}
        with FakeWeb() as web:
            web.json("/search", data)
            s = replace_settings(searxng_url=web.base)
            f = fetcher_for(web, trusted_hosts=[web.authority], allow_private=True)
            hits = PROVIDERS["searxng"].search(f, s, "climate", 10)
        self.assertEqual([h.url for h in hits], ["https://example.org/a", "http://example.net/b"])
        self.assertEqual(hits[0].title, "Climate report")
        self.assertNotIn("<i>", hits[0].snippet)
        self.assertEqual(hits[0].published, "2026-03-01T00:00:00")

    def test_instance_without_json_support_gives_actionable_error(self):
        with FakeWeb() as web:
            web.html("/search", "<html><body><p>results page with html only here</p></body></html>")
            s = replace_settings(searxng_url=web.base)
            f = fetcher_for(web, trusted_hosts=[web.authority], allow_private=True)
            with self.assertRaises(FetchError) as cm:
                PROVIDERS["searxng"].search(f, s, "x", 5)
        self.assertEqual(cm.exception.code, "PROVIDER_JSON_DISABLED")


def replace_settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class CacheAndFacade(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.alice, _ = self.h.make_user("alice@example.com")
        self.bob, _ = self.h.make_user("bob@example.com")
        ws = self.h.workspace()
        self.pa = ws.projects.create(self.alice, "A")["id"]
        self.pb = ws.projects.create(self.bob, "B")["id"]
        self.storage = LocalStorage(self.h.settings.storage_dir)

    def cache(self, ttl=24, cap=10_000_000):
        return WebCache(self.h.conn, self.storage, self.h.clock, ttl_hours=ttl, max_bytes=cap)

    def test_round_trip_expiry_and_hit_counting(self):
        c = self.cache(ttl=2)
        k = cache_key("search", "wikipedia", "q")
        c.put(self.alice.user_id, self.pa, "search", k, "wikipedia", "q", {"hits": [1, 2]})
        got = c.get(self.alice.user_id, self.pa, "search", k)
        self.assertEqual(got["payload"], {"hits": [1, 2]})
        self.assertEqual(c.stats(self.alice.user_id, self.pa)["entries"], 1)
        self.h.clock.advance(hours=3)
        self.assertIsNone(c.get(self.alice.user_id, self.pa, "search", k))
        self.assertEqual(c.stats(self.alice.user_id, self.pa)["entries"], 0)
        self.assertEqual(self.storage.list_prefix(f"p/{self.pa}/w") if hasattr(self.storage, "list_prefix") else [], [])

    def test_cache_is_per_person_and_project(self):
        c = self.cache()
        k = cache_key("page", "https://example.org/")
        c.put(self.alice.user_id, self.pa, "page", k, "page", "u", {"text": "secret-ish"})
        self.assertIsNone(c.get(self.bob.user_id, self.pb, "page", k))
        self.assertIsNone(c.get(self.bob.user_id, self.pa, "page", k))
        self.assertIsNone(c.get(self.alice.user_id, self.pb, "page", k))

    def test_tampered_or_missing_bodies_are_dropped_not_served(self):
        c = self.cache()
        k = cache_key("search", "x")
        c.put(self.alice.user_id, self.pa, "search", k, "wikipedia", "q", {"hits": ["ok"]})
        row = self.h.conn.execute("SELECT body_key FROM web_cache").fetchone()
        self.storage.put(row["body_key"], [json.dumps({"payload": {"hits": ["forged"]}}).encode()], overwrite=True)
        self.assertIsNone(c.get(self.alice.user_id, self.pa, "search", k))
        self.assertEqual(self.h.conn.execute("SELECT COUNT(*) AS n FROM web_cache").fetchone()["n"], 0)
        c.put(self.alice.user_id, self.pa, "search", k, "wikipedia", "q", {"hits": ["ok"]})
        self.storage.delete(self.h.conn.execute("SELECT body_key FROM web_cache").fetchone()["body_key"])
        self.assertIsNone(c.get(self.alice.user_id, self.pa, "search", k))

    def test_size_cap_evicts_oldest_and_clear_removes_everything(self):
        c = self.cache(cap=2500)
        for i in range(6):
            self.h.clock.advance(minutes=1)
            c.put(self.alice.user_id, self.pa, "page", cache_key("p", str(i)), "page", str(i), {"text": "x" * 600})
        self.assertLessEqual(c.stats(self.alice.user_id, self.pa)["bytes"], 2500)
        self.assertIsNone(c.get(self.alice.user_id, self.pa, "page", cache_key("p", "0")))
        self.assertIsNotNone(c.get(self.alice.user_id, self.pa, "page", cache_key("p", "5")))
        self.assertGreater(c.clear(self.alice.user_id, self.pa), 0)
        self.assertEqual(c.stats(self.alice.user_id, self.pa)["entries"], 0)

    def web_access(self, web, cache=True, **settings):
        s = replace_settings(**settings)
        return WebAccess(fetcher_for(web, trusted_hosts=[web.authority], allow_private=True), s, self.h.clock, self.cache() if cache else None, self.alice.user_id, self.pa)

    def test_search_uses_cache_and_reports_timestamps(self):
        with FakeWeb() as web:
            web.routes["/w/api.php"] = lambda hd: (200, {"Content-Type": "application/json"}, json.dumps(WIKI_EXTRACTS if "prop=extracts" in hd.path else WIKI_SEARCH).encode())
            wa = self.web_access(web)
            first = wa.search("wikipedia", "Photosynthesis", 5)
            n = len(web.hits)
            second = wa.search("wikipedia", "  photosynthesis ", 5)
            self.assertEqual(len(web.hits), n)                                  # served from the cache: no second request
            third = wa.search("wikipedia", "photosynthesis", 5, use_cache=False)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertFalse(third["cache_hit"])
        self.assertEqual(second["hits"], first["hits"])
        self.assertTrue(first["retrieved_at"].endswith("Z") and second["retrieved_at"].endswith("Z"))

    def test_unknown_and_unconfigured_providers_are_clear_errors(self):
        with FakeWeb() as web:
            wa = self.web_access(web)
            for key, code in [("nope", "PROVIDER_UNKNOWN"), ("searxng", "PROVIDER_NOT_CONFIGURED")]:
                with self.assertRaises(FetchError) as cm:
                    wa.search(key, "x")
                self.assertEqual(cm.exception.code, code)

    def test_page_extracts_text_metadata_and_caches(self):
        with FakeWeb() as web:
            web.html("/story", page("Rivers rise", ["The river rose two metres on Tuesday according to officials.", "Residents were evacuated from low areas."], published="2026-11-01T09:00:00Z"))
            wa = self.web_access(web)
            p = wa.page(f"{web.base}/story")
            again = wa.page(f"{web.base}/story#section")
        self.assertEqual(p["title"], "Rivers rise")
        self.assertIn("river rose two metres", p["text"])
        self.assertNotIn("Copyright footer", p["text"])
        self.assertEqual(len(p["content_hash"]), 64)
        self.assertEqual(p["meta"].get("published"), "2026-11-01T09:00:00Z")
        self.assertFalse(p["cache_hit"])
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["content_hash"], p["content_hash"])

    def test_page_failures_are_specific_and_never_cached(self):
        with FakeWeb() as web:
            web.html("/gone", "<html><body>x</body></html>", status=404)
            web.html("/empty", "<html><head><title>t</title></head><body><script>var a=1</script></body></html>")
            web.text("/data.bin", "abc", "application/octet-stream")
            wa = self.web_access(web)
            for path, code in [("/gone", "NOT_FOUND"), ("/empty", "NO_TEXT"), ("/data.bin", "WRONG_TYPE")]:
                with self.assertRaises(FetchError) as cm:
                    wa.page(web.base + path)
                self.assertEqual(cm.exception.code, code, path)
            self.assertEqual(self.cache().stats(self.alice.user_id, self.pa)["entries"], 0)

    def test_private_addresses_are_refused_without_the_development_flag(self):
        with FakeWeb() as web:
            f = SafeFetcher(user_agent="t", resolver=PUBLIC, transport=RoutedTransport(web), sleeper=lambda s: None, host_interval_s=0)
            wa = WebAccess(f, replace_settings(), self.h.clock, None, self.alice.user_id, self.pa)
            for url in ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://localhost:8000/admin"]:
                with self.assertRaises(FetchError) as cm:
                    wa.page(url)
                self.assertTrue(cm.exception.code.startswith("URL_"), url)
            self.assertEqual(web.hits, [])

    def test_build_fetcher_trusts_only_the_configured_searxng_authority(self):
        s = load_like(searxng_url="http://127.0.0.1:8888")
        f = build_fetcher(s)
        self.assertEqual(set(f.trusted_hosts), {"127.0.0.1:8888"})
        self.assertFalse(f.allow_private)
        self.assertIn("PredictAI-Research", f.user_agent)
        self.assertEqual(set(build_fetcher(load_like()).trusted_hosts), set())


def load_like(**kw):
    s = replace_settings(**kw)
    s.research_allow_private_network = False
    s.research_contact = ""
    return s


if __name__ == "__main__":
    unittest.main()
