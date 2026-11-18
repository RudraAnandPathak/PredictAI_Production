"""Phase 3 security sweep over the whole HTTP surface (FastAPI TestClient; needs fastapi and httpx)."""
import re
import unittest

try:
    import test_workspace_api as wsbase  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

PUBLIC = re.compile(r"^/api/v1(/health.*|/auth/register|/auth/login|/auth/verify(/resend)?|/auth/recovery/(request|complete)|/auth/magic-link/(request|consume)"
                    r"|/auth/logout|/auth/oauth/available|/auth/oauth/\{provider\}/(signin|callback)"
                    r"|/dev/webhooks/git/\{repo_id\})$")   # Phase 9: a Git host posts here with no session; it is authenticated by an HMAC signature (tested in test_p9_git / test_p9_api)
FILL = {"project_id": "a" * 32, "asset_id": "b" * 32, "upload_id": "c" * 32, "transformation_id": "d" * 32, "tid": "d" * 32,
        "session_id": "e" * 32, "provider": "google", "user_id": "f" * 32, "code_id": "9" * 32}


_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _walk(routes, prefix=""):
    """Concrete (method, path) pairs from app.routes, descending into any nested router/mount.

    Newer FastAPI versions keep lazy include objects in app.routes that have no ``path``/``methods`` of their own,
    so containers are followed through their own ``routes`` (or ``router.routes``) instead of being skipped."""
    for route in routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is not None and methods:
            for method in sorted(set(methods) & _VERBS):
                yield method, prefix + path
            continue
        children = getattr(route, "routes", None)
        if children is None:
            children = getattr(getattr(route, "router", None), "routes", None)
        if children:
            yield from _walk(children, prefix + (path or getattr(route, "prefix", "") or ""))


def routes(app):
    """Every API route, discovered rather than listed: the router tree and the OpenAPI schema, merged.

    The OpenAPI document is the framework's own authoritative list of registered operations, so it works however the
    router tree is represented internally; the tree walk adds routes that are excluded from the schema."""
    found = {(m, p) for m, p in _walk(app.routes) if p.startswith("/api/v1")}
    schema = app.openapi() if hasattr(app, "openapi") else {}  # real FastAPI always has it (the offline stand-in does not)
    for path, item in schema.get("paths", {}).items():
        for method in item:
            if method.upper() in _VERBS and path.startswith("/api/v1"):
                found.add((method.upper(), path))
    for pair in sorted(found, key=lambda x: (x[1], x[0])):
        yield pair


def concrete(path):
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: FILL.get(m.group(1), "a" * 32), path)


class RouteDiscoveryTests(unittest.TestCase):
    """The sweeps are only meaningful if discovery finds routes; newer FastAPI keeps lazy include objects in app.routes."""

    class _Lazy:  # has neither .path nor .methods, like the lazy include objects reported for FastAPI 0.162
        pass

    class _App:
        routes = None

        def __init__(self, routes, paths):
            self.routes, self._paths = routes, paths

        def openapi(self):
            return {"paths": self._paths}

    def test_lazy_include_objects_do_not_hide_routes(self):
        app = self._App([self._Lazy()], {"/api/v1/projects": {"get": {}, "post": {}}, "/api/v1/assets/{asset_id}": {"delete": {}, "parameters": []}})
        self.assertEqual(sorted(routes(app)), [("DELETE", "/api/v1/assets/{asset_id}"), ("GET", "/api/v1/projects"), ("POST", "/api/v1/projects")])

    def test_nested_routers_are_followed_with_their_prefix(self):
        leaf = type("R", (), {"path": "/x", "methods": {"GET", "HEAD", "OPTIONS"}})()
        container = type("C", (), {"routes": [leaf], "path": "/api/v1"})()
        self.assertEqual(list(routes(self._App([container], {}))), [("GET", "/api/v1/x")])

    def test_real_app_exposes_a_plausible_number_of_routes(self):
        api = wsbase.base.Api()
        self.addCleanup(api.close)
        self.assertGreater(len(list(routes(api.app))), 60)


