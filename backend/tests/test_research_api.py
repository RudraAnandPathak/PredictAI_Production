"""HTTP tests for the research API: authentication, CSRF, the standard envelope, the whole session -> plan -> approval -> run -> bundle -> citation -> note -> export -> compare -> deletion flow,
validation, and 404-only isolation between people (FastAPI TestClient; needs fastapi and httpx). The search service and the pages are a local stand-in; nothing reaches the internet."""
import hashlib
import json
import time
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from fixtures import pdf
from research_support import RIVER_DOC, serp
from support import GOOD_PASSWORD
from web_support import FakeWeb, page


def browser(api, email):
    client = api.browser()
    api.register(email)
    response = api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
    assert response.status_code == 200, response.text
    token = response.json()["data"]["csrf_token"]

    def call(method, path, json=None, params=None, headers=None, content=None):
        merged = {"X-CSRF-Token": token, **(headers or {})}
        return api.call(method, path, json=json, params=params, csrf=False, client=client, headers=merged, content=content)

    return call


def upload(call, pid, name, data):
    started = call("POST", f"/projects/{pid}/uploads", {"filename": name, "size": len(data), "checksum_sha256": hashlib.sha256(data).hexdigest()})
    info = started.json()["data"]["upload"]
    for i in range(info["total_chunks"]):
        call("POST", f"/uploads/{info['id']}/chunks/{i}", content=data[i * info["chunk_size"]:(i + 1) * info["chunk_size"]], headers={"Content-Type": "application/octet-stream"})
    done = call("POST", f"/uploads/{info['id']}/complete")
    assert done.status_code == 200, done.text
    return done.json()["data"]["asset"]["id"]


