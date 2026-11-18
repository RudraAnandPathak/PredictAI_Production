"""Phase 8 over HTTP: research workspace routes (session, CSRF, isolation, limits) and privilege-escalation attempts that must all fail."""
import unittest

try:
    import test_workspace_api as wsbase  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from support import GOOD_PASSWORD


class ResearchWorkspaceApi(wsbase.WorkspaceApiBase):
    def test_every_route_needs_a_session_and_changes_need_csrf(self):
        for method, path in (("GET", "/research-workspaces"), ("POST", "/research-workspaces"), ("GET", "/research-workspaces/limits"), ("GET", "/research-workspaces/x"),
                             ("PATCH", "/research-workspaces/x"), ("DELETE", "/research-workspaces/x")):
            self.assertEqual(self.api.call(method, path, json={} if method in ("POST", "PATCH") else None, csrf=False).status_code, 401, f"{method} {path}")
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", "/research-workspaces", {"name": "x"}, csrf=False), 403), "CSRF_FAILED")
        self.assertEqual(self.error_code(self.api.call("DELETE", "/research-workspaces/x", csrf=False), 403), "CSRF_FAILED")

    def test_lifecycle_isolation_and_limits_over_http(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        made = alice("POST", "/research-workspaces", {"name": "Thesis", "description": "d"})
        self.assertEqual(made.status_code, 201, made.text)
        wid = made.json()["data"]["id"]
        self.assertEqual(alice("GET", f"/research-workspaces/{wid}").json()["data"]["name"], "Thesis")
        self.assertEqual(alice("PATCH", f"/research-workspaces/{wid}", {"status": "archived"}).json()["data"]["status"], "archived")
        listing = alice("GET", "/research-workspaces").json()["data"]
        self.assertEqual(len(listing["items"]), 1)
        self.assertEqual(listing["limits"]["tier"], "standard")
        # bob sees nothing of alice's and cannot change it; the answer is the same as for a missing id
        self.assertEqual(bob("GET", "/research-workspaces").json()["data"]["items"], [])
        for method, body in (("GET", None), ("PATCH", {"name": "mine"}), ("DELETE", None)):
            self.assertEqual(self.error_code(bob(method, f"/research-workspaces/{wid}", body), 404), "NOT_FOUND", method)
        self.assertEqual(self.error_code(alice("POST", "/research-workspaces", {"name": "thesis"}), 409), "WORKSPACE_NAME_TAKEN")
        self.assertEqual(self.error_code(alice("POST", "/research-workspaces", {"name": ""}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", "/research-workspaces", {}), 422), "VALIDATION_ERROR")
        self.assertEqual(alice("GET", "/research-workspaces/limits").json()["data"]["max_workspaces"], 5)
        for i in range(4):
            self.assertEqual(alice("POST", "/research-workspaces", {"name": f"w{i}"}).status_code, 201)
        self.assertEqual(self.error_code(alice("POST", "/research-workspaces", {"name": "one more"}), 403), "WORKSPACE_LIMIT_REACHED")
        self.assertEqual(alice("DELETE", f"/research-workspaces/{wid}").json()["data"]["deleted"], True)
        self.assertEqual(self.error_code(alice("GET", f"/research-workspaces/{wid}"), 404), "NOT_FOUND")


class EscalationAttempts(wsbase.WorkspaceApiBase):
    def test_registration_cannot_carry_a_role_tier_or_owner_flag(self):
        for extra in ({"role": "owner"}, {"roles": ["owner"]}, {"tier": "special"}, {"is_owner": True}, {"permissions": ["owner.users.read"]}, {"entitlements": ["special"]}):
            email = f"x{abs(hash(str(extra))) % 10**6}@example.com"
            self.api.call("POST", "/auth/register", {"email": email, "password": GOOD_PASSWORD, **extra}, csrf=False)
            client = self.api.browser()
            login = self.api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
            if login.status_code != 200:
                continue                                   # refused outright is also acceptable
            me = self.api.call("GET", "/me", client=client).json()["data"]
            self.assertFalse(any("owner." in p for p in me.get("permissions", [])), extra)
            self.assertNotIn("special", [t for t in me.get("tiers", [])], extra)
            self.assertNotIn("owner", me.get("roles", []), extra)

    def test_profile_update_cannot_change_roles_or_status(self):
        alice = self.user("alice@example.com")
        alice("PATCH", "/me", {"display_name": "Al", "roles": ["owner"], "tier": "special", "status": "active", "permissions": ["owner.users.read"]})
        me = alice("GET", "/me").json()["data"]
        self.assertNotIn("owner", me.get("roles", []))
        self.assertFalse(any(p.startswith("owner.") for p in me.get("permissions", [])))
        self.assertFalse(any(p == "uploads.unlimited" for p in me.get("permissions", [])))

    def test_a_normal_account_cannot_reach_any_owner_route(self):
        alice = self.user("alice@example.com")
        me = alice("GET", "/me").json()["data"]
        routes = [("GET", "/owner/users"), ("GET", f"/owner/users/{me['user']['id'] if 'user' in me else 'x'}"), ("POST", "/owner/owners"), ("GET", "/owner/audit-events"),
                  ("POST", "/owner/access-codes"), ("GET", "/owner/access-codes"), ("GET", "/owner/overview"), ("PUT", "/owner/maintenance"), ("PUT", "/owner/quota-policy/standard_max_documents"),
                  ("GET", "/owner/monitoring"), ("GET", "/owner/events")]
        for method, path in routes:
            r = alice(method, path, {"value": 1, "email": "alice@example.com"} if method != "GET" else None)
            self.assertIn(r.status_code, (403, 404, 422), f"{method} {path} -> {r.status_code}")
            self.assertNotEqual(r.status_code, 200, f"{method} {path}")
        # promoting oneself is refused too
        self.assertIn(alice("POST", "/owner/owners", {"user_id": me.get("user", {}).get("id", "x")}).status_code, (403, 404, 422))

    def test_wrong_activation_codes_are_throttled_and_never_grant_access(self):
        alice = self.user("alice@example.com")
        codes = [f"PAI-{i:05d}-AAAAA-BBBBB-CCCCC" for i in range(20)]
        statuses = [alice("POST", "/access-codes/activate", {"code": c}).status_code for c in codes]
        self.assertTrue(all(s in (400, 403, 422, 429) for s in statuses), statuses)
        self.assertIn(429, statuses, "repeated failures must be rate limited")
        entitlements = alice("GET", "/me/entitlements").json()["data"]
        self.assertEqual(entitlements["items"] if isinstance(entitlements, dict) and "items" in entitlements else entitlements, [])

    def test_there_is_no_public_route_that_creates_or_bootstraps_an_owner(self):
        for path in ("/owner/bootstrap", "/auth/bootstrap", "/auth/register-owner", "/owner/register", "/bootstrap-owner"):
            r = self.api.call("POST", path, {"email": "evil@example.com", "password": GOOD_PASSWORD}, csrf=False)
            self.assertIn(r.status_code, (404, 405, 401), path)
        schema_paths = repr(self.api.app.openapi().get("paths", {})) if hasattr(self.api.app, "openapi") else ""
        self.assertNotIn("bootstrap", schema_paths)


if __name__ == "__main__":
    unittest.main()
