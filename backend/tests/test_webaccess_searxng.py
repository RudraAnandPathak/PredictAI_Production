"""SearXNG: every way the instance can be wrong or silent is named, results are cleaned and de-duplicated, and the shipped docker configuration really enables the JSON format.
A local FakeWeb plays SearXNG; a real SearXNG is NOT contacted (see docs/phase6/CORRECTIVE_REPORT.md for what was and was not verified)."""
import json
import socket
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.modules.webaccess.fetcher import FetchError, SafeFetcher
from app.modules.webaccess.providers import PROVIDERS
from app.modules.webaccess.providers.searxng import SETTINGS_FIX, public_url

from web_support import FakeWeb, RoutedTransport

PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731
S = PROVIDERS["searxng"]
ROOT = Path(__file__).resolve().parents[2]


class Settings:
    searxng_url = ""
    research_contact = ""
    research_fetch_timeout_s = 3
    research_allow_private_network = False


def settings_for(web):
    s = Settings()
    s.searxng_url = web.base
    return s


def fetcher_for(web):
    return SafeFetcher(user_agent="PredictAI-Test/1", resolver=PUBLIC, transport=RoutedTransport(web), sleeper=lambda s: None, host_interval_s=0, trusted_hosts=[web.authority], allow_private=True)


def serve(web, body, status=200, ctype="application/json"):
    web.routes["/search"] = (status, {"Content-Type": ctype}, body if isinstance(body, bytes) else json.dumps(body).encode())


def result(url, title="T", content="c", **extra):
    return {"url": url, "title": title, "content": content, "engine": "duckduckgo", **extra}


