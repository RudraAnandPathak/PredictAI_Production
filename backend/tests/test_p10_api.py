"""Phase 10 over HTTP: session + CSRF on every route, isolation, the upload/reference/ask flow with a real worker, limits, and the owner usage view."""
import json
import os
import tempfile
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from app.modules.research_intelligence.workers.worker import Worker
from p10_support import ARTICLE, make_pdf
from support import GOOD_PASSWORD

P = "/research-intelligence"


class RiApi(base.ApiBase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = base.Api(ri_root=os.path.join(self.tmp.name, "ri"))
        self.addCleanup(self.api.close)
        self.owner_p, _ = self.api.h.make_owner()

    def user(self, email, owner=False):
        client = self.api.browser()
        self.api.register(email)
        resp = self.api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
        self.assertEqual(resp.status_code, 200)
        token = resp.json()["data"]["csrf_token"]

        def call(method, path, json=None, params=None, headers=None, content=None):
            return self.api.call(method, path, json=json, params=params, csrf=False, client=client, headers={"X-CSRF-Token": token, **(headers or {})}, content=content)
        return call

    def drain(self):
        h = self.api.h
        w = Worker(h.db, h.settings, h.clock, h.hasher, h.outbox, poll=0.01)
        out = []
        while True:
            r = w.tick()
            if r is None:
                return out
            out.append(r)

    def workspace(self, call, name="Study"):
        r = call("POST", f"{P}/workspaces", {"name": name})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id"]

    def upload(self, call, wid, name, data):
        return call("POST", f"{P}/workspaces/{wid}/sources/upload", params={"filename": name}, content=data, headers={"Content-Type": "application/octet-stream"})


class Gate(RiApi):
    def test_every_route_needs_a_session_and_changes_need_csrf(self):
        routes = [("GET", "/limits"), ("GET", "/workspaces"), ("POST", "/workspaces"), ("GET", "/workspaces/x"), ("PATCH", "/workspaces/x"), ("DELETE", "/workspaces/x"), ("GET", "/workspaces/x/settings"),
                  ("PUT", "/workspaces/x/settings"), ("GET", "/workspaces/x/sources"), ("POST", "/workspaces/x/sources/upload"), ("POST", "/workspaces/x/sources/reference"),
                  ("GET", "/workspaces/x/sources/y"), ("PATCH", "/workspaces/x/sources/y"), ("DELETE", "/workspaces/x/sources/y"), ("POST", "/workspaces/x/sources/y/reindex"),
                  ("GET", "/workspaces/x/sources/y/chunks"), ("GET", "/workspaces/x/passages/z"), ("GET", "/workspaces/x/jobs"), ("POST", "/jobs/j/cancel"), ("POST", "/jobs/j/retry"),
                  ("POST", "/workspaces/x/search"), ("GET", "/workspaces/x/conversations"), ("POST", "/workspaces/x/conversations"), ("GET", "/workspaces/x/conversations/c"),
                  ("PATCH", "/workspaces/x/conversations/c"), ("DELETE", "/workspaces/x/conversations/c"), ("PUT", "/workspaces/x/conversations/c/scope"), ("POST", "/workspaces/x/ask"),
                  ("GET", "/workspaces/x/notes"), ("POST", "/workspaces/x/notes"), ("PATCH", "/workspaces/x/notes/n"), ("DELETE", "/workspaces/x/notes/n"), ("GET", "/workspaces/x/findings"),
                  ("POST", "/workspaces/x/findings"), ("DELETE", "/workspaces/x/findings/f"), ("GET", "/owner/usage"),
                  ("GET", "/discovery/providers"), ("POST", "/discovery/search"), ("GET", "/discovery/searches"), ("DELETE", "/discovery/searches"), ("GET", "/discovery/searches/s"),
                  ("DELETE", "/discovery/searches/s"), ("POST", "/discovery/library"), ("GET", "/discovery/library"), ("GET", "/discovery/library/l"), ("DELETE", "/discovery/library/l"),
                  ("POST", "/discovery/compare"), ("POST", "/workspaces/x/discovery/import"),
                  ("POST", "/workspaces/x/analyses"), ("GET", "/workspaces/x/analyses"), ("GET", "/workspaces/x/analyses/a"), ("POST", "/workspaces/x/analyses/a/cancel"), ("DELETE", "/workspaces/x/analyses/a"),
                  ("GET", "/home"), ("GET", "/workspaces/x/activity"), ("PATCH", "/workspaces/x/findings/f"), ("GET", "/workspaces/x/studio/evidence"), ("GET", "/workspaces/x/studio/links"),
                  ("POST", "/workspaces/x/studio/links"), ("DELETE", "/workspaces/x/studio/links/l"), ("GET", "/workspaces/x/studio/groups"), ("POST", "/workspaces/x/studio/groups"),
                  ("PATCH", "/workspaces/x/studio/groups/g"), ("PUT", "/workspaces/x/studio/groups/g/items"), ("DELETE", "/workspaces/x/studio/groups/g"), ("GET", "/workspaces/x/studio/outlines"),
                  ("POST", "/workspaces/x/studio/outlines"), ("GET", "/workspaces/x/studio/outlines/o"), ("PATCH", "/workspaces/x/studio/outlines/o"), ("DELETE", "/workspaces/x/studio/outlines/o"),
                  ("POST", "/workspaces/x/studio/generate"), ("GET", "/workspaces/x/studio/generated"), ("GET", "/workspaces/x/studio/generated/g"), ("DELETE", "/workspaces/x/studio/generated/g"),
                  ("GET", "/report-types"), ("GET", "/reports"), ("GET", "/workspaces/x/reports"), ("POST", "/workspaces/x/reports"), ("GET", "/workspaces/x/reports/r"), ("PATCH", "/workspaces/x/reports/r"),
                  ("DELETE", "/workspaces/x/reports/r"), ("POST", "/workspaces/x/reports/r/regenerate"), ("GET", "/workspaces/x/reports/r/versions"), ("POST", "/workspaces/x/reports/r/versions/1/restore"),
                  ("GET", "/workspaces/x/reports/r/export")]
        for method, path in routes:
            r = self.api.call(method, P + path, json={} if method in ("POST", "PATCH", "PUT") else None, csrf=False)
            self.assertEqual(r.status_code, 401, f"{method} {path}")
        self.api.signed_in()
        for method, path in (("POST", "/workspaces"), ("POST", "/workspaces/x/ask"), ("DELETE", "/workspaces/x/sources/y"), ("PUT", "/workspaces/x/settings")):
            self.assertEqual(self.error_code(self.api.call(method, P + path, {"name": "x"}, csrf=False), 403), "CSRF_FAILED", f"{method} {path}")

    def test_the_owner_usage_route_is_owner_only_and_counts_only(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        self.upload(alice, wid, "a.txt", ARTICLE.encode())
        self.drain()
        self.assertEqual(self.error_code(alice("GET", f"{P}/owner/usage"), 403), "FORBIDDEN")
        client = self.api.browser()
        login = self.api.call("POST", "/auth/login", {"email": "owner@example.com", "password": GOOD_PASSWORD}, client=client)
        self.assertEqual(login.status_code, 200, login.text)
        r = self.api.call("GET", f"{P}/owner/usage", client=client)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual((data["sources"], data["chunks"] > 0, data["vector_backend"]), ({"ready": 1}, True, "sqlite"))
        self.assertNotIn("Urban green", r.text)
        self.assertNotIn("heat", json.dumps(data).lower().replace("heat_", ""))


class Flow(RiApi):
    def test_upload_process_search_ask_cite_and_delete_over_http(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        lim = alice("GET", f"{P}/limits").json()["data"]
        self.assertEqual((lim["tier"], lim["max_upload_mb"], lim["imports_per_day"]), ("standard", 10, 40))
        up = self.upload(alice, wid, "heat study.txt", ARTICLE.encode())
        self.assertEqual(up.status_code, 201, up.text)
        sid = up.json()["data"]["id"]
        self.assertEqual(up.json()["data"]["status"], "queued")
        ov = alice("GET", f"{P}/workspaces/{wid}").json()["data"]
        self.assertEqual(ov["sources_by_status"], {"queued": 1})
        self.assertIn("worker", ov["worker_note"])
        self.assertEqual(self.drain(), ["succeeded"])
        d = alice("GET", f"{P}/workspaces/{wid}/sources/{sid}").json()["data"]
        self.assertEqual((d["status"], d["embedding_kind"]), ("ready", "lexical"))
        found = alice("POST", f"{P}/workspaces/{wid}/search", {"query": "tree canopy temperature", "mode": "hybrid"}).json()["data"]
        self.assertTrue(found["items"])
        ask = alice("POST", f"{P}/workspaces/{wid}/ask", {"question": "How much do trees lower surface temperature?"})
        self.assertEqual(ask.status_code, 200, ask.text)
        msg = ask.json()["data"]["message"]
        self.assertEqual(msg["answer"]["mode"], "extractive")
        cite = msg["citations"][0]
        passage = alice("GET", f"{P}/workspaces/{wid}/passages/{cite['chunk_id']}").json()["data"]
        self.assertIn(cite["quote"], passage["passage"]["text"])
        cid = ask.json()["data"]["conversation"]["id"]
        self.assertEqual(len(alice("GET", f"{P}/workspaces/{wid}/conversations/{cid}").json()["data"]["messages"]), 2)
        self.assertEqual(alice("DELETE", f"{P}/workspaces/{wid}/sources/{sid}").json()["data"]["deleted"], True)
        self.assertEqual(alice("GET", f"{P}/workspaces/{wid}/sources").json()["data"]["items"], [])
        self.assertEqual(alice("DELETE", f"{P}/workspaces/{wid}").json()["data"]["deleted"], True)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "ri")), [])

    def test_reference_import_policy_notes_and_findings(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        bad = alice("POST", f"{P}/workspaces/{wid}/sources/reference", {"reference": "http://169.254.169.254/latest"})
        self.assertEqual(self.error_code(bad, 422), "VALIDATION_ERROR")
        ok = alice("POST", f"{P}/workspaces/{wid}/sources/reference", {"reference": "10.1000/cool"})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual((ok.json()["data"]["origin"], ok.json()["data"]["doi"]), ("doi", "10.1000/cool"))
        self.assertEqual(self.error_code(alice("PUT", f"{P}/workspaces/{wid}/settings", {"ai_data_policy": "external_allowed"}), 422), "VALIDATION_ERROR")
        s = alice("PUT", f"{P}/workspaces/{wid}/settings", {"ai_data_policy": "external_allowed", "confirm": True}).json()["data"]
        self.assertEqual(s["ai_data_policy"], "external_allowed")
        note = alice("POST", f"{P}/workspaces/{wid}/notes", {"body": "remember"})
        self.assertEqual(note.status_code, 201)
        self.assertEqual(len(alice("GET", f"{P}/workspaces/{wid}/notes").json()["data"]["items"]), 1)

    def test_upload_size_is_capped_while_streaming(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        r = self.upload(alice, wid, "big.txt", b"x" * (10 * 1024 * 1024 + 10))
        self.assertEqual(self.error_code(r, 413), "FILE_TOO_LARGE")
        r = self.upload(alice, wid, "a.exe", b"MZ")
        self.assertEqual(self.error_code(r, 415), "UNSUPPORTED_FILE")


class Isolation(RiApi):
    def test_another_account_gets_not_found_for_everything(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        wid = self.workspace(alice)
        sid = self.upload(alice, wid, "a.txt", ARTICLE.encode()).json()["data"]["id"]
        self.drain()
        ask = alice("POST", f"{P}/workspaces/{wid}/ask", {"question": "How much do trees lower surface temperature?"}).json()["data"]
        cid, chunk = ask["conversation"]["id"], ask["message"]["citations"][0]["chunk_id"]
        job = alice("GET", f"{P}/workspaces/{wid}/jobs").json()["data"]["items"][0]["id"]
        probes = [("GET", f"/workspaces/{wid}", None), ("PATCH", f"/workspaces/{wid}", {"name": "x"}), ("DELETE", f"/workspaces/{wid}", None), ("GET", f"/workspaces/{wid}/settings", None),
                  ("PUT", f"/workspaces/{wid}/settings", {"ai_data_policy": "external_allowed", "confirm": True}), ("GET", f"/workspaces/{wid}/sources", None),
                  ("POST", f"/workspaces/{wid}/sources/reference", {"reference": "10.1000/x"}), ("GET", f"/workspaces/{wid}/sources/{sid}", None),
                  ("PATCH", f"/workspaces/{wid}/sources/{sid}", {"included": False}), ("DELETE", f"/workspaces/{wid}/sources/{sid}", None), ("POST", f"/workspaces/{wid}/sources/{sid}/reindex", None),
                  ("GET", f"/workspaces/{wid}/sources/{sid}/chunks", None), ("GET", f"/workspaces/{wid}/passages/{chunk}", None), ("GET", f"/workspaces/{wid}/jobs", None),
                  ("POST", f"/jobs/{job}/cancel", None), ("POST", f"/jobs/{job}/retry", None), ("POST", f"/workspaces/{wid}/search", {"query": "trees"}),
                  ("GET", f"/workspaces/{wid}/conversations", None), ("GET", f"/workspaces/{wid}/conversations/{cid}", None), ("DELETE", f"/workspaces/{wid}/conversations/{cid}", None),
                  ("POST", f"/workspaces/{wid}/ask", {"question": "trees?"}), ("GET", f"/workspaces/{wid}/notes", None), ("POST", f"/workspaces/{wid}/notes", {"body": "x"}),
                  ("GET", f"/workspaces/{wid}/findings", None), ("POST", f"/workspaces/{wid}/findings", {"source_id": sid, "text": "x"})]
        for method, path, body in probes:
            r = bob(method, P + path, body)
            self.assertEqual(self.error_code(r, 404), "NOT_FOUND", f"{method} {path}")
            self.assertNotIn("Urban green", r.text)
        up = self.upload(bob, wid, "b.txt", b"hello there my friend")
        self.assertEqual(self.error_code(up, 404), "NOT_FOUND")
        self.assertEqual(bob("GET", f"{P}/workspaces").json()["data"]["items"], [])
        self.assertEqual(alice("GET", f"{P}/workspaces/{wid}/sources/{sid}").json()["data"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
