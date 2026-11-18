"""HTTP-level tests for the identity API (FastAPI TestClient). They need fastapi and httpx:
pip install -r requirements-dev.txt"""
import json
import re
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    import httpx  # noqa: F401
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("fastapi and httpx are required: pip install -r requirements-dev.txt") from exc

import os
import secrets

# Importing app.main builds the module-level `app`, which in development would create and persist a dev secret under
# data/. Tests must not touch the project folder, so give that import a throwaway secret first.
os.environ.setdefault("PREDICTAI_SECRET_KEY", secrets.token_urlsafe(48))

from app.main import create_app
from support import GOOD_PASSWORD, OTHER_PASSWORD, Harness

ORIGIN = "http://localhost:5173"
CONTRACTS = Path(__file__).resolve().parents[2] / "shared" / "contracts"


class Api:
    """One app, one database, one 'browser' (cookie jar). Remembers the CSRF token like the frontend does."""

    def __init__(self, **overrides):
        self.h = Harness(**overrides)
        self.app = create_app(self.h.settings, clock=self.h.clock, sender=self.h.outbox, database=self.h.db)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.csrf = None
        self.bodies = []

    def close(self):
        runner = getattr(self.app.state, "ml_runner", None)     # the app's background-training pool (if any) must not outlive the test
        if runner is not None:
            runner.shutdown()
        self.h.close()

    def browser(self):
        other = TestClient(self.app, raise_server_exceptions=False)
        return other

    def call(self, method, path, json=None, params=None, csrf=True, origin=ORIGIN, client=None, headers=None, content=None):
        merged = dict(headers or {})
        if origin:
            merged["Origin"] = origin
        if csrf and self.csrf:
            merged["X-CSRF-Token"] = self.csrf
        kwargs = {"content": content} if content is not None else {}
        response = (client or self.client).request(method, f"/api/v1{path}", json=json, params=params, headers=merged, follow_redirects=False, **kwargs)
        self.bodies.append(response.text)
        return response

    def register(self, email="alice@example.com", password=GOOD_PASSWORD, name=None):
        return self.call("POST", "/auth/register", {"email": email, "password": password, "display_name": name})

    def login(self, email="alice@example.com", password=GOOD_PASSWORD, **extra):
        response = self.call("POST", "/auth/login", {"email": email, "password": password, **extra})
        if response.status_code == 200:
            self.csrf = response.json()["data"]["csrf_token"]
        return response

    def signed_in(self, email="alice@example.com", password=GOOD_PASSWORD):
        self.register(email, password)
        assert self.login(email, password).status_code == 200
        return self

    def owner_signed_in(self, email="owner@example.com"):
        from app.modules.identity.provisioning import provision_owner

        provision_owner(self.h.services().ctx, email=email, password=GOOD_PASSWORD, allow_additional=True)
        assert self.login(email, GOOD_PASSWORD).status_code == 200


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.api = Api()
        self.addCleanup(self.api.close)

    def error_code(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]) - {"details"}, {"code", "message", "request_id"})
        return body["error"]["code"]


