"""Platform-wide authorisation sweep over EVERY registered route (not a hand-written list): anonymous callers get 401 everywhere except an explicit,
reviewed public list; a signed-in ordinary user is refused (403) on every owner route and every Development Workspace route; no route answers 5xx to
those probes; error bodies never carry stack traces or secrets."""
import re
import unittest

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise

# Routes that are public ON PURPOSE (sign-up/sign-in flow, liveness, OAuth redirects). Anything new must be added here deliberately.
PUBLIC = {
    ("GET", "/health"), ("GET", "/health/ready"),
    ("POST", "/auth/register"), ("POST", "/auth/login"), ("POST", "/auth/logout"),
    ("POST", "/auth/verify"), ("POST", "/auth/verify/resend"), ("POST", "/auth/recovery/request"), ("POST", "/auth/recovery/complete"), ("POST", "/auth/magic-link/request"), ("POST", "/auth/magic-link/consume"),
}
# Authenticated by a signed payload instead of a browser session (so no session and no CSRF token): the signature is checked in the handler.
# Designed to answer an ordinary user with a locked state instead of an error (Phase 9 requirement 2); the test checks it says so.
LOCKED_LANDING = {("GET", "/dev/overview")}
SIGNED_WEBHOOKS = {("POST", "/dev/webhooks/git/{repo_id}")}
PUBLIC_PREFIXES = ("/auth/oauth", "/oauth")          # provider redirect/callback endpoints are anonymous by nature


def api_routes(app):
    seen = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/v1"):
            continue
        for m in sorted(getattr(r, "methods", None) or []):
            if m in ("HEAD", "OPTIONS"):
                continue
            seen.add((m, path[len("/api/v1"):]))
    return sorted(seen)


def fill(path):
    return re.sub(r"\{[^}]+\}", "x", path)


class Sweep(base.ApiBase):
    def routes(self):
        rs = api_routes(self.api.app)
        self.assertGreater(len(rs), 300, "the sweep found suspiciously few routes")
        return rs

    def test_anonymous_callers_are_refused_everywhere_except_the_reviewed_public_list(self):
        bad, public_seen = [], []
        for m, p in self.routes():
            r = self.api.call(m, fill(p), json={} if m in ("POST", "PUT", "PATCH") else None, csrf=False)
            is_public = (m, p) in PUBLIC or p.startswith(PUBLIC_PREFIXES)
            if (m, p) in SIGNED_WEBHOOKS:
                self.assertEqual(r.status_code, 401, "an unsigned webhook must be rejected")
                continue
            if is_public:
                public_seen.append((m, p))
                continue
            if r.status_code != 401:
                bad.append(f"{m} {p} -> {r.status_code}")
        self.assertEqual(bad, [], "routes reachable without signing in")
        self.assertGreater(len(public_seen), 3)

    def test_no_route_leaks_a_stack_trace_or_answers_5xx_to_an_anonymous_probe(self):
        for m, p in self.routes():
            r = self.api.call(m, fill(p), json={"a": "' OR 1=1 --"} if m in ("POST", "PUT", "PATCH") else None, csrf=False)
            self.assertLess(r.status_code, 500, f"{m} {p} -> {r.status_code}")
            self.assertNotRegex(r.text, r"Traceback|File \"|sqlite3\.|OperationalError", f"{m} {p}")

    def test_an_ordinary_signed_in_user_cannot_reach_owner_or_special_only_areas(self):
        self.api.signed_in()
        leaks = []
        for m, p in self.routes():
            if not (p.startswith("/owner") or p.startswith("/dev") or p.startswith("/compute")):
                continue
            hdr = {"X-CSRF-Token": self.api.csrf} if self.api.csrf else {}
            # supply the parameters those routes require, so the permission check (not request validation) is what answers
            r = self.api.call(m, fill(p), json={"path": "a", "name": "a", "content": "x"} if m in ("POST", "PUT", "PATCH") else None, params={"path": "a", "q": "a", "a": 1, "b": 2}, headers=hdr)
            if (m, p) in LOCKED_LANDING:
                self.assertEqual(r.status_code, 200)
                self.assertFalse(r.json()["data"]["access"]["allowed"], "the landing page must report 'locked' for an ordinary user")
                continue
            if r.status_code not in (401, 403):
                leaks.append(f"{m} {p} -> {r.status_code}")
        self.assertEqual(leaks, [], "owner/special-only routes answered an ordinary user with something other than 401/403")

    def test_state_changing_routes_require_the_csrf_token_for_a_signed_in_browser(self):
        self.api.signed_in()
        weak = []
        for m, p in self.routes():
            if m not in ("POST", "PUT", "PATCH", "DELETE") or p.startswith(PUBLIC_PREFIXES) or (m, p) in PUBLIC or (m, p) in SIGNED_WEBHOOKS:
                continue
            r = self.api.call(m, fill(p), json={}, csrf=False)
            if r.status_code != 403:
                weak.append(f"{m} {p} -> {r.status_code}")
        self.assertEqual(weak, [], "state-changing routes that accepted a request without the CSRF token")


if __name__ == "__main__":
    unittest.main()