class Failures(unittest.TestCase):
    def search(self, web, query="climate"):
        return S.search(fetcher_for(web), settings_for(web), query, 5)

    def test_json_disabled_403_is_named_with_the_exact_fix(self):
        # This is the stock SearXNG behaviour (search.formats lists only html) that Phase 6 reported as "the site does not allow this page to be read automatically".
        with FakeWeb() as web:
            serve(web, b"<html>Forbidden</html>", 403, "text/html")
            with self.assertRaises(FetchError) as cm:
                self.search(web)
        self.assertEqual((cm.exception.code, cm.exception.status), ("PROVIDER_JSON_DISABLED", 403))
        self.assertIn("formats", cm.exception.message)
        self.assertIn("json", cm.exception.message)
        self.assertNotIn("allow this page to be read automatically", cm.exception.message)

    def test_the_bot_limiter_429_is_named_and_not_retried_forever(self):
        with FakeWeb() as web:
            serve(web, b"Too Many Requests", 429, "text/plain")
            with self.assertRaises(FetchError) as cm:
                self.search(web)
            calls = len(web.hits)
        self.assertEqual(cm.exception.code, "PROVIDER_LIMITER")
        self.assertIn("limiter: false", cm.exception.message)
        self.assertLessEqual(calls, 2)

    def test_a_wrong_address_404_says_to_check_the_url(self):
        with FakeWeb() as web:
            with self.assertRaises(FetchError) as cm:
                self.search(web)                                                                   # the fake server has no /search route: 404
        self.assertEqual(cm.exception.code, "PROVIDER_BAD_ANSWER")
        self.assertIn("PREDICTAI_SEARXNG_URL", cm.exception.message)

    def test_an_instance_that_is_not_running_is_said_plainly(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()                                                                               # nothing listens on this port now
        s = Settings()
        s.searxng_url = f"http://127.0.0.1:{port}"
        f = SafeFetcher(user_agent="PredictAI-Test/1", resolver=PUBLIC, sleeper=lambda x: None, host_interval_s=0, trusted_hosts=[f"127.0.0.1:{port}"], allow_private=True, timeout_s=2)
        with self.assertRaises(FetchError) as cm:
            S.search(f, s, "x", 3)
        self.assertEqual(cm.exception.code, "CONNECT_FAILED")
        self.assertIn("Is it running", cm.exception.message)

    def test_html_instead_of_json_and_garbage_are_distinguished(self):
        cases = [(b"<html><body><p>a results page made only of html text</p></body></html>", "text/html", "PROVIDER_JSON_DISABLED"),
                 (b"{not json", "application/json", "PROVIDER_BAD_ANSWER"), (b"[1, 2]", "application/json", "PROVIDER_BAD_ANSWER")]
        for body, ctype, code in cases:
            with FakeWeb() as web:
                serve(web, body, 200, ctype)
                with self.assertRaises(FetchError, msg=body[:12]) as cm:
                    self.search(web)
            self.assertEqual(cm.exception.code, code, body[:12])

    def test_every_engine_failing_is_not_reported_as_no_results(self):
        data = {"results": [], "unresponsive_engines": [["google", "CAPTCHA"], ["duckduckgo", "Suspended: access denied"]]}
        with FakeWeb() as web:
            serve(web, data)
            with self.assertRaises(FetchError) as cm:
                self.search(web)
        self.assertEqual(cm.exception.code, "PROVIDER_ENGINES_FAILED")
        self.assertIn("CAPTCHA", cm.exception.message)
        self.assertIn("duckduckgo", cm.exception.message)

    def test_a_genuinely_empty_answer_is_an_empty_list(self):
        with FakeWeb() as web:
            serve(web, {"results": [], "unresponsive_engines": []})
            self.assertEqual(self.search(web), [])


class Results(unittest.TestCase):
    def search(self, web, data, limit=10, **kw):
        serve(web, data)
        return S.search(fetcher_for(web), settings_for(web), "climate", limit, **kw)

    def test_hits_keep_score_engines_date_and_clean_text(self):
        data = {"results": [result("https://example.org/a?utm_source=x&id=3#top", "<b>Climate</b> report", "Temperatures <i>rose</i>.", publishedDate="2026-03-01T00:00:00",
                                   engines=["duckduckgo", "bing"], score=4.5, category="general")]}
        with FakeWeb() as web:
            hits = self.search(web, data)
        h = hits[0]
        self.assertEqual((h.url, h.title, h.published, h.source_type), ("https://example.org/a?id=3", "Climate report", "2026-03-01T00:00:00", "web"))      # tracking and fragment removed
        self.assertEqual((h.extra["engines"], h.extra["score"], h.extra["category"]), (["duckduckgo", "bing"], 4.5, "general"))
        self.assertNotIn("<i>", h.snippet)

    def test_duplicates_collapse_and_order_is_kept(self):
        data = {"results": [result("https://example.org/a"), result("https://EXAMPLE.org/a/"), result("https://example.org/a#frag"), result("https://example.org/b?utm_campaign=z"),
                            result("https://example.org/b"), result("https://example.net/c")]}
        with FakeWeb() as web:
            hits = self.search(web, data)
        self.assertEqual([h.url for h in hits], ["https://example.org/a", "https://example.org/b", "https://example.net/c"])
        self.assertEqual([h.rank for h in hits], [1, 2, 3])

    def test_unsafe_and_malformed_results_are_dropped(self):
        bad = ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.org/x", "http://127.0.0.1/admin", "http://localhost/x", "http://10.0.0.5/x", "http://169.254.169.254/latest",
               "https://user:pw@example.org/x", "http://2130706433/", "https://example.org:8443/x", "", None, 5, {"x": 1}]
        data = {"results": [result(u) if isinstance(u, str) else {"url": u, "title": "t"} for u in bad] + ["not a dict", None, result("https://example.org/ok")]}
        with FakeWeb() as web:
            hits = self.search(web, data)
        self.assertEqual([h.url for h in hits], ["https://example.org/ok"])

    def test_the_limit_is_respected(self):
        data = {"results": [result(f"https://example.org/{i}") for i in range(20)]}
        with FakeWeb() as web:
            self.assertEqual(len(self.search(web, data, limit=4)), 4)

    def test_partial_engine_failures_are_attached_not_fatal(self):
        data = {"results": [result("https://example.org/a")], "unresponsive_engines": [["google", "timeout"]]}
        with FakeWeb() as web:
            hits = self.search(web, data)
        self.assertEqual(hits[0].extra["unresponsive_engines"], [["google", "timeout"]])

    def test_the_request_is_a_json_query_with_only_the_question_in_it(self):
        with FakeWeb() as web:
            self.search(web, {"results": []}, time_range="month")
            q = parse_qs(urlsplit(web.hits[0]).query)
            seen = web.headers_seen[0]
            self.search(web, {"results": []}, time_range="decade")                                  # not a SearXNG range: ignored, never sent
            q2 = parse_qs(urlsplit(web.hits[1]).query)
        self.assertEqual((q["q"], q["format"], q["categories"], q["time_range"], q["safesearch"]), (["climate"], ["json"], ["general"], ["month"], ["1"]))
        self.assertNotIn("time_range", q2)
        self.assertNotIn("cookie", seen)
        self.assertNotIn("authorization", seen)

    def test_public_url_helper(self):
        self.assertEqual(public_url("HTTPS://Example.org/Path/?b=1&utm_x=2&a=3#f"), "https://example.org/Path?b=1&a=3")
        self.assertEqual(public_url("https://example.org"), "https://example.org/")
        self.assertIsNone(public_url("http://[::1]/x"))
        self.assertIsNone(public_url(None))


