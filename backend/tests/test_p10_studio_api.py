"""R7/R8 over HTTP: auth and CSRF on every route (see test_p10_api), the full path discover->analyse->studio->report->export, downloads, isolation and no 500s on bad input."""
import io
import unittest

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise

from test_p10_api import P, RiApi
from test_p10_reasoning import S1, S2, S3


class StudioReports(RiApi):
    def setup_ws(self, call):
        wid = self.workspace(call)
        ids = []
        for n, t in (("a.txt", S1), ("b.txt", S2), ("c.txt", S3)):
            r = self.upload(call, wid, n, t.encode())
            self.assertEqual(r.status_code, 201, r.text)
            ids.append(r.json()["data"]["id"])
        self.drain()
        return wid, ids

    def analysis(self, call, wid):
        r = call("POST", f"{P}/workspaces/{wid}/analyses", {})
        self.assertEqual(r.status_code, 202, r.text)
        self.drain()
        return r.json()["data"]["id"]

    def test_full_path_to_an_exported_report(self):
        alice = self.user("alice@example.com")
        wid, ids = self.setup_ws(alice)
        aid = self.analysis(alice, wid)
        chunk = alice("GET", f"{P}/workspaces/{wid}/sources/{ids[0]}/chunks").json()["data"]["items"][0]["id"]
        f1 = alice("POST", f"{P}/workspaces/{wid}/findings", {"source_id": ids[0], "chunk_id": chunk, "kind": "highlight"}).json()["data"]
        f2 = alice("POST", f"{P}/workspaces/{wid}/findings", {"source_id": ids[1], "chunk_id": alice("GET", f"{P}/workspaces/{wid}/sources/{ids[1]}/chunks").json()["data"]["items"][0]["id"]}).json()["data"]
        self.assertEqual(alice("PATCH", f"{P}/workspaces/{wid}/findings/{f1['id']}", {"comment": "important"}).json()["data"]["comment"], "important")
        link = alice("POST", f"{P}/workspaces/{wid}/studio/links", {"from_id": f1["id"], "to_id": f2["id"], "relation": "supports"})
        self.assertEqual(link.status_code, 201, link.text)
        g = alice("POST", f"{P}/workspaces/{wid}/studio/groups", {"name": "Core"}).json()["data"]
        self.assertEqual(alice("PUT", f"{P}/workspaces/{wid}/studio/groups/{g['id']}/items", {"finding_ids": [f1["id"], f2["id"]]}).json()["data"]["finding_ids"], [f1["id"], f2["id"]])
        o = alice("POST", f"{P}/workspaces/{wid}/studio/outlines", {"title": "Plan", "items": [{"heading": "Intro", "finding_ids": [f1["id"]]}]})
        self.assertEqual(o.status_code, 201, o.text)
        gen = alice("POST", f"{P}/workspaces/{wid}/studio/generate", {"kind": "research_questions", "analysis_id": aid})
        self.assertEqual(gen.status_code, 201, gen.text)
        self.assertTrue(gen.json()["data"]["content"]["items"])
        ev = alice("GET", f"{P}/workspaces/{wid}/studio/evidence", params={"kind": "highlight"}).json()["data"]
        self.assertEqual(len(ev["items"]), 1)
        self.assertEqual(ev["items"][0]["links"], 1)
        rep = alice("POST", f"{P}/workspaces/{wid}/reports", {"analysis_id": aid, "report_type": "academic", "title": "Canopy report"})
        self.assertEqual(rep.status_code, 201, rep.text)
        rid = rep.json()["data"]["id"]
        for fmt, magic, ctype in (("pdf", b"%PDF", "application/pdf"), ("docx", b"PK", "wordprocessingml"), ("pptx", b"PK", "presentationml"), ("md", b"# Canopy report", "text/markdown")):
            r = alice("GET", f"{P}/workspaces/{wid}/reports/{rid}/export", params={"format": fmt})
            self.assertEqual(r.status_code, 200, f"{fmt}: {r.text[:200]}")
            self.assertTrue(r.content.startswith(magic), fmt)
            self.assertIn(ctype, r.headers["content-type"])
            self.assertIn("attachment", r.headers["content-disposition"])
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(alice("GET", f"{P}/workspaces/{wid}/reports/{rid}/export", params={"format": "exe"}).status_code in (400, 422), True)
        body = alice("GET", f"{P}/workspaces/{wid}/reports/{rid}").json()["data"]
        body["sections"][0]["blocks"][0]["text"] = "Edited by me."
        upd = alice("PATCH", f"{P}/workspaces/{wid}/reports/{rid}", {"sections": body["sections"]})
        self.assertEqual(upd.status_code, 200, upd.text)
        self.assertEqual(upd.json()["data"]["version"], 2)
        self.assertEqual(len(alice("GET", f"{P}/workspaces/{wid}/reports/{rid}/versions").json()["data"]["items"]), 1)
        self.assertEqual(alice("POST", f"{P}/workspaces/{wid}/reports/{rid}/regenerate", {}).json()["data"]["version"], 3)
        self.assertEqual(len(alice("GET", f"{P}/reports").json()["data"]["items"]), 1)
        self.assertEqual(len(alice("GET", f"{P}/report-types").json()["data"]["items"]), 7)
        h = alice("GET", f"{P}/home").json()["data"]
        self.assertTrue(h["reports"] and h["findings"] and h["workspaces"])
        self.assertTrue(alice("GET", f"{P}/workspaces/{wid}/activity").json()["data"]["items"])
        self.assertEqual(alice("DELETE", f"{P}/workspaces/{wid}/reports/{rid}").json()["data"]["deleted"], True)

    def test_editing_a_quotation_is_rejected_over_http(self):
        alice = self.user("alice@example.com")
        wid, ids = self.setup_ws(alice)
        aid = self.analysis(alice, wid)
        rid = alice("POST", f"{P}/workspaces/{wid}/reports", {"analysis_id": aid, "report_type": "evidence_synthesis"}).json()["data"]["id"]
        body = alice("GET", f"{P}/workspaces/{wid}/reports/{rid}").json()["data"]
        for s in body["sections"]:
            for b in s["blocks"]:
                if b["type"] == "quote":
                    b["text"] = "Invented."
        r = alice("PATCH", f"{P}/workspaces/{wid}/reports/{rid}", {"sections": body["sections"]})
        self.assertIn(r.status_code, (400, 422), r.text)

    def test_bad_input_is_4xx_never_500(self):
        alice = self.user("alice@example.com")
        wid, ids = self.setup_ws(alice)
        aid = self.analysis(alice, wid)
        for method, path, payload in (("POST", f"{P}/workspaces/{wid}/reports", {}), ("POST", f"{P}/workspaces/{wid}/reports", {"analysis_id": aid, "report_type": "poem"}),
                                      ("POST", f"{P}/workspaces/{wid}/reports", {"analysis_id": aid, "report_type": "academic", "source_ids": []}),
                                      ("POST", f"{P}/workspaces/{wid}/studio/links", {"from_id": "a", "to_id": "b", "relation": "x"}), ("POST", f"{P}/workspaces/{wid}/studio/generate", {"kind": "x", "analysis_id": aid}),
                                      ("POST", f"{P}/workspaces/{wid}/studio/outlines", {"title": "t", "items": [{"heading": ""}]}), ("PATCH", f"{P}/workspaces/{wid}/reports/nope", {"title": "x"}),
                                      ("PUT", f"{P}/workspaces/{wid}/studio/groups/nope/items", {"finding_ids": []}), ("POST", f"{P}/workspaces/{wid}/reports/nope/versions/abc/restore", {})):
            r = alice(method, path, payload)
            self.assertIn(r.status_code, (400, 404, 422), f"{method} {path} {payload} -> {r.status_code}")

    def test_isolation_between_users(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        wa, ids = self.setup_ws(alice)
        wb = self.workspace(bob, "Bobs")
        aid = self.analysis(alice, wa)
        rid = alice("POST", f"{P}/workspaces/{wa}/reports", {"analysis_id": aid, "report_type": "academic"}).json()["data"]["id"]
        gid = alice("POST", f"{P}/workspaces/{wa}/studio/generate", {"kind": "hypotheses", "analysis_id": aid}).json()["data"]["id"]
        for method, path in (("GET", f"{P}/workspaces/{wa}/reports/{rid}"), ("GET", f"{P}/workspaces/{wb}/reports/{rid}"), ("GET", f"{P}/workspaces/{wa}/reports/{rid}/export?format=pdf"),
                             ("DELETE", f"{P}/workspaces/{wa}/reports/{rid}"), ("GET", f"{P}/workspaces/{wa}/studio/generated/{gid}"), ("GET", f"{P}/workspaces/{wa}/studio/evidence"),
                             ("GET", f"{P}/workspaces/{wa}/activity"), ("GET", f"{P}/workspaces/{wa}/reports")):
            self.assertEqual(bob(method, path).status_code, 404, path)
        self.assertEqual(bob("POST", f"{P}/workspaces/{wb}/reports", {"analysis_id": aid, "report_type": "academic"}).status_code, 404)
        self.assertEqual(bob("GET", f"{P}/reports").json()["data"]["items"], [])
        self.assertEqual(bob("GET", f"{P}/home").json()["data"]["reports"], [])


if __name__ == "__main__":
    unittest.main()
