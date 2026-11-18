"""HTTP-level tests for projects (FastAPI TestClient; needs fastapi and httpx)."""
import unittest

try:
    import test_identity_api as base  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

from support import GOOD_PASSWORD


class WorkspaceApiBase(base.ApiBase):
    def user(self, email):
        """A separate browser (cookie jar + CSRF) signed in as `email`."""
        client = self.api.browser()
        self.api.register(email)
        response = self.api.call("POST", "/auth/login", {"email": email, "password": GOOD_PASSWORD}, client=client)
        self.assertEqual(response.status_code, 200)
        token = response.json()["data"]["csrf_token"]

        def call(method, path, json=None, params=None, headers=None, content=None):
            merged = {"X-CSRF-Token": token, **(headers or {})}
            return self.api.call(method, path, json=json, params=params, csrf=False, client=client, headers=merged, content=content)

        return call


class ProjectApiTests(WorkspaceApiBase):
    def test_requires_a_session_and_csrf(self):
        self.assertEqual(self.error_code(self.api.call("GET", "/projects"), 401), "UNAUTHENTICATED")
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", "/projects", {"name": "x"}, csrf=False), 403), "CSRF_FAILED")
        self.assertEqual(self.error_code(self.api.call("POST", "/projects", {"name": "x"}, origin="https://evil.example"), 403), "CSRF_FAILED")

    def test_lifecycle_over_http(self):
        alice = self.user("alice@example.com")
        created = alice("POST", "/projects", {"name": "Thesis", "description": "chapter data", "metadata": {"tags": ["a"]}})
        self.assertEqual(created.status_code, 201)
        project = created.json()["data"]
        self.assertTrue(created.json()["meta"]["request_id"])
        pid = project["id"]
        self.assertEqual(alice("GET", "/projects").json()["data"]["total"], 1)
        self.assertEqual(alice("GET", "/projects", params={"q": "thes"}).json()["data"]["total"], 1)
        self.assertEqual(alice("GET", "/projects", params={"q": "zzz"}).json()["data"]["total"], 0)
        self.assertEqual(alice("PATCH", f"/projects/{pid}", {"name": "Thesis v2"}).json()["data"]["name"], "Thesis v2")
        self.assertEqual(alice("POST", f"/projects/{pid}/archive").json()["data"]["status"], "archived")
        self.assertEqual(alice("POST", f"/projects/{pid}/restore").json()["data"]["status"], "active")
        activity = alice("GET", f"/projects/{pid}/activity").json()["data"]["items"]
        self.assertEqual(activity[0]["event_type"], "project.restored")
        self.assertEqual(self.error_code(alice("POST", f"/projects/{pid}/delete", {"confirm_name": "nope"}), 422), "VALIDATION_ERROR")
        self.assertEqual(alice("POST", f"/projects/{pid}/delete", {"confirm_name": "Thesis v2"}).json()["data"]["deleted"], True)
        self.assertEqual(self.error_code(alice("GET", f"/projects/{pid}"), 404), "NOT_FOUND")

    def test_validation_errors_use_the_standard_envelope(self):
        alice = self.user("alice@example.com")
        self.assertEqual(self.error_code(alice("POST", "/projects", {"name": ""}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", "/projects", {}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", "/projects", {"name": "x" * 600}), 422), "VALIDATION_ERROR")

    def test_other_users_cannot_see_or_change_a_project(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "Mine"}).json()["data"]["id"]
        probes = [("GET", f"/projects/{pid}", None), ("PATCH", f"/projects/{pid}", {"name": "stolen"}),
                  ("POST", f"/projects/{pid}/archive", None), ("POST", f"/projects/{pid}/restore", None),
                  ("POST", f"/projects/{pid}/delete", {"confirm_name": "Mine"}), ("GET", f"/projects/{pid}/activity", None)]
        for method, path, body in probes:
            response = bob(method, path, body)
            self.assertEqual(self.error_code(response, 404), "NOT_FOUND", path)
            self.assertNotIn("Mine", response.text)
        self.assertEqual(bob("GET", "/projects").json()["data"]["total"], 0)
        self.assertEqual(alice("GET", f"/projects/{pid}").json()["data"]["name"], "Mine")

    def test_quota_endpoint_reflects_entitlement(self):
        alice = self.user("alice@example.com")
        data = alice("GET", "/workspace/quota").json()["data"]
        self.assertEqual((data["unlimited"], data["max_documents"], data["max_pages_per_document"]), (False, 3, 100))

    def test_deleting_an_account_removes_projects_and_stored_files(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "Mine"}).json()["data"]["id"]
        self.api.h.storage_probe = None
        storage = self.api.app.state.storage
        storage.put(f"p/{pid}/a/abc/v/def/original", [b"data"])
        response = alice("POST", "/me/delete", {"password": GOOD_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(storage.list_prefix(f"p/{pid}") if storage.exists(f"p/{pid}/a/abc/v/def/original") else [], [])
        self.assertFalse(storage.exists(f"p/{pid}/a/abc/v/def/original"))
        row = self.api.h.conn.execute("SELECT status, name FROM projects WHERE id = ?", (pid,)).fetchone()
        self.assertEqual((row["status"], row["name"]), ("deleted", None))


class UploadApiTests(WorkspaceApiBase):
    DATA = b"name,score\nAda,91\nGrace,88\n"

    def setUp(self):
        from dataclasses import replace

        self.api = base.Api(upload_chunk_max_bytes=16, upload_max_bytes=4096)
        self.addCleanup(self.api.close)

    def push(self, call, project_id, name, data, chunk=16):
        import hashlib

        started = call("POST", f"/projects/{project_id}/uploads", {"filename": name, "size": len(data), "checksum_sha256": hashlib.sha256(data).hexdigest()})
        self.assertEqual(started.status_code, 201, started.text)
        info = started.json()["data"]["upload"]
        for i in range(info["total_chunks"]):
            piece = data[i * chunk:(i + 1) * chunk]
            r = call("POST", f"/uploads/{info['id']}/chunks/{i}", content=piece, headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(r.status_code, 200, r.text)
        return info, call("POST", f"/uploads/{info['id']}/complete")

    def test_full_upload_download_and_delete_over_http(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        info, done = self.push(alice, pid, "..\\..\\scores.csv", self.DATA)
        self.assertEqual(done.status_code, 200, done.text)
        asset = done.json()["data"]["asset"]
        self.assertEqual((asset["name"], asset["status"]), ("scores.csv", "ready"))
        self.assertNotIn("storage_key", done.text)
        got = alice("GET", f"/assets/{asset['id']}/download")
        self.assertEqual(got.content, self.DATA)
        self.assertEqual(got.headers["x-content-type-options"], "nosniff")
        self.assertIn("attachment", got.headers["content-disposition"])
        self.assertEqual(got.headers["content-type"], "application/octet-stream")
        self.assertEqual(alice("GET", f"/projects/{pid}/assets").json()["data"]["total"], 1)
        self.assertTrue(alice("DELETE", f"/assets/{asset['id']}").json()["data"]["deleted"])
        self.assertEqual(self.error_code(alice("GET", f"/assets/{asset['id']}"), 404), "NOT_FOUND")

    def test_oversized_chunks_and_bad_inputs_are_refused(self):
        alice = self.user("alice@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        started = alice("POST", f"/projects/{pid}/uploads", {"filename": "a.csv", "size": 40}).json()["data"]["upload"]
        too_big = alice("POST", f"/uploads/{started['id']}/chunks/0", content=b"x" * 5000)
        self.assertEqual(self.error_code(too_big, 413), "CHUNK_TOO_LARGE")
        self.assertEqual(self.error_code(alice("POST", f"/uploads/{started['id']}/chunks/0", content=b"short"), 400), "CHUNK_SIZE_MISMATCH")
        self.assertEqual(self.error_code(alice("POST", f"/uploads/{started['id']}/chunks/abc", content=b"x"), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", f"/projects/{pid}/uploads", {"filename": "huge.csv", "size": 99999}), 413), "FILE_TOO_LARGE")
        self.assertEqual(self.error_code(alice("POST", f"/projects/{pid}/uploads", {"filename": "run.exe", "size": 10}), 415), "UNSUPPORTED_FILE")

    def test_uploads_need_csrf_and_a_session_and_stay_private(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        info, done = self.push(alice, pid, "a.csv", self.DATA)
        asset = done.json()["data"]["asset"]["id"]
        self.assertEqual(self.error_code(self.api.call("POST", f"/uploads/{info['id']}/complete", csrf=False), 401), "UNAUTHENTICATED")
        for method, path in (("GET", f"/assets/{asset}"), ("GET", f"/assets/{asset}/download"), ("DELETE", f"/assets/{asset}"),
                             ("GET", f"/projects/{pid}/assets"), ("POST", f"/projects/{pid}/uploads"), ("GET", f"/uploads/{info['id']}"),
                             ("DELETE", f"/uploads/{info['id']}"), ("POST", f"/uploads/{info['id']}/chunks/0")):
            body = {"filename": "x.csv", "size": 10} if method == "POST" and path.endswith("/uploads") else None
            response = bob(method, path, body, content=b"x" * 16 if "chunks" in path else None)
            self.assertEqual(self.error_code(response, 404), "NOT_FOUND", path)
        no_csrf = self.api.call("DELETE", f"/assets/{asset}", csrf=False, client=self.api.client)
        self.assertIn(no_csrf.status_code, (401, 403))

    def test_preview_extraction_and_text_endpoints(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        _, done = self.push(alice, pid, "scores.csv", self.DATA)
        aid = done.json()["data"]["asset"]["id"]
        ext = alice("GET", f"/assets/{aid}/extraction").json()["data"]
        self.assertEqual(ext["status"], "ready")
        self.assertTrue(ext["summary"]["columns"])
        page = alice("GET", f"/assets/{aid}/preview", params={"offset": 0, "limit": 2}).json()["data"]
        self.assertLessEqual(len(page["rows"]), 2)
        self.assertEqual(self.error_code(alice("GET", f"/assets/{aid}/pages"), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("GET", f"/assets/{aid}/preview", params={"offset": "x"}), 422), "VALIDATION_ERROR")
        _, doc = self.push(alice, pid, "notes.txt", b"Plain words in a document.\n")
        did = doc.json()["data"]["asset"]["id"]
        self.assertIn("Plain words", alice("GET", f"/assets/{did}/pages").json()["data"]["pages"][0]["text"])
        self.assertIn("headings", alice("GET", f"/assets/{did}/structure").json()["data"])
        for path in (f"/assets/{aid}/extraction", f"/assets/{aid}/preview", f"/assets/{did}/pages", f"/assets/{did}/structure"):
            self.assertEqual(self.error_code(bob("GET", path), 404), "NOT_FOUND", path)
            self.assertEqual(self.error_code(self.api.call("GET", path, client=self.api.browser()), 401), "UNAUTHENTICATED", path)
        self.assertNotIn("storage", alice("GET", f"/assets/{aid}/extraction").text.lower().replace("storage_", ""))

    def test_profile_endpoints(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        _, done = self.push(alice, pid, "scores.csv", self.DATA)
        aid = done.json()["data"]["asset"]["id"]
        self.assertIsNone(alice("GET", f"/assets/{aid}/profile").json()["data"]["profile"])
        made = alice("POST", f"/assets/{aid}/profile")
        self.assertEqual(made.status_code, 200, made.text)
        data = made.json()["data"]
        self.assertEqual(data["profile"]["kind"], "dataset")
        self.assertEqual(data["profile"]["source"]["checksum_sha256"], done.json()["data"]["asset"]["checksum_sha256"])
        again = alice("POST", f"/assets/{aid}/profile", params={"refresh": "true"}).json()["data"]
        self.assertEqual(again["profile"]["content_hash"], data["profile"]["content_hash"])
        self.assertNotEqual(again["id"], data["id"])
        self.assertEqual(self.error_code(bob("GET", f"/assets/{aid}/profile"), 404), "NOT_FOUND")
        self.assertEqual(self.error_code(bob("POST", f"/assets/{aid}/profile"), 404), "NOT_FOUND")
        self.assertEqual(self.error_code(self.api.call("POST", f"/assets/{aid}/profile", csrf=False, client=self.api.browser()), 401), "UNAUTHENTICATED")
        self.assertEqual(self.error_code(alice("POST", f"/assets/{aid}/profile", headers={"X-CSRF-Token": "wrong"}), 403), "CSRF_FAILED")

    def test_retention_footprint_and_inventory_over_http(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        _, done = self.push(alice, pid, "p.csv", b"id,name\n1,Ada\n")
        aid = done.json()["data"]["asset"]["id"]
        ok = alice("PATCH", f"/assets/{aid}/retention", {"retain_until": "2026-06-30"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(alice("GET", f"/assets/{aid}").json()["data"]["retain_until"], "2026-06-30T23:59:59.000000Z")
        self.assertEqual(self.error_code(alice("PATCH", f"/assets/{aid}/retention", {"retain_until": "2020-01-01"}), 422), "VALIDATION_ERROR")
        fp = alice("GET", f"/assets/{aid}/footprint")
        self.assertEqual(fp.status_code, 200, fp.text)
        self.assertGreaterEqual(fp.json()["data"]["objects"], 2)
        inv = alice("GET", "/workspace/inventory").json()["data"]
        self.assertEqual((inv["assets"], inv["projects"]), (1, 1))
        self.assertEqual(bob("GET", "/workspace/inventory").json()["data"]["assets"], 0)
        for method, path, body in (("PATCH", f"/assets/{aid}/retention", {"retain_until": "2026-06-30"}), ("GET", f"/assets/{aid}/footprint", None)):
            self.assertEqual(self.error_code(bob(method, path, body), 404), "NOT_FOUND", path)
        self.assertEqual(alice("PATCH", f"/assets/{aid}/retention", {"retain_until": None}).json()["data"]["retain_until"], None)

    def test_cleaning_preview_confirm_history_and_rollback_over_http(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        pid = alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        data = b"id,name\n1, Ada \n2,Grace\n2,Grace\n"
        _, done = self.push(alice, pid, "p.csv", data)
        aid = done.json()["data"]["asset"]["id"]
        ops = [{"op": "drop_duplicates"}, {"op": "trim_whitespace"}]
        bad = alice("POST", f"/assets/{aid}/cleaning/preview", {"operations": [{"op": "drop_columns", "columns": ["nope"]}]})
        self.assertEqual(self.error_code(bad, 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", f"/assets/{aid}/cleaning/preview", {"operations": []}), 422), "VALIDATION_ERROR")
        pv = alice("POST", f"/assets/{aid}/cleaning/preview", {"operations": ops})
        self.assertEqual(pv.status_code, 200, pv.text)
        prev = pv.json()["data"]
        self.assertEqual((prev["summary"]["rows_before"], prev["summary"]["rows_after"]), (3, 2))
        self.assertEqual(alice("GET", f"/assets/{aid}").json()["data"]["row_count"], 3)  # preview saved nothing
        self.assertEqual(alice("GET", f"/cleaning/{prev['id']}").json()["data"]["id"], prev["id"])
        for method, path, body in (("GET", f"/cleaning/{prev['id']}", None), ("POST", f"/cleaning/{prev['id']}/confirm", {"confirm": True}),
                                   ("POST", f"/cleaning/{prev['id']}/discard", None), ("POST", f"/assets/{aid}/cleaning/preview", {"operations": ops}),
                                   ("GET", f"/assets/{aid}/history", None), ("POST", f"/assets/{aid}/rollback", {"version_id": "a" * 32, "confirm": True}),
                                   ("GET", f"/assets/{aid}/cleaning/suggestions", None)):
            self.assertEqual(self.error_code(bob(method, path, body), 404), "NOT_FOUND", path)
        self.assertEqual(self.error_code(alice("POST", f"/cleaning/{prev['id']}/confirm", {}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.error_code(alice("POST", f"/cleaning/{prev['id']}/confirm", {"confirm": False}), 422), "VALIDATION_ERROR")
        applied = alice("POST", f"/cleaning/{prev['id']}/confirm", {"confirm": True})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["data"]["version_number"], 2)
        self.assertEqual(self.error_code(alice("POST", f"/cleaning/{prev['id']}/confirm", {"confirm": True}), 409), "PREVIEW_ALREADY_USED")
        self.assertEqual(alice("GET", f"/assets/{aid}/download").content, data)  # the original download is untouched
        rows = alice("GET", f"/assets/{aid}/preview").json()["data"]["rows"]
        self.assertEqual([r[1] for r in rows], ["Ada", "Grace"])
        hist = alice("GET", f"/assets/{aid}/history").json()["data"]
        self.assertEqual(len(hist["versions"]), 2)
        self.assertTrue(hist["items"][0]["performed_by"]["name"])
        first = hist["versions"][0]["id"]
        self.assertEqual(self.error_code(alice("POST", f"/assets/{aid}/rollback", {"version_id": first}), 422), "VALIDATION_ERROR")
        back = alice("POST", f"/assets/{aid}/rollback", {"version_id": first, "confirm": True})
        self.assertEqual(back.status_code, 200, back.text)
        self.assertEqual(alice("GET", f"/assets/{aid}").json()["data"]["row_count"], 3)
        self.assertEqual(self.error_code(self.api.call("POST", f"/assets/{aid}/cleaning/preview", {"operations": ops}, client=self.api.browser()), 401), "UNAUTHENTICATED")


if __name__ == "__main__":
    unittest.main()
