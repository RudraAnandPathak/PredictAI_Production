"""Personal API tokens: created in the browser with a password, usable only as Authorization: Bearer on the agent routes, revocable,
expiring, and revoked automatically by password changes, blocking and account deletion."""
import re
import unittest

import test_identity_api as base
from app.modules.identity import api_tokens as T

GOOD = base.GOOD_PASSWORD


class TokenApi(base.ApiBase):
    def make_token(self, name="Agent", days=90):
        r = self.api.call("POST", "/me/api-tokens", {"name": name, "password": GOOD, "expires_in_days": days})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def bearer(self, method, path, token, json=None, content=None, headers=None):
        """A request with ONLY the token: a fresh client (no cookie), no Origin, no CSRF header."""
        c = self.api.browser()
        h = {"Authorization": f"Bearer {token}", **(headers or {})}
        kw = {"content": content} if content is not None else {}
        return c.request(method, f"/api/v1{path}", json=json, headers=h, follow_redirects=False, **kw)

    def test_create_needs_a_session_and_the_password_and_the_token_is_shown_once(self):
        self.api.signed_in()
        wrong = self.api.call("POST", "/me/api-tokens", {"name": "x", "password": "wrong-password-123", "expires_in_days": 90})
        self.assertEqual((wrong.status_code, wrong.json()["error"]["code"]), (400, "INVALID_PASSWORD"))
        t = self.make_token()
        self.assertRegex(t["token"], r"^pai_[A-Za-z0-9_-]{40,64}$")
        self.assertEqual(t["prefix"], t["token"][:10])
        listed = self.api.call("GET", "/me/api-tokens").json()["data"]["items"]
        self.assertEqual([x["id"] for x in listed], [t["id"]])
        self.assertNotIn(t["token"], self.api.call("GET", "/me/api-tokens").text)       # never shown again
        row = self.api.h.services().ctx.conn.execute("SELECT token_hash FROM api_tokens").fetchone()
        self.assertNotEqual(row[0], t["token"])                                          # only the hash is stored
        self.assertEqual(self.api.call("POST", "/me/api-tokens", {"name": "x", "password": GOOD, "expires_in_days": 7}).status_code, 422)

    def test_a_token_works_on_agent_routes_without_cookie_origin_or_csrf(self):
        self.api.signed_in()
        tok = self.make_token()["token"]
        me = self.bearer("GET", "/me", tok)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertTrue(me.json()["data"]["via_api_token"])
        self.assertIsNone(me.json()["data"]["csrf_token"])
        r = self.bearer("POST", "/projects", tok, json={"name": "From an agent"})
        self.assertIn(r.status_code, (200, 201), r.text)
        names = [p["name"] for p in self.bearer("GET", "/projects", tok).json()["data"]["items"]]
        self.assertIn("From an agent", names)

    def test_a_token_is_refused_everywhere_else(self):
        self.api.signed_in()
        tok = self.make_token()["token"]
        for method, path, body in (("GET", "/owner/users", None), ("PATCH", "/me", {"display_name": "x"}), ("POST", "/me/password", {"current_password": GOOD, "new_password": "another-good-password-1"}),
                                   ("POST", "/me/delete", {"password": GOOD}), ("GET", "/me/api-tokens", None), ("POST", "/me/api-tokens", {"name": "x", "password": GOOD}),
                                   ("DELETE", "/assets/abc", None), ("DELETE", "/ml/models/abc", None), ("GET", "/research-intelligence/workspaces", None)):
            r = self.bearer(method, path, tok, json=body)
            self.assertEqual(r.status_code, 403, f"{method} {path}: {r.text}")
            self.assertEqual(r.json()["error"]["code"], "TOKEN_NOT_ALLOWED", f"{method} {path}")

    def test_invalid_revoked_and_expired_tokens_are_rejected(self):
        self.api.signed_in()
        t = self.make_token(days=30)
        self.assertEqual(self.bearer("GET", "/projects", "pai_" + "x" * 48).status_code, 401)
        self.assertEqual(self.bearer("GET", "/projects", "pai_short").status_code, 401)
        self.assertEqual(self.api.call("DELETE", f"/me/api-tokens/{t['id']}").status_code, 200)
        r = self.bearer("GET", "/projects", t["token"])
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "TOKEN_INVALID"))
        t2 = self.make_token(days=30)
        self.api.h.clock.advance(days=31)
        self.assertEqual(self.bearer("GET", "/projects", t2["token"]).status_code, 401)

    def test_a_password_change_revokes_tokens(self):
        self.api.signed_in()
        tok = self.make_token()["token"]
        r = self.api.call("POST", "/me/password", {"current_password": GOOD, "new_password": "a-brand-new-password-42"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.bearer("GET", "/projects", tok).status_code, 401)

    def test_account_deletion_removes_tokens(self):
        self.api.signed_in()
        tok = self.make_token()["token"]
        self.assertEqual(self.api.call("POST", "/me/delete", {"password": GOOD}).status_code, 200)
        self.assertEqual(self.bearer("GET", "/projects", tok).status_code, 401)
        self.assertEqual(self.api.h.services().ctx.conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0], 0)

    def test_a_blocked_account_cannot_use_its_tokens(self):
        self.api.signed_in("bob@example.com")
        tok = self.make_token()["token"]
        conn = self.api.h.services().ctx.conn
        conn.execute("UPDATE users SET status = 'blocked' WHERE email_normalized = 'bob@example.com'")
        self.assertEqual(self.bearer("GET", "/projects", tok).status_code, 401)

    def test_a_token_only_reaches_its_own_persons_data(self):
        self.api.signed_in("alice@example.com")
        alice_tok = self.make_token()["token"]
        pid = self.bearer("POST", "/projects", alice_tok, json={"name": "Alice only"}).json()["data"]["id"]
        other = base.Api()
        self.addCleanup(other.close)
        other.signed_in("mallory@example.com")
        r = other.call("POST", "/me/api-tokens", {"name": "m", "password": GOOD, "expires_in_days": 90})
        mtok = r.json()["data"]["token"]
        c = other.browser()
        res = c.get(f"/api/v1/projects/{pid}", headers={"Authorization": f"Bearer {mtok}"})
        self.assertIn(res.status_code, (403, 404))

    def test_at_most_ten_active_tokens(self):
        self.api.signed_in()
        for i in range(10):
            self.make_token(name=f"t{i}")
        r = self.api.call("POST", "/me/api-tokens", {"name": "eleven", "password": GOOD, "expires_in_days": 90})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "TOKEN_LIMIT"))

    def test_cookie_requests_still_need_csrf(self):
        self.api.signed_in()
        self.assertEqual(self.api.call("POST", "/projects", {"name": "x"}, csrf=False).status_code, 403)