def wait_job(call, job_id, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = call("GET", f"/research/jobs/{job_id}").json()["data"]
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("research did not finish")


class ResearchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = FakeWeb()
        cls.web.__enter__()
        cls.web.html("/a1", page("Flood report", ["The river rose by 2.4 metres on Tuesday after heavy rainfall according to the regional authority.", "Residents were told to avoid the low lying areas near the bridge."],
                                 published="2026-10-20T08:00:00Z"))
        serp(cls.web, [{"title": "Flood report", "url": cls.web.base + "/a1", "content": "river rose"}])
        cls.api = base.Api(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, research_web_enabled=True, research_allow_private_network=True, searxng_url=cls.web.base, research_fetch_timeout_s=5)
        alice, bob = browser(cls.api, "alice@example.com"), browser(cls.api, "bob@example.com")
        cls.alice, cls.bob = staticmethod(alice), staticmethod(bob)
        cls.pid = alice("POST", "/projects", {"name": "Floods"}).json()["data"]["id"]
        cls.doc = upload(alice, cls.pid, "river.pdf", pdf(RIVER_DOC))

    @classmethod
    def tearDownClass(cls):
        runner = getattr(cls.api.app.state, "research", None)
        if runner is not None:
            runner.runner.shutdown()
        cls.api.close()
        cls.web.__exit__()

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertTrue(body["meta"]["request_id"])
        return body["data"]

    def err(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {"error"})
        return response.json()["error"]["code"]

    def session(self, question="How high did the river rise after the heavy rainfall?", **options):
        return self.ok(self.alice("POST", f"/projects/{self.pid}/research/sessions", {"question": question, "options": options}), 201)

    def run_local(self, question="How high did the river rise after the heavy rainfall?"):
        s = self.session(question)
        job = self.ok(self.alice("POST", f"/research/sessions/{s['id']}/run", {}), 202)
        done = wait_job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done)
        return s["id"], done["result_id"]

    # ---------------------------------------------------------------- access
    def test_anonymous_callers_are_refused_and_state_changes_need_the_csrf_token(self):
        anon = self.api.browser()
        for method, path in [("GET", "/research/settings"), ("GET", f"/projects/{self.pid}/research/sessions"), ("POST", f"/projects/{self.pid}/research/sessions"),
                             ("GET", "/research/bundles/x"), ("GET", "/ai/providers")]:
            r = self.api.call(method, path, json={} if method == "POST" else None, client=anon, csrf=False)
            self.assertIn(r.status_code, (401, 403), path)
        no_token = self.api.browser()
        self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=no_token)
        for method, path in [("POST", f"/projects/{self.pid}/research/sessions"), ("PATCH", "/research/settings"), ("DELETE", f"/projects/{self.pid}/research/cache")]:
            r = self.api.call(method, path, json={}, client=no_token, csrf=False)
            self.assertEqual(r.status_code, 403, path)

    # ---------------------------------------------------------------- the whole local flow
    def test_a_research_session_from_question_to_evidence_to_export_and_deletion(self):
        caps = self.ok(self.alice("GET", f"/projects/{self.pid}/research/capabilities"))
        self.assertFalse(caps["web"]["allowed"])
        s = self.session()
        self.assertEqual((s["status"], s["needs_approval"], s["web"]["will_send"]), ("draft", False, []))
        self.assertTrue(s["explanation"]["summary"])
        sid = s["id"]
        job = self.ok(self.alice("POST", f"/research/sessions/{sid}/run", {}), 202)
        done = wait_job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed")
        bundle_id = done["result_id"]
        got = self.ok(self.alice("GET", f"/research/bundles/{bundle_id}"))
        self.assertTrue(got["integrity_ok"])
        b = got["bundle"]
        self.assertIn("2.4 metres", " ".join(e["quote"] for e in b["evidence"]))
        text = json.dumps(got)
        for internal in ("text_key", "bundle_key", "/r/"):
            self.assertNotIn(internal, text)
        self.assertTrue(got["explanation"]["points"])
        cite = self.ok(self.alice("GET", f"/research/bundles/{bundle_id}/evidence/{b['evidence'][0]['id']}"))
        self.assertTrue(cite["verification"]["verified"])
        self.assertEqual(self.err(self.alice("GET", f"/research/bundles/{bundle_id}/evidence/E999"), 404), "NOT_FOUND")
        summary = self.ok(self.alice("GET", f"/research/bundles/{bundle_id}/summary"))
        self.assertEqual(summary["schema"], "predictai.evidence_summary/1")
        # notes
        note = self.ok(self.alice("POST", f"/research/sessions/{sid}/notes", {"body": "Ask the council about the levee"}), 201)
        self.assertEqual(note["origin"], "written_by_you")
        self.ok(self.alice("PATCH", f"/research/notes/{note['id']}", {"body": "Ask about drainage"}))
        self.assertEqual(len(self.ok(self.alice("GET", f"/projects/{self.pid}/research/search", params={"q": "drainage"}))["items"]), 1)
        self.assertEqual(len(self.ok(self.alice("GET", f"/research/sessions/{sid}/notes"))["items"]), 1)
        # history, sources, jobs
        self.assertIn(sid, [x["id"] for x in self.ok(self.alice("GET", f"/projects/{self.pid}/research/sessions"))["items"]])
        sources = self.ok(self.alice("GET", f"/projects/{self.pid}/research/sources"))["items"]
        self.assertTrue(sources)
        self.assertTrue(self.ok(self.alice("GET", f"/research/sources/{sources[0]['id']}"))["cited_passages"])
        self.ok(self.alice("PATCH", f"/research/sources/{sources[0]['id']}", {"saved": True}))
        self.assertTrue(self.ok(self.alice("GET", f"/projects/{self.pid}/research/jobs"))["items"])
        # exports
        for fmt, ctype, ext in (("json", "application/json", "json"), ("markdown", "text/markdown", "md"), ("csv", "text/csv", "csv")):
            r = self.alice("GET", f"/research/bundles/{bundle_id}/export", params={"format": fmt})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.headers["content-type"].startswith(ctype), r.headers["content-type"])
            self.assertIn(f".{ext}\"", r.headers["content-disposition"])
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.err(self.alice("GET", f"/research/bundles/{bundle_id}/export", params={"format": "exe"}), 422), "VALIDATION_ERROR")
        # a second run and the comparison
        job2 = self.ok(self.alice("POST", f"/research/sessions/{sid}/run", {}), 202)
        second = wait_job(self.alice, job2["id"])["result_id"]
        cmp = self.ok(self.alice("GET", "/research/compare", params={"a": bundle_id, "b": second}))
        self.assertIn("does not say which is more correct", cmp["note"])
        self.assertTrue(cmp["explanation"]["summary"])
        # save, rename, delete
        self.ok(self.alice("PATCH", f"/research/bundles/{second}", {"saved": True, "label": "after the second look"}))
        self.ok(self.alice("PATCH", f"/research/sessions/{sid}", {"title": "River heights", "saved": True}))
        self.ok(self.alice("DELETE", f"/research/bundles/{second}"))
        self.assertEqual(self.err(self.alice("GET", f"/research/bundles/{second}"), 404), "NOT_FOUND")
        self.ok(self.alice("DELETE", f"/research/sessions/{sid}"))
        self.assertEqual(self.err(self.alice("GET", f"/research/sessions/{sid}"), 404), "NOT_FOUND")
        self.assertEqual(self.err(self.alice("GET", f"/research/bundles/{bundle_id}"), 404), "NOT_FOUND")

    # ---------------------------------------------------------------- consent and web
    def test_web_research_needs_consent_then_approval_and_only_the_approved_searches_are_sent(self):
        s = self.session(web=True, providers=["searxng"])
        self.assertTrue(s["needs_approval"])
        self.assertTrue(s["web"]["will_send"])
        self.assertEqual(self.err(self.alice("POST", f"/research/sessions/{s['id']}/approve", {"plan_hash": s["plan_hash"]}), 403), "WEB_NOT_ALLOWED")
        self.assertEqual(self.web.hits, [])
        self.ok(self.alice("PATCH", "/research/settings", {"web_enabled": True, "providers": ["searxng"]}))
        self.addCleanup(lambda: self.alice("PATCH", "/research/settings", {"web_enabled": False}))
        self.assertEqual(self.err(self.alice("POST", f"/research/sessions/{s['id']}/run", {}), 409), "PLAN_NOT_APPROVED")
        self.assertEqual(self.err(self.alice("POST", f"/research/sessions/{s['id']}/approve", {"plan_hash": "0" * 64}), 409), "PLAN_CHANGED")
        self.assertEqual(self.web.hits, [])
        self.ok(self.alice("POST", f"/research/sessions/{s['id']}/approve", {"plan_hash": s["plan_hash"]}))
        job = self.ok(self.alice("POST", f"/research/sessions/{s['id']}/run", {}), 202)
        done = wait_job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed")
        b = self.ok(self.alice("GET", f"/research/bundles/{done['result_id']}"))["bundle"]
        self.assertTrue(b["web"]["used"])
        self.assertEqual({e["origin"] for e in b["evidence"]}, {"user_provided", "external_research"})
        sent = " ".join(self.web.hits).lower()
        for private in ("evacuated", "levee", "central+bank", "1,200"):
            self.assertNotIn(private, sent)

    def test_settings_validation_and_project_narrowing_over_http(self):
        for body in ({}, {"web_enabled": "yes"}, {"providers": ["bing"]}, {"freshness_days": 0}, {"unknown": True}):
            self.assertIn(self.alice("PATCH", "/research/settings", body).status_code, (422,), body)
        got = self.ok(self.alice("GET", "/research/settings", params={"project_id": self.pid}))
        self.assertFalse(got["effective"]["web_allowed"])

    # ---------------------------------------------------------------- validation
    def test_invalid_input_is_a_validation_error_that_does_not_echo_the_values(self):
        for body in ({"question": "hi"}, {"question": "What is a river delta?", "level": "huge"}, {"question": "What is a river delta?", "options": {"providers": ["bing"]}},
                     {"question": "x" * 2500}, {}):
            r = self.alice("POST", f"/projects/{self.pid}/research/sessions", body)
            self.assertEqual(self.err(r, 422), "VALIDATION_ERROR", body)
        r = self.alice("POST", f"/projects/{self.pid}/research/sessions", {"question": "hi", "level": "SECRET-LEVEL-VALUE"})
        self.assertNotIn("SECRET-LEVEL-VALUE", r.text)

    # ---------------------------------------------------------------- isolation
    def test_other_people_see_only_404s_with_the_same_wording_as_for_ids_that_do_not_exist(self):
        sid, bundle_id = self.run_local("What happened to the river level after rainfall?")
        job_id = self.ok(self.alice("GET", f"/projects/{self.pid}/research/jobs"))["items"][0]["id"]
        note_id = self.ok(self.alice("POST", f"/research/sessions/{sid}/notes", {"body": "mine"}), 201)["id"]
        mine = [("GET", f"/research/sessions/{sid}"), ("PATCH", f"/research/sessions/{sid}"), ("DELETE", f"/research/sessions/{sid}"), ("POST", f"/research/sessions/{sid}/approve"),
                ("POST", f"/research/sessions/{sid}/run"), ("GET", f"/research/sessions/{sid}/notes"), ("POST", f"/research/sessions/{sid}/notes"), ("PATCH", f"/research/notes/{note_id}"),
                ("DELETE", f"/research/notes/{note_id}"), ("GET", f"/research/jobs/{job_id}"), ("POST", f"/research/jobs/{job_id}/cancel"), ("GET", f"/research/bundles/{bundle_id}"),
                ("PATCH", f"/research/bundles/{bundle_id}"), ("DELETE", f"/research/bundles/{bundle_id}"), ("GET", f"/research/bundles/{bundle_id}/summary"),
                ("GET", f"/research/bundles/{bundle_id}/evidence/E1"), ("GET", f"/research/bundles/{bundle_id}/export"), ("GET", f"/projects/{self.pid}/research/sessions"),
                ("POST", f"/projects/{self.pid}/research/sessions"), ("GET", f"/projects/{self.pid}/research/sources"), ("GET", f"/projects/{self.pid}/research/capabilities"),
                ("GET", f"/projects/{self.pid}/research/bundles"), ("GET", f"/projects/{self.pid}/research/jobs"), ("GET", f"/projects/{self.pid}/research/cache")]
        for method, path in mine:
            body = {"question": "What is a river delta?", "body": "x", "plan_hash": "a" * 64, "title": "x", "saved": True} if method != "GET" else None
            theirs = self.bob(method, path, body)
            gone = self.bob(method, path.replace(self.pid, "0" * 32).replace(sid, "1" * 32).replace(bundle_id, "2" * 32).replace(job_id, "3" * 32).replace(note_id, "4" * 32), body)
            self.assertEqual((theirs.status_code, theirs.json()["error"]["code"]), (404, "NOT_FOUND"), path)
            self.assertEqual(theirs.json()["error"]["message"], gone.json()["error"]["message"], path)
        self.assertEqual(self.bob("GET", f"/projects/{self.pid}/research/search", params={"q": "river"}).status_code, 404)
        self.assertEqual(self.alice("GET", f"/research/sessions/{sid}").status_code, 200)          # nothing above changed Alice's research

    def test_responses_never_carry_secrets_or_server_paths(self):
        self.run_local("What did the report say about the river level?")
        blob = "\n".join(self.api.bodies[-60:])
        for needle in ("PREDICTAI_", "sk-", "Traceback", "/mnt/", "/home/claude", "data/storage"):
            self.assertNotIn(needle, blob)


