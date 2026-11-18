"""HTTP tests for the endpoints the corrective work added: follow-up questions and the provider test / search / open endpoints (FastAPI TestClient; needs fastapi and httpx).
Login, CSRF, the standard envelope, validation, consent and 404-only isolation between people are checked over HTTP. Only the SearXNG-style provider is switched on, and it points at a local
stand-in, so these tests never contact Wikipedia or arXiv (their behaviour is tested in test_webaccess_wikipedia / test_webaccess_arxiv / test_research_combined with mocked API answers)."""
import json
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from fixtures import pdf
from research_support import RIVER_DOC, serp
from support import GOOD_PASSWORD
from test_research_api import browser, upload, wait_job
from web_support import FakeWeb, page


class CorrectiveApi(unittest.TestCase):
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
        return body["data"]

    def err(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {"error"})
        return response.json()["error"]["code"]

    def switch_web(self, on, providers=("searxng",)):
        self.ok(self.alice("PATCH", "/research/settings", {"web_enabled": on, **({"providers": list(providers)} if on else {})}))

    def finished_session(self, question="How high did the river rise after the heavy rainfall?"):
        s = self.ok(self.alice("POST", f"/projects/{self.pid}/research/sessions", {"question": question, "options": {"documents": [self.doc]}}), 201)
        job = self.ok(self.alice("POST", f"/research/sessions/{s['id']}/run", {}), 202)
        done = wait_job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done)
        return s["id"], done["result_id"]

    # ---------------------------------------------------------------- follow-ups
    def test_a_follow_up_builds_on_a_finished_question_and_not_on_one_that_has_not_run(self):
        fresh = self.ok(self.alice("POST", f"/projects/{self.pid}/research/sessions", {"question": "How high did the river rise after the heavy rainfall?", "options": {"documents": [self.doc]}}), 201)
        self.assertEqual(self.err(self.alice("POST", f"/research/sessions/{fresh['id']}/follow-up", {"question": "And how many people were evacuated?"}), 422), "VALIDATION_ERROR")
        sid, bundle_id = self.finished_session()
        f = self.ok(self.alice("POST", f"/research/sessions/{sid}/follow-up", {"question": "And how many residents were evacuated from the low lying areas?"}), 201)
        self.assertEqual((f["status"], f["parent"]["of"]), ("draft", sid))
        self.assertEqual(f["plan"]["local"]["documents"], [self.doc])                                 # it keeps the earlier question's files
        self.assertEqual([st["title"] for st in f["plan"]["subtopics"]], ["Answer"])                  # and is answered as asked
        self.assertFalse(f["needs_approval"])                                                         # web is off, so nothing to approve and nothing leaves
        job = self.ok(self.alice("POST", f"/research/sessions/{f['id']}/run", {}), 202)
        done = wait_job(self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done)
        b = self.ok(self.alice("GET", f"/research/bundles/{done['result_id']}"))
        self.assertIn("1,200", " ".join(e["quote"] for e in b["bundle"]["evidence"]))
        self.assertNotEqual(done["result_id"], bundle_id)

    def test_follow_up_input_is_checked_and_needs_login_csrf_and_ownership(self):
        sid, _ = self.finished_session()
        anon = self.api.browser()
        self.assertIn(self.api.call("POST", f"/research/sessions/{sid}/follow-up", json={"question": "More?"}, client=anon, csrf=False).status_code, (401, 403))
        no_token = self.api.browser()
        self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=no_token)
        self.assertEqual(self.api.call("POST", f"/research/sessions/{sid}/follow-up", json={"question": "What else was reported?"}, client=no_token, csrf=False).status_code, 403)
        for body in ({}, {"question": "x" * 2001}, {"question": 12}, {"question": "What else was reported?", "options": "web"}):
            self.assertEqual(self.alice("POST", f"/research/sessions/{sid}/follow-up", body).status_code, 422, body)
        secret = "ZXSECRETQUESTION" + "y" * 2100
        r = self.alice("POST", f"/research/sessions/{sid}/follow-up", {"question": secret})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("ZXSECRETQUESTION", r.text)                                                  # a rejected value is not echoed back
        mine = self.alice("POST", f"/research/sessions/{sid}/follow-up", {"question": "What else was reported?"})
        theirs = self.bob("POST", f"/research/sessions/{sid}/follow-up", {"question": "What else was reported?"})
        missing = self.bob("POST", "/research/sessions/doesnotexist/follow-up", {"question": "What else was reported?"})
        self.assertEqual(mine.status_code, 201)
        self.assertEqual((theirs.status_code, missing.status_code), (404, 404))
        self.assertEqual(theirs.json()["error"]["message"], missing.json()["error"]["message"])      # another person's session looks exactly like one that does not exist

    # ---------------------------------------------------------------- provider endpoints
    def test_provider_endpoints_need_login_csrf_and_the_web_switch(self):
        self.switch_web(False)
        base_path = f"/projects/{self.pid}/research/providers"
        calls = [("diagnose", {}), ("search", {"provider": "searxng", "query": "river flood"}), ("open", {"provider": "searxng", "ref": {"url": self.web.base + "/a1"}})]
        anon = self.api.browser()
        no_token = self.api.browser()
        self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": GOOD_PASSWORD}, client=no_token)
        before = len(self.web.hits)
        for name, body in calls:
            self.assertIn(self.api.call("POST", f"{base_path}/{name}", json=body, client=anon, csrf=False).status_code, (401, 403), name)
            self.assertEqual(self.api.call("POST", f"{base_path}/{name}", json=body, client=no_token, csrf=False).status_code, 403, name)
            self.assertEqual(self.err(self.alice("POST", f"{base_path}/{name}", body), 403), "WEB_NOT_ALLOWED", name)        # web research is off: refused before anything is contacted
        self.assertEqual(len(self.web.hits), before)

    def test_search_and_open_a_page_send_only_what_was_typed_and_refuse_anything_that_looks_private(self):
        self.switch_web(True)
        self.addCleanup(lambda: self.switch_web(False))
        base_path = f"/projects/{self.pid}/research/providers"
        n0 = len(self.web.hits)
        found = self.ok(self.alice("POST", f"{base_path}/search", {"provider": "searxng", "query": "river flood"}))
        self.assertEqual(found["provider"], "searxng")
        self.assertEqual(found["hits"][0]["title"], "Flood report")
        self.assertEqual(found["hits"][0]["source_label"], "General web page")
        sent = [h for h in self.web.hits[n0:] if h.startswith("/search")]
        self.assertEqual(len(sent), 1)
        self.assertIn("river+flood", sent[0].replace("%20", "+"))
        opened = self.ok(self.alice("POST", f"{base_path}/open", {"provider": "searxng", "ref": {"url": self.web.base + "/a1"}}))
        self.assertIn("2.4 metres", opened["sections"][0]["text"])
        self.assertIn("never instructions", opened["note"])
        n1 = len(self.web.hits)
        for q in ("my account number 1234567890123 balance", "email me at alice@example.com about floods"):
            self.assertEqual(self.err(self.alice("POST", f"{base_path}/search", {"provider": "searxng", "query": q}), 422), "VALIDATION_ERROR")
        self.assertEqual(len(self.web.hits), n1)                                                      # nothing private-looking was sent
        self.assertEqual(self.alice("POST", f"{base_path}/search", {"provider": "bing", "query": "river"}).status_code, 422)
        self.assertEqual(self.alice("POST", f"{base_path}/search", {"provider": "searxng", "query": "r"}).status_code, 422)
        self.assertEqual(self.alice("POST", f"{base_path}/search", {"provider": "searxng", "query": "river", "limit": 50}).status_code, 422)
        self.assertEqual(self.err(self.alice("POST", f"{base_path}/search", {"provider": "wikipedia", "query": "river"}), 403), "PROVIDER_NOT_ALLOWED")       # not switched on: not contacted
        self.assertEqual(self.err(self.alice("POST", f"{base_path}/open", {"provider": "arxiv", "ref": {"arxiv_id": "2101.00001"}}), 403), "PROVIDER_NOT_ALLOWED")
        self.assertEqual(self.alice("POST", f"{base_path}/open", {"provider": "searxng", "ref": {}}).status_code, 422)
        for bad in ("file:///etc/passwd", "ftp://example.com/a", "http://user:pw@example.com/a", "not a url"):
            r = self.alice("POST", f"{base_path}/open", {"provider": "searxng", "ref": {"url": bad}})
            self.assertEqual(r.status_code, 422, bad)                                                      # the person's own bad address is a 4xx, never a server fault
            self.assertNotIn("passwd", r.text)

    def test_another_person_cannot_use_my_project_through_the_provider_endpoints(self):
        self.switch_web(True)
        self.addCleanup(lambda: self.switch_web(False))
        base_path = f"/projects/{self.pid}/research/providers"
        gone = "/projects/doesnotexist/research/providers"
        for name, body in (("diagnose", {}), ("search", {"provider": "searxng", "query": "river flood"}), ("open", {"provider": "searxng", "ref": {"url": self.web.base + "/a1"}})):
            theirs = self.bob("POST", f"{base_path}/{name}", body)
            missing = self.bob("POST", f"{gone}/{name}", body)
            self.assertEqual((theirs.status_code, missing.status_code), (404, 404), name)
            self.assertEqual(theirs.json()["error"]["message"], missing.json()["error"]["message"])

    def test_the_diagnosis_contacts_only_the_providers_that_are_switched_on(self):
        self.switch_web(True)
        self.addCleanup(lambda: self.switch_web(False))
        n0 = len(self.web.hits)
        out = self.ok(self.alice("POST", f"/projects/{self.pid}/research/providers/diagnose", {}))
        by = {r["provider"]: r for r in out["results"]}
        self.assertEqual(set(by), {"wikipedia", "arxiv", "searxng"})
        self.assertEqual((by["wikipedia"]["status"], by["arxiv"]["status"]), ("not_allowed", "not_allowed"))
        self.assertEqual(by["searxng"]["status"], "ok")
        self.assertTrue(by["searxng"]["ok"])
        self.assertIn("None of your documents", out["note"])
        self.assertEqual([h for h in self.web.hits[n0:] if not h.startswith("/search")], [])           # only SearXNG's own test search was made
        one = self.ok(self.alice("POST", f"/projects/{self.pid}/research/providers/diagnose", {"provider": "searxng"}))
        self.assertEqual([r["provider"] for r in one["results"]], ["searxng"])
        self.assertEqual(self.alice("POST", f"/projects/{self.pid}/research/providers/diagnose", {"provider": "bing"}).status_code, 422)
        text = json.dumps(out)
        for secret in ("PREDICTAI_AI_KEY", "password", "alice@example.com"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