class Allowlist(unittest.TestCase):
    def test_every_allowed_route_exists_and_none_deletes_or_touches_owner_or_account_tools(self):
        from app.main import create_app
        import support
        h = support.Harness()
        self.addCleanup(h.close)
        app = create_app(h.settings, clock=h.clock, sender=h.outbox, database=h.db)
        routes = []
        for r in app.routes:
            path = getattr(r, "path", "")
            if path.startswith("/api/v1"):
                for m in getattr(r, "methods", set()) or set():
                    routes.append((m, re.sub(r"\{[^}]+\}", "abc123", re.sub(r"\{name\}", "dashboard", re.sub(r"\{index\}", "7", path[len("/api/v1"):])))))
        for method, pat in T.ALLOWED_ROUTES:
            self.assertNotEqual(method, "DELETE")
            self.assertTrue(any(m == method and pat.match(p) for m, p in routes), f"no real route for {method} {pat.pattern}")
            self.assertFalse(pat.pattern.startswith("^/owner") or "api-tokens" in pat.pattern or "/auth" in pat.pattern)
        for m, p in routes:
            if m == "DELETE" or p.startswith(("/owner", "/auth", "/me/")):
                self.assertFalse(T.route_allowed(m, p), f"{m} {p} must not be allowed for tokens")

    def test_bearer_parsing(self):
        tok = "pai_" + "a" * 48
        self.assertEqual(T.bearer(f"Bearer {tok}"), tok)
        self.assertEqual(T.bearer(f"bearer   {tok}"), tok)
        self.assertIsNone(T.bearer("Bearer eyJhbGciOi"))
        self.assertIsNone(T.bearer(None))


if __name__ == "__main__":
    unittest.main()