class ProvidersApi(unittest.TestCase):
    def setUp(self):
        self.api = base.Api(ai_allow_paid=False)
        self.addCleanup(self.api.close)
        self.api.owner_signed_in("owner@example.com")
        self.owner = self.api.call
        self.user = browser(self.api, "user@example.com")

    def test_only_the_owner_configures_providers_and_secrets_are_never_returned(self):
        spec = {"key": "local-llm", "kind": "ollama", "label": "My Ollama", "base_url": "http://127.0.0.1:11434", "model": "llama3.2"}
        self.assertEqual(self.user("POST", "/ai/providers", spec).status_code, 403)
        made = self.owner("POST", "/ai/providers", spec)
        self.assertEqual(made.status_code, 201, made.text)
        self.assertFalse(made.json()["data"]["enabled"])                                             # new providers start off
        self.assertEqual(self.user("PATCH", "/ai/providers/local-llm", {"label": "x"}).status_code, 403)
        listed = self.user("GET", "/ai/providers")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn("secret", json.dumps(listed.json()).lower().replace("secret_env", "").replace("has_secret", ""))
        paid = self.owner("POST", "/ai/providers", {**spec, "key": "paid-one", "paid": True})
        self.assertEqual(paid.status_code, 201, paid.text)
        blocked = self.owner("POST", "/ai/providers/paid-one/enabled", {"enabled": True, "confirm_paid": True})
        self.assertEqual((blocked.status_code, blocked.json()["error"]["code"]), (409, "PAID_NOT_ALLOWED"))                                    # the platform flag is off


if __name__ == "__main__":
    unittest.main()