class SecuritySweepTests(wsbase.WorkspaceApiBase):
    def setUp(self):
        self.api = wsbase.base.Api(upload_chunk_max_bytes=16, upload_max_bytes=4096)
        self.addCleanup(self.api.close)

    def push(self, call, project_id, name, data, chunk=16):
        import hashlib

        started = call("POST", f"/projects/{project_id}/uploads", {"filename": name, "size": len(data), "checksum_sha256": hashlib.sha256(data).hexdigest()})
        if started.status_code != 201:
            return None, started
        info = started.json()["data"]["upload"]
        for i in range(info["total_chunks"]):
            r = call("POST", f"/uploads/{info['id']}/chunks/{i}", content=data[i * chunk:(i + 1) * chunk], headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(r.status_code, 200, r.text)
        return info, call("POST", f"/uploads/{info['id']}/complete")

    def test_every_non_public_route_refuses_anonymous_callers(self):
        checked = 0
        for method, path in routes(self.api.app):
            if PUBLIC.match(path) or "/docs" in path or "openapi" in path or "redoc" in path:
                continue
            anonymous = self.api.browser()
            response = self.api.call(method, concrete(path.replace("/api/v1", "")), json={} if method in ("POST", "PATCH", "PUT") else None, client=anonymous, csrf=False)
            self.assertIn(response.status_code, (401, 403), f"{method} {path} -> {response.status_code}")
            checked += 1
        self.assertGreater(checked, 40)  # the sweep really covered the API (identity + workspace + owner)

    def test_every_state_changing_route_needs_the_csrf_token(self):
        alice = self.user("alice@example.com")  # signed in, but we deliberately omit the token below
        client = self.api.browser()
        self.api.register("carol@example.com")
        self.assertEqual(self.api.call("POST", "/auth/login", {"email": "carol@example.com", "password": wsbase.GOOD_PASSWORD}, client=client).status_code, 200)
        checked = 0
        for method, path in routes(self.api.app):
            if method in ("GET",) or PUBLIC.match(path):
                continue
            response = self.api.call(method, concrete(path.replace("/api/v1", "")), json={}, client=client, csrf=False)
            self.assertEqual(response.status_code, 403, f"{method} {path} -> {response.status_code} without a CSRF token")
            checked += 1
        self.assertGreater(checked, 20)

    def test_guessed_ids_are_indistinguishable_from_missing_ones_across_every_resource_family(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        _, done = self.push(alice, pid, "p.csv", b"id,name\n1,Ada\n")
        aid = done.json()["data"]["asset"]["id"]
        mine = [("GET", f"/projects/{pid}"), ("GET", f"/projects/{pid}/assets"), ("GET", f"/projects/{pid}/activity"), ("GET", f"/assets/{aid}"),
                ("GET", f"/assets/{aid}/download"), ("GET", f"/assets/{aid}/preview"), ("GET", f"/assets/{aid}/extraction"), ("GET", f"/assets/{aid}/profile"),
                ("GET", f"/assets/{aid}/history"), ("GET", f"/assets/{aid}/footprint"), ("DELETE", f"/assets/{aid}"),
                ("PATCH", f"/projects/{pid}"), ("POST", f"/projects/{pid}/archive"), ("POST", f"/projects/{pid}/restore"), ("POST", f"/projects/{pid}/delete"),
                ("PATCH", f"/assets/{aid}/retention"), ("POST", f"/assets/{aid}/cleaning/preview"), ("POST", f"/assets/{aid}/rollback"), ("POST", f"/projects/{pid}/uploads")]
        for method, path in mine:
            body = {"name": "x", "confirm_name": "P", "retain_until": "2030-01-01", "operations": [{"op": "drop_duplicates"}], "version_id": "a" * 32,
                    "confirm": True, "filename": "a.csv", "size": 5} if method != "GET" and method != "DELETE" else None
            theirs = bob(method, path, body)
            missing = bob(method, path.replace(pid, "0" * 32).replace(aid, "1" * 32), body)
            self.assertEqual((theirs.status_code, theirs.json()["error"]["code"]), (404, "NOT_FOUND"), path)
            self.assertEqual((theirs.status_code, theirs.json()["error"]["message"]), (missing.status_code, missing.json()["error"]["message"]), path)
        self.assertEqual(alice("GET", f"/assets/{aid}").status_code, 200)  # nothing above touched Alice's data

    def test_download_headers_survive_hostile_filenames(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        for name in ('evil"\r\nSet-Cookie: x=1.csv', "../../etc/passwd.csv", "résumé ✓ 日本語.csv", ("a" * 400) + ".csv", "<script>alert(1)</script>.csv"):
            _, done = self.push(alice, pid, name, b"id\n" + name.encode("utf-8", "ignore")[:20].replace(b"\n", b" ").replace(b"\r", b" ") + b"\n")
            self.assertEqual(done.status_code, 200, (name, done.text))
            aid = done.json()["data"]["asset"]["id"]
            got = alice("GET", f"/assets/{aid}/download")
            self.assertEqual(got.status_code, 200)
            header = got.headers["content-disposition"]
            self.assertNotIn("\r", header)
            self.assertNotIn("\n", header)
            self.assertNotIn("set-cookie", {k.lower() for k in got.headers.keys()})
            self.assertTrue(header.startswith("attachment"))
            self.assertEqual(got.headers.get("x-content-type-options"), "nosniff")

    def test_oversized_and_malformed_uploads_are_refused_without_a_server_error(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        too_big = alice("POST", f"/projects/{pid}/uploads", {"filename": "big.csv", "size": 10 ** 12})
        self.assertIn(too_big.status_code, (413, 422), too_big.text)
        for body in ({"filename": "", "size": 5}, {"filename": "a.csv", "size": -1}, {"filename": "a.csv", "size": "x"}, {}):
            self.assertEqual(alice("POST", f"/projects/{pid}/uploads", body).status_code, 422, body)
        self.assertEqual(alice("POST", "/uploads/" + "0" * 32 + "/chunks/0", content=b"x", headers={"Content-Type": "application/octet-stream"}).status_code, 404)

    def test_responses_never_carry_secrets_or_server_paths(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        _, done = self.push(alice, pid, "p.csv", b"id,name\n1,Ada\n")
        aid = done.json()["data"]["asset"]["id"]
        blob = " ".join(alice("GET", p).text for p in (f"/projects/{pid}", f"/assets/{aid}", f"/assets/{aid}/extraction", f"/assets/{aid}/footprint", "/workspace/inventory", "/me"))
        for forbidden in ("/tmp/", "storage/", "Traceback", "password_hash", "secret", "token_hash", "storage_key"):
            self.assertNotIn(forbidden, blob, forbidden)


if __name__ == "__main__":
    unittest.main()