class RegistrationAndLoginApiTests(ApiBase):
    def test_registration_answers_202_with_the_standard_envelope(self):
        response = self.api.register()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["data"], {"verification_required": False})
        self.assertTrue(response.json()["meta"]["request_id"])

    def test_a_duplicate_registration_is_indistinguishable(self):
        first, second = self.api.register(), self.api.register()
        self.assertEqual((first.status_code, first.json()["data"]), (second.status_code, second.json()["data"]))

    def test_invalid_input_reports_fields_without_echoing_values(self):
        response = self.api.register("not-an-email", "short")
        self.assertEqual(self.error_code(response, 422), "VALIDATION_ERROR")
        fields = {d["field"] for d in response.json()["error"]["details"]}
        self.assertEqual(fields, {"email", "password"})
        self.assertNotIn("not-an-email", response.text)

    def test_missing_fields_are_a_validation_error(self):
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/login", {"email": "a@example.com"}), 422), "VALIDATION_ERROR")

    def test_login_sets_a_hardened_cookie_and_returns_the_profile_without_secrets(self):
        self.api.register()
        response = self.api.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers.get_list("set-cookie")[0]
        for flag in ("HttpOnly", "Path=/", "Max-Age="):
            self.assertIn(flag, cookie)
        self.assertRegex(cookie.lower(), r"samesite=lax")
        self.assertNotIn("Secure", cookie)  # test environment only; hosted environments force Secure (see config tests)
        data = response.json()["data"]
        self.assertEqual((data["email"], data["roles"], data["tiers"]), ("alice@example.com", ["user"], ["standard"]))
        self.assertTrue(data["csrf_token"])
        for forbidden in ("password", "hash", "token_hash"):
            self.assertNotIn(forbidden, set(data), forbidden)

    def test_remember_me_false_sets_a_browser_session_cookie(self):
        self.api.register()
        cookie = self.api.login(remember_me=False).headers.get_list("set-cookie")[0]
        self.assertNotIn("Max-Age", cookie)

    def test_wrong_credentials_are_one_generic_401(self):
        self.api.register()
        wrong = self.api.login(password="wrong-wrong-1")
        unknown = self.api.login(email="ghost@example.com")
        self.assertEqual(self.error_code(wrong, 401), "INVALID_CREDENTIALS")
        self.assertEqual(wrong.json()["error"]["message"], unknown.json()["error"]["message"])

    def test_lockout_returns_429_with_retry_after(self):
        self.api.register()
        for _ in range(5):
            self.api.login(password="wrong-wrong-1")
        response = self.api.login()
        self.assertEqual(self.error_code(response, 429), "RATE_LIMITED")
        self.assertGreater(int(response.headers["retry-after"]), 0)


class SessionApiTests(ApiBase):
    def test_me_requires_a_session_and_survives_refreshes_and_new_browsers(self):
        self.assertEqual(self.error_code(self.api.call("GET", "/me"), 401), "UNAUTHENTICATED")
        self.api.signed_in()
        for _ in range(3):  # "page refreshes"
            self.assertEqual(self.api.call("GET", "/me").json()["data"]["email"], "alice@example.com")
        other = self.api.browser()
        other.cookies.set(self.api.h.settings.session_cookie_name, self.api.client.cookies.get(self.api.h.settings.session_cookie_name))
        self.assertEqual(self.api.call("GET", "/me", client=other).status_code, 200)   # server-side session, not browser state

    def test_logout_ends_the_session_and_clears_the_cookie(self):
        self.api.signed_in()
        name = self.api.h.settings.session_cookie_name
        old_cookie = self.api.client.cookies.get(name)
        response = self.api.call("POST", "/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", response.headers.get_list("set-cookie")[0])
        replay = self.api.browser()
        replay.cookies.set(name, old_cookie)
        denied = self.api.call("GET", "/me", client=replay)
        self.assertEqual(self.error_code(denied, 401), "SESSION_EXPIRED")                       # the old token is dead server-side
        self.assertIn("Max-Age=0", denied.headers.get_list("set-cookie")[0])                    # and the browser is told to drop it

    def test_idle_expiry_returns_session_expired(self):
        self.api.signed_in()
        self.api.h.clock.advance(hours=25)
        response = self.api.call("GET", "/me")
        self.assertEqual(self.error_code(response, 401), "SESSION_EXPIRED")

    def test_session_listing_and_revocation(self):
        self.api.signed_in()
        second = self.api.browser()
        self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=second)
        items = self.api.call("GET", "/auth/sessions").json()["data"]["items"]
        self.assertEqual(len(items), 2)
        other = [s for s in items if not s["current"]][0]
        self.assertEqual(self.api.call("DELETE", f"/auth/sessions/{other['id']}").status_code, 200)
        self.assertEqual(self.error_code(self.api.call("GET", "/me", client=second), 401), "SESSION_EXPIRED")

    def test_unauthorised_session_ids_look_like_404(self):
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("DELETE", "/auth/sessions/deadbeef"), 404), "NOT_FOUND")