class Diagnose(unittest.TestCase):
    def run_case(self, body, status=200, ctype="application/json"):
        with FakeWeb() as web:
            serve(web, body, status, ctype)
            return S.diagnose(fetcher_for(web), settings_for(web))

    def test_ok(self):
        d = self.run_case({"results": [result("https://en.wikipedia.org/wiki/Wikipedia")]})
        self.assertEqual((d["ok"], d["status"], d["results"]), (True, "ok", 1))

    def test_each_failure_has_a_status_and_a_fix(self):
        cases = [(b"x", 403, "text/html", "json_disabled", "formats"), (b"x", 429, "text/plain", "limited", "limiter"),
                 ({"results": [], "unresponsive_engines": [["google", "CAPTCHA"]]}, 200, "application/json", "engines_failed", "engines"),
                 ({"results": []}, 200, "application/json", "bad_answer", "engine")]
        for body, status, ctype, expect, word in cases:
            d = self.run_case(body, status, ctype)
            self.assertEqual((d["ok"], d["status"]), (False, expect), expect)
            self.assertIn(word, (d["message"] + " " + d["fix"]).lower(), expect)

    def test_unreachable(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        s = Settings()
        s.searxng_url = f"http://127.0.0.1:{port}"
        f = SafeFetcher(user_agent="t", resolver=PUBLIC, sleeper=lambda x: None, host_interval_s=0, trusted_hosts=[f"127.0.0.1:{port}"], allow_private=True, timeout_s=2)
        d = S.diagnose(f, s)
        self.assertEqual((d["ok"], d["status"]), (False, "unreachable"))
        self.assertIn("docker compose up", d["fix"])

    def test_not_configured_says_what_to_do(self):
        ok, reason = S.configured(Settings())
        self.assertFalse(ok)
        self.assertIn("docker compose up", reason)


class ShippedConfiguration(unittest.TestCase):
    """The repository ships a SearXNG configuration that already enables JSON; these tests keep it from drifting."""

    def text(self, name):
        path = ROOT / "docker" / "searxng" / name
        self.assertTrue(path.exists(), f"{path} is missing")
        return path.read_text()

    def test_settings_enable_json_and_a_private_instance(self):
        t = self.text("settings.yml")
        self.assertRegex(t, r"(?m)^\s*formats:\s*\n\s*-\s*html\s*\n\s*-\s*json")
        self.assertRegex(t, r"(?m)^\s*limiter:\s*false")
        self.assertRegex(t, r"(?m)^\s*use_default_settings:\s*true")
        self.assertNotIn("ultrasecretkey", t)                                                      # SearXNG refuses to start with its default secret

    def test_the_secret_key_is_a_placeholder_to_replace_not_a_real_secret(self):
        t = self.text("settings.yml")
        self.assertIn("REPLACE_WITH_A_RANDOM_SECRET", t)

    def test_compose_binds_to_loopback_only(self):
        t = self.text("docker-compose.yml")
        self.assertIn("127.0.0.1:8080:8080", t)
        self.assertNotIn("0.0.0.0:8080", t)
        self.assertIn("settings.yml", t)

    def test_the_message_the_person_sees_matches_the_shipped_file(self):
        self.assertIn("docker/searxng/settings.yml", SETTINGS_FIX)


if __name__ == "__main__":
    unittest.main()
