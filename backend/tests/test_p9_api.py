"""Phase 9 over HTTP: session + CSRF on every route, the Special-access gate, project isolation, a job lifecycle, the owner override route and the signed webhook route."""
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from app.modules.devspace.engine import Worker
from support import GOOD_PASSWORD


class DevApi(base.ApiBase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = base.Api(dev_execution="local_trusted", dev_root=os.path.join(self.tmp.name, "dev"), dev_pipelines_enabled=True, dev_git_allow_local=True)
        self.addCleanup(self.api.close)
        self.owner_p, _ = self.api.h.make_owner()

    def user(self, email, special=True):
        client = self.api.browser()
        self.api.register(email)
        resp = self.api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
        self.assertEqual(resp.status_code, 200)
        token = resp.json()["data"]["csrf_token"]
        if special:
            uid = self.api.h.conn.execute("SELECT id FROM users WHERE email_normalized = ?", (email,)).fetchone()[0]
            self.api.h.services().codes.grant_special(self.owner_p, uid)

        def call(method, path, json=None, params=None, headers=None, content=None):
            return self.api.call(method, path, json=json, params=params, csrf=False, client=client, headers={"X-CSRF-Token": token, **(headers or {})}, content=content)
        return call

    def project(self, call):
        r = call("POST", "/projects", {"name": "Dev"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id"]

    def drain(self):
        w = Worker(self.api.h.db, self.api.h.settings, self.api.h.clock, poll=0.01)
        try:
            while w.tick() is not None:
                pass
        finally:
            w.close()

    def test_every_route_needs_a_session_and_changes_need_csrf(self):
        routes = (("GET", "/dev/overview"), ("GET", "/dev/projects/x/files"), ("PUT", "/dev/projects/x/files/content"), ("POST", "/dev/projects/x/jobs"), ("GET", "/dev/projects/x/jobs"),
                  ("POST", "/dev/projects/x/git/push"), ("POST", "/dev/projects/x/pipelines/y/run"), ("POST", "/dev/projects/x/pipeline-runs/y/deploy"), ("PUT", "/dev/owner/compute-overrides"),
                  ("GET", "/dev/compute/capabilities"), ("POST", "/dev/projects/x/secrets"), ("POST", "/dev/projects/x/sessions"), ("GET", "/dev/projects/x/experiments/y/lineage"))
        for method, path in routes:
            r = self.api.call(method, path, json={} if method != "GET" else None, csrf=False)
            self.assertEqual(r.status_code, 401, f"{method} {path}")
        self.api.signed_in()
        for method, path in (("PUT", "/dev/projects/x/files/content"), ("POST", "/dev/projects/x/jobs"), ("POST", "/dev/projects/x/git/push"), ("PUT", "/dev/owner/compute-overrides")):
            self.assertEqual(self.error_code(self.api.call(method, path, {}, csrf=False), 403), "CSRF_FAILED", path)

    def test_people_without_special_access_get_403_everywhere_and_a_locked_landing_page(self):
        plain = self.user("plain@example.com", special=False)
        pid = self.project(plain)
        for method, path in (("GET", f"/dev/projects/{pid}/files"), ("POST", f"/dev/projects/{pid}/jobs"), ("GET", f"/dev/projects/{pid}/envs"), ("GET", f"/dev/projects/{pid}/git"), ("GET", f"/dev/projects/{pid}/history"),
                             ("GET", "/dev/compute/capabilities")):
            self.assertEqual(self.error_code(plain(method, path, {} if method == "POST" else None), 403), "SPECIAL_ACCESS_REQUIRED", path)
        landing = plain("GET", "/dev/overview")
        self.assertEqual(landing.status_code, 200)
        self.assertEqual(landing.json()["data"], {"access": {"allowed": False, "reason": "The Development Workspace is available to people with Special access. Ask the platform owner for access."}})

    def test_lifecycle_over_http_with_isolation_between_people(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = self.project(alice)
        r = alice("PUT", f"/dev/projects/{pid}/files/content", {"path": "hello.py", "content": "import os\nprint('hi from', os.environ['PREDICTAI_JOB_ID'][:4])\nopen(os.environ['PREDICTAI_OUTPUT_DIR'] + '/r.txt', 'w').write('x')\n"})
        self.assertEqual(r.status_code, 200, r.text)
        job = alice("POST", f"/dev/projects/{pid}/jobs", {"kind": "script", "entry": "hello.py"})
        self.assertEqual(job.status_code, 201, job.text)
        jid = job.json()["data"]["id"]
        self.assertEqual(alice("GET", f"/dev/projects/{pid}/jobs/{jid}").json()["data"]["status"], "queued")      # the API process never runs it
        self.drain()
        done = alice("GET", f"/dev/projects/{pid}/jobs/{jid}").json()["data"]
        self.assertEqual((done["status"], done["exit_code"]), ("succeeded", 0))
        self.assertTrue(alice("GET", f"/dev/projects/{pid}/jobs/{jid}/logs").json()["data"]["stdout"]["text"].startswith("hi from"))
        arts = alice("GET", f"/dev/projects/{pid}/jobs/{jid}/artifacts").json()["data"]["items"]
        self.assertEqual(alice("GET", f"/dev/projects/{pid}/artifacts/{arts[0]['id']}").json()["data"]["content"], "x")
        self.assertEqual(alice("GET", "/dev/overview").json()["data"]["counts"]["files"], 1)
        hist = alice("GET", f"/dev/projects/{pid}/history").json()["data"]["items"]
        self.assertIn("job.succeeded", [h["action"] for h in hist])
        # bob gets 404 for everything of alice's, exactly as for a missing id
        for method, path, body in (("GET", f"/dev/projects/{pid}/files", None), ("GET", f"/dev/projects/{pid}/jobs/{jid}", None), ("GET", f"/dev/projects/{pid}/jobs/{jid}/logs", None),
                                   ("GET", f"/dev/projects/{pid}/artifacts/{arts[0]['id']}", None), ("POST", f"/dev/projects/{pid}/jobs/{jid}/cancel", {}), ("GET", f"/dev/projects/{pid}/history", None)):
            self.assertEqual(self.error_code(bob(method, path, body), 404), "NOT_FOUND", path)

    def test_secret_values_never_appear_in_any_response(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        made = alice("POST", f"/dev/projects/{pid}/secrets", {"name": "MY_KEY", "value": "very-secret-123456"})
        self.assertEqual(made.status_code, 201, made.text)
        alice("GET", f"/dev/projects/{pid}/secrets")
        self.assertFalse(any("very-secret-123456" in b for b in self.api.bodies))

    def test_validation_errors_use_the_standard_envelope(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        self.assertEqual(self.error_code(alice("PUT", f"/dev/projects/{pid}/files/content", {"path": "../x.py", "content": "x"}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", f"/dev/projects/{pid}/jobs", {"kind": "shell"}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", f"/dev/projects/{pid}/pipeline-runs/none/deploy"), 404), "NOT_FOUND")

    def test_sessions_are_reported_unavailable_with_a_reason_when_jupyter_is_not_installed(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        status = alice("GET", f"/dev/projects/{pid}/sessions").json()["data"]["availability"]
        if not status["notebook"]["available"]:
            self.assertIn("not installed", status["notebook"]["reason"])
            self.assertEqual(self.error_code(alice("POST", f"/dev/projects/{pid}/sessions", {"kind": "notebook"}), 409), "SESSION_UNAVAILABLE")

    def test_notebook_export_formats_and_download_headers(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        alice("POST", f"/dev/projects/{pid}/notebooks", {"path": "n.ipynb", "title": "T"})
        for fmt in ("ipynb", "py", "html"):
            self.assertEqual(alice("POST", f"/dev/projects/{pid}/notebooks/export", {"path": "n.ipynb", "format": fmt}).status_code, 200)
        self.assertEqual(self.error_code(alice("POST", f"/dev/projects/{pid}/notebooks/export", {"path": "n.ipynb", "format": "pdf"}), 501), "EXPORT_UNAVAILABLE")
        dl = alice("POST", f"/dev/projects/{pid}/notebooks/export", {"path": "n.ipynb", "format": "html"}, params={"download": "true"})
        self.assertEqual(dl.headers["content-type"].split(";")[0], "text/html")
        self.assertEqual(dl.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", dl.headers["content-disposition"])

    def test_owner_overrides_need_the_owner_a_reason_and_are_visible_in_effective_limits(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        uid = self.api.h.conn.execute("SELECT id FROM users WHERE email_normalized = 'alice@example.com'").fetchone()[0]
        self.assertEqual(self.error_code(alice("PUT", "/dev/owner/compute-overrides", {"scope": "user", "scope_id": uid, "key": "job_wall_seconds", "value": 9999, "reason": "please"}), 403), "FORBIDDEN")
        self.api.owner_signed_in("owner2@example.com")
        self.assertEqual(self.error_code(self.api.call("PUT", "/dev/owner/compute-overrides", {"scope": "user", "scope_id": uid, "key": "job_wall_seconds", "value": 120, "reason": "no"}), 422), "VALIDATION_ERROR")
        ok = self.api.call("PUT", "/dev/owner/compute-overrides", {"scope": "user", "scope_id": uid, "key": "job_wall_seconds", "value": 120, "reason": "research run needs longer"})
        self.assertEqual(ok.status_code, 200, ok.text)
        limits = alice("GET", f"/dev/projects/{pid}/compute/limits").json()["data"]
        self.assertIn("120", json.dumps(limits))
        self.assertTrue(self.api.h.conn.execute("SELECT 1 FROM audit_events WHERE event_type LIKE '%compute%' OR event_type LIKE '%dev%' OR event_type LIKE '%limit%'").fetchone()
                        or self.api.h.conn.execute("SELECT 1 FROM dv_quota_overrides WHERE reason != ''").fetchone())

    def test_capabilities_say_plainly_what_is_unavailable(self):
        alice = self.user("alice@example.com")
        caps = alice("GET", "/dev/compute/capabilities").json()["data"]
        self.assertFalse(caps["gpu"]["available"])
        for key in ("pause", "resize", "gpu"):
            self.assertTrue(caps["capabilities"][key].startswith("unavailable"), key)

    def test_webhook_route_needs_no_session_but_needs_a_valid_signature(self):
        alice = self.user("alice@example.com")
        pid = self.project(alice)
        remote = os.path.join(self.tmp.name, "r.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
        c = alice("POST", f"/dev/projects/{pid}/git/connect", {"provider": "local", "remote_url": remote, "consent": True, "webhook_secret": "hook-secret-abc"})
        self.assertEqual(c.status_code, 201, c.text)
        repo = self.api.h.conn.execute("SELECT id FROM dv_repos").fetchone()[0]
        body = b'{"ref":"refs/heads/main"}'
        sig = "sha256=" + hmac.new(b"hook-secret-abc", body, hashlib.sha256).hexdigest()
        anon = self.api.browser()
        good = self.api.call("POST", f"/dev/webhooks/git/{repo}", content=body, client=anon, csrf=False, origin=None, headers={"X-Hub-Signature-256": sig, "X-GitHub-Delivery": "delivery-abcdef", "X-GitHub-Event": "push", "Content-Type": "application/json"})
        self.assertEqual(good.status_code, 200, good.text)
        replay = self.api.call("POST", f"/dev/webhooks/git/{repo}", content=body, client=anon, csrf=False, origin=None, headers={"X-Hub-Signature-256": sig, "X-GitHub-Delivery": "delivery-abcdef", "X-GitHub-Event": "push"})
        self.assertEqual(self.error_code(replay, 409), "WEBHOOK_REPLAY")
        for headers in ({}, {"X-Hub-Signature-256": "sha256=" + "0" * 64, "X-GitHub-Delivery": "delivery-zzzzzz"}):
            self.assertEqual(self.error_code(self.api.call("POST", f"/dev/webhooks/git/{repo}", content=body, client=anon, csrf=False, origin=None, headers=headers), 401), "WEBHOOK_REJECTED")
        self.assertEqual(self.error_code(self.api.call("POST", "/dev/webhooks/git/unknown", content=body, client=anon, csrf=False, origin=None, headers={"X-Hub-Signature-256": sig, "X-GitHub-Delivery": "delivery-abcdeg"}), 401), "WEBHOOK_REJECTED")


if __name__ == "__main__":
    unittest.main()