class CsrfApiTests(ApiBase):
    def test_state_changing_requests_need_the_csrf_token_once_signed_in(self):
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/logout", csrf=False), 403), "CSRF_FAILED")
        bad = self.api.call("POST", "/auth/logout", csrf=False, headers={"X-CSRF-Token": "forged"})
        self.assertEqual(self.error_code(bad, 403), "CSRF_FAILED")
        self.assertEqual(self.api.call("GET", "/me", csrf=False).status_code, 200)                  # safe methods never need it
        self.assertEqual(self.api.call("POST", "/auth/logout").status_code, 200)

    def test_cross_site_origins_are_refused_even_before_sign_in(self):
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/login", {"email": "a@example.com", "password": "x"}, origin="https://evil.example"), 403), "CSRF_FAILED")
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/login", {"email": "a@example.com", "password": "x"}, origin="null"), 403), "CSRF_FAILED")

    def test_requests_without_an_origin_header_work_when_there_is_no_session(self):
        self.api.register()
        self.assertEqual(self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, origin=None).status_code, 200)

    def test_csrf_failures_are_audited(self):
        self.api.call("POST", "/auth/login", {"email": "a@example.com", "password": "x"}, origin="https://evil.example")
        self.assertEqual(self.api.h.rows("SELECT outcome, reason FROM audit_events WHERE event_type = 'csrf.rejected'"), [{"outcome": "denied", "reason": "origin"}])

    def test_cors_preflight_allows_credentials_for_our_origin_only(self):
        ok = self.api.client.options("/api/v1/auth/login", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-token"})
        self.assertEqual(ok.headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(ok.headers["access-control-allow-credentials"], "true")
        self.assertIn("x-csrf-token", ok.headers["access-control-allow-headers"].lower())
        bad = self.api.client.options("/api/v1/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", bad.headers)


class PasswordApiTests(ApiBase):
    def test_changing_the_password_rotates_the_cookie(self):
        self.api.signed_in()
        name = self.api.h.settings.session_cookie_name
        before = self.api.client.cookies.get(name)
        wrong = self.api.call("POST", "/me/password", {"current_password": "wrong-wrong-1", "new_password": OTHER_PASSWORD})
        self.assertEqual(self.error_code(wrong, 400), "INVALID_PASSWORD")
        response = self.api.call("POST", "/me/password", {"current_password": GOOD_PASSWORD, "new_password": OTHER_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.api.csrf = response.json()["data"]["csrf_token"]
        self.assertNotEqual(self.api.client.cookies.get(name), before)
        self.assertEqual(self.api.call("GET", "/me").status_code, 200)
        self.assertEqual(self.error_code(self.api.login(password=GOOD_PASSWORD), 401), "INVALID_CREDENTIALS")
        self.assertEqual(self.api.login(password=OTHER_PASSWORD).status_code, 200)

    def test_recovery_flow_over_http(self):
        self.api.register()
        self.assertEqual(self.api.call("POST", "/auth/recovery/request", {"email": "alice@example.com"}).status_code, 202)
        self.assertEqual(self.api.call("POST", "/auth/recovery/request", {"email": "ghost@example.com"}).json()["data"], {"sent": True})
        token = self.api.h.last_token("alice@example.com")
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/recovery/complete", {"token": "bogus", "new_password": OTHER_PASSWORD}), 400), "INVALID_TOKEN")
        self.assertEqual(self.api.call("POST", "/auth/recovery/complete", {"token": token, "new_password": OTHER_PASSWORD}).status_code, 200)
        self.assertEqual(self.api.login(password=OTHER_PASSWORD).status_code, 200)

    def test_account_deletion(self):
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", "/me/delete", {"password": "wrong-wrong-1"}), 400), "INVALID_PASSWORD")
        response = self.api.call("POST", "/me/delete", {"password": GOOD_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", response.headers.get_list("set-cookie")[0])
        self.assertEqual(self.error_code(self.api.login(), 401), "INVALID_CREDENTIALS")

    def test_profile_update(self):
        self.api.signed_in()
        self.assertEqual(self.api.call("PATCH", "/me", {"display_name": "  Alice  A "}).json()["data"]["display_name"], "Alice A")


class AuthorizationApiTests(ApiBase):
    def test_owner_routes_return_401_when_anonymous_and_403_for_ordinary_users(self):
        for method, path in (("GET", "/owner/users"), ("GET", "/owner/audit-events"), ("GET", "/owner/access-codes"), ("POST", "/owner/access-codes")):
            with self.subTest(path=path):
                self.assertEqual(self.error_code(self.api.call(method, path, {} if method == "POST" else None), 401), "UNAUTHENTICATED")
        self.api.signed_in()
        for method, path in (("GET", "/owner/users"), ("GET", "/owner/audit-events"), ("POST", "/owner/access-codes"), ("POST", "/owner/owners")):
            with self.subTest(path=path):
                response = self.api.call(method, path, {"user_id": "x"} if method == "POST" else None)
                self.assertEqual(self.error_code(response, 403), "FORBIDDEN")
        self.assertGreaterEqual(self.api.h.rows("SELECT COUNT(*) AS n FROM audit_events WHERE event_type = 'authz.denied'")[0]["n"], 4)

    def test_special_access_code_lifecycle_over_http(self):
        api = self.api
        api.owner_signed_in()
        owner_csrf, owner_client = api.csrf, api.client
        user_api_client = api.browser()
        api.call("POST", "/auth/register", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=user_api_client)
        login = api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=user_api_client)
        user_csrf = login.json()["data"]["csrf_token"]
        user_id = login.json()["data"]["id"]

        def as_user(method, path, body=None):
            api.csrf = user_csrf
            return api.call(method, path, body, client=user_api_client)

        def as_owner(method, path, body=None, params=None):
            api.csrf = owner_csrf
            return api.call(method, path, body, params=params, client=owner_client)

        created = as_owner("POST", "/owner/access-codes", {"label": "for Alice", "expires_in_days": 7})
        self.assertEqual(created.status_code, 201)
        code = created.json()["data"]["code"]
        listing = as_owner("GET", "/owner/access-codes")
        self.assertNotIn(code, listing.text)

        self.assertEqual(self.error_code(as_user("POST", "/access-codes/activate", {"code": "PAI-WRONG-WRONG-WRONG-WRONG"}), 400), "INVALID_CODE")
        self.assertNotIn("uploads.unlimited", as_user("GET", "/me").json()["data"]["permissions"])
        self.assertEqual(as_user("POST", "/access-codes/activate", {"code": code}).json()["data"]["tier"], "special")
        self.assertIn("uploads.unlimited", as_user("GET", "/me").json()["data"]["permissions"])
        self.assertEqual(self.error_code(as_user("POST", "/access-codes/activate", {"code": code}), 400), "INVALID_CODE")   # no reuse

        entitlement = as_owner("GET", f"/owner/users/{user_id}").json()["data"]["entitlements"][0]["id"]
        self.assertEqual(as_owner("POST", f"/owner/entitlements/{entitlement}/revoke", {"reason": "test"}).status_code, 200)
        self.assertNotIn("uploads.unlimited", as_user("GET", "/me").json()["data"]["permissions"])                          # immediate
        events = as_owner("GET", "/owner/audit-events", params={"event_type": "access_code"}).json()["data"]["items"]
        self.assertTrue({"access_code.created", "access_code.activated", "access_code.activation_failed"} <= {e["event_type"] for e in events})
        self.assertNotIn(code, json.dumps(events))

    def test_owner_changes_need_a_fresh_password_confirmation(self):
        api = self.api
        api.owner_signed_in()
        api.signed_in("bob@example.com", OTHER_PASSWORD) if False else None
        target = api.h.make_user("bob@example.com", OTHER_PASSWORD)[0].user_id
        api.h.clock.advance(minutes=11)
        self.assertEqual(self.error_code(api.call("POST", f"/owner/users/{target}/block", {"reason": "x"}), 403), "REAUTH_REQUIRED")
        self.assertEqual(self.error_code(api.call("POST", "/auth/reauth", {"password": "wrong-wrong-1"}), 400), "INVALID_PASSWORD")
        reauth = api.call("POST", "/auth/reauth", {"password": GOOD_PASSWORD})
        self.assertEqual(reauth.status_code, 200)
        api.csrf = reauth.json()["data"]["csrf_token"]
        self.assertEqual(api.call("POST", f"/owner/users/{target}/block", {"reason": "x"}).status_code, 200)
        self.assertEqual(self.error_code(api.login("bob@example.com", OTHER_PASSWORD), 401), "INVALID_CREDENTIALS")

    def test_user_listing_search_and_privacy(self):
        api = self.api
        api.h.make_user("alice@example.com")
        api.owner_signed_in()
        page = api.call("GET", "/owner/users", params={"q": "alice"}).json()["data"]
        self.assertEqual([u["email"] for u in page["items"]], ["alice@example.com"])
        self.assertTrue(all("password" not in key and "hash" not in key for key in page["items"][0]))


class MiscApiTests(ApiBase):
    def test_oauth_without_credentials_degrades_cleanly(self):
        self.api.signed_in()
        providers = self.api.call("GET", "/auth/oauth").json()["data"]["items"]
        self.assertEqual({p["id"]: p["configured"] for p in providers}, {"google": False, "github": False})
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/oauth/google/link"), 503), "PROVIDER_NOT_CONFIGURED")
        anonymous = self.api.browser()
        redirect = self.api.call("GET", "/auth/oauth/google/callback", params={"code": "x", "state": "y"}, client=anonymous)
        self.assertEqual((redirect.status_code, redirect.headers["location"]), (302, "http://localhost:5173/login?next=/account"))
        signed_in = self.api.call("GET", "/auth/oauth/google/callback", params={"code": "x", "state": "forged"})
        self.assertEqual((signed_in.status_code, signed_in.headers["location"]), (302, "http://localhost:5173/account?oauth=error&provider=google"))

    def test_phase_1_health_endpoints_still_work(self):
        live = self.api.call("GET", "/health")
        self.assertEqual(live.json()["data"]["status"], "ok")
        ready = self.api.call("GET", "/health/ready")
        self.assertEqual((ready.status_code, ready.json()["data"]["checks"]["database"]), (200, "ok"))
        self.assertEqual(self.error_code(self.api.call("GET", "/nope"), 404), "NOT_FOUND")

    def test_responses_never_leak_secrets(self):
        self.api.signed_in()
        self.api.call("POST", "/auth/recovery/request", {"email": "alice@example.com"})
        token = self.api.h.last_token("alice@example.com")  # the reset link's token, read before later notices arrive
        self.api.call("POST", "/me/password", {"current_password": GOOD_PASSWORD, "new_password": OTHER_PASSWORD})
        everything = "\n".join(self.api.bodies)
        for secret in (GOOD_PASSWORD, OTHER_PASSWORD, token, "scrypt$", "password_hash", "token_hash"):
            self.assertNotIn(secret, everything)

    def test_error_envelope_matches_the_shared_contract(self):
        error = self.api.call("GET", "/me").json()
        self.assertEqual(set(error["error"]), set(json.loads((CONTRACTS / "error.example.json").read_text())["error"]))

    def test_security_headers_are_present_on_authenticated_responses(self):
        self.api.signed_in()
        headers = self.api.call("GET", "/me").headers
        self.assertEqual((headers["x-content-type-options"], headers["cache-control"]), ("nosniff", "no-store"))
        self.assertTrue(headers["x-request-id"])


if __name__ == "__main__":
    unittest.main()
