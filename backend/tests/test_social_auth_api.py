"""HTTP-level tests for Continue-with-provider (FastAPI TestClient; needs fastapi and httpx)."""
import unittest
from urllib.parse import parse_qs, urlsplit

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from fastapi.testclient import TestClient

from app.main import create_app
from support import GOOD_PASSWORD, Harness
from test_identity_platform import FakeHttp

ORIGIN = "http://localhost:5173"
FRONT = "http://localhost:5173"


class SocialApiTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(oauth_google_client_id="gid", oauth_google_client_secret="gsecret")
        self.addCleanup(self.h.close)
        self.http = FakeHttp()
        self.http.google = (200, {"sub": "g-1", "email": "nia@example.com", "email_verified": True, "name": "Nia"})
        self.app = create_app(self.h.settings, clock=self.h.clock, sender=self.h.outbox, http_client=self.http, database=self.h.db)

    def browser(self):
        return TestClient(self.app, raise_server_exceptions=False)

    def post(self, client, path, body=None):
        return client.post(f"/api/v1{path}", json=body, headers={"Origin": ORIGIN}, follow_redirects=False)

    def start(self, client, intent="register"):
        response = self.post(client, "/auth/oauth/google/signin", {"intent": intent})
        self.assertEqual(response.status_code, 200, response.text)
        url = response.json()["data"]["authorization_url"]
        return parse_qs(urlsplit(url).query)["state"][0], response

    def callback(self, client, state, **extra):
        return client.get("/api/v1/auth/oauth/google/callback", params={"code": "c", "state": state, **extra}, follow_redirects=False)

    def test_providers_are_listed_publicly_without_secrets(self):
        response = self.browser().get("/api/v1/auth/oauth/available")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["items"], [{"id": "google", "label": "Google", "configured": True},
                                                            {"id": "github", "label": "GitHub", "configured": False}])
        self.assertNotIn("gsecret", response.text)

    def test_register_end_to_end_sets_a_session_and_clears_the_binding_cookie(self):
        client = self.browser()
        state, started = self.start(client)
        cookie = started.headers.get_list("set-cookie")[0]
        for flag in ("pai_oauth=", "HttpOnly", "Path=/", "Max-Age=600"):
            self.assertIn(flag, cookie)
        self.assertRegex(cookie.lower(), r"samesite=lax")
        self.assertNotIn("gsecret", started.text)
        done = self.callback(client, state)
        self.assertEqual((done.status_code, done.headers["location"]), (302, f"{FRONT}/?oauth=registered"))
        cookies = " ".join(done.headers.get_list("set-cookie"))
        self.assertIn("pai_session=", cookies)
        self.assertIn("HttpOnly", cookies)
        me = client.get("/api/v1/me").json()["data"]
        self.assertEqual((me["email"], me["has_password"], me["connected_providers"]), ("nia@example.com", False, ["google"]))
        self.assertTrue(me["csrf_token"])

    def test_a_different_browser_cannot_finish_the_flow(self):
        victim, attacker = self.browser(), self.browser()
        state, _ = self.start(attacker)          # the attacker starts a flow and tricks the victim into opening the callback
        done = self.callback(victim, state)
        self.assertEqual(done.headers["location"], f"{FRONT}/register?oauth_error=invalid_token&provider=google")
        self.assertNotIn("pai_session=", " ".join(done.headers.get_list("set-cookie")))
        self.assertEqual(victim.get("/api/v1/me").status_code, 401)

    def test_failures_redirect_back_to_the_right_page_with_a_fixed_code_only(self):
        client = self.browser()
        state, _ = self.start(client, "login")
        done = self.callback(client, state)
        self.assertEqual(done.headers["location"], f"{FRONT}/login?oauth_error=oauth_no_account&provider=google")
        state, _ = self.start(client, "register")
        denied = self.callback(client, state, error="access_denied")
        self.assertEqual(denied.headers["location"], f"{FRONT}/register?oauth_error=denied&provider=google")
        self.assertNotIn("access_denied", denied.headers["location"])
        self.assertEqual(self.h.rows("SELECT * FROM users"), [])

    def test_a_password_account_with_the_same_email_is_not_taken_over(self):
        self.h.register("nia@example.com", GOOD_PASSWORD)
        client = self.browser()
        state, _ = self.start(client)
        done = self.callback(client, state)
        self.assertEqual(done.headers["location"], f"{FRONT}/register?oauth_error=oauth_email_exists&provider=google")
        self.assertEqual(client.get("/api/v1/me").status_code, 401)

    def test_start_requires_a_trusted_origin_and_a_valid_intent(self):
        client = self.browser()
        self.assertEqual(client.post("/api/v1/auth/oauth/google/signin", json={"intent": "login"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.post(client, "/auth/oauth/google/signin", {"intent": "root"}).status_code, 422)
        self.assertEqual(self.post(client, "/auth/oauth/github/signin", {"intent": "login"}).status_code, 503)  # not configured

    def test_password_less_account_can_delete_itself_over_http_while_fresh(self):
        client = self.browser()
        state, _ = self.start(client)
        self.callback(client, state)
        csrf = client.get("/api/v1/me").json()["data"]["csrf_token"]
        deleted = client.post("/api/v1/me/delete", json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.h.rows("SELECT status FROM users")[0]["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
