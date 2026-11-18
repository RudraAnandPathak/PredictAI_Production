"""HTTP tests for analytics, insights, the assistant, drill-down and export (FastAPI TestClient; needs fastapi and httpx)."""
import hashlib
import json
import unittest

try:
    import test_workspace_api as wsbase  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

import analytics_support as sup


class AnalyticsApiBase(wsbase.WorkspaceApiBase):
    def setUp(self):
        from dataclasses import replace  # noqa: F401
        import test_identity_api as base

        self.api = base.Api(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20)
        self.addCleanup(self.api.close)
        self.df = sup.synth(n=400)
        self.data = sup.csv_of(self.df)
        self.alice = self.user("alice@example.com")
        self.bob = self.user("bob@example.com")
        self.pid = self.alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        self.asset = self.upload(self.alice, self.pid, "customers.csv", self.data)

    def upload(self, call, pid, name, data):
        started = call("POST", f"/projects/{pid}/uploads", {"filename": name, "size": len(data), "checksum_sha256": hashlib.sha256(data).hexdigest()})
        info = started.json()["data"]["upload"]
        for i in range(info["total_chunks"]):
            piece = data[i * info["chunk_size"]:(i + 1) * info["chunk_size"]]
            call("POST", f"/uploads/{info['id']}/chunks/{i}", content=piece, headers={"Content-Type": "application/octet-stream"})
        done = call("POST", f"/uploads/{info['id']}/complete")
        self.assertEqual(done.status_code, 200, done.text)
        return done.json()["data"]["asset"]["id"]


class AnalyticsApiTests(AnalyticsApiBase):
    def test_requires_login_and_csrf(self):
        a = self.asset
        for method, path in (("GET", f"/assets/{a}/analytics/schema"), ("POST", f"/assets/{a}/analytics/run"), ("GET", "/analytics/runs/x"), ("POST", f"/assets/{a}/assistant/ask"),
                             ("POST", f"/assets/{a}/insights/generate"), ("POST", "/analytics/runs/x/export")):
            self.assertEqual(self.api.call(method, path, csrf=False).status_code, 401, path)

    def test_schema_run_get_rows_export_over_http(self):
        sch = self.alice("GET", f"/assets/{self.asset}/analytics/schema").json()["data"]
        self.assertIn("region", [c["name"] for c in sch["columns"]])
        run = self.alice("POST", f"/assets/{self.asset}/analytics/run", {"kind": "chart", "operations": [{"op": "chart", "params": {"type": "bar", "x": "region"}}]})
        self.assertEqual(run.status_code, 201, run.text)
        r = run.json()["data"]
        self.assertEqual(r["evidence_status"], "VERIFIED")
        self.assertNotIn("storage_key", run.text)
        got = self.alice("GET", f"/analytics/runs/{r['id']}").json()["data"]
        self.assertEqual(got["result_hash"], r["result_hash"])
        bar = got["result"]["results"][0]["data"]["bars"][0]
        rows = self.alice("POST", f"/analytics/runs/{r['id']}/rows", {"select": bar["select"], "limit": 5})
        self.assertEqual(rows.status_code, 200, rows.text)
        self.assertEqual(rows.json()["data"]["total"], bar["n"])
        exp = self.alice("POST", f"/analytics/runs/{r['id']}/export", {"what": "rows", "select": bar["select"]})
        self.assertEqual(exp.status_code, 200)
        self.assertIn("attachment", exp.headers["content-disposition"])
        self.assertEqual(exp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(len(exp.text.strip().splitlines()) - 1, bar["n"])

    def test_malicious_plans_are_rejected(self):
        for body in ({"kind": "descriptive", "operations": [{"op": "exec", "params": {"code": "import os"}}]},
                     {"kind": "descriptive", "operations": [{"op": "column_stats", "params": {"columns": ["x'; DROP TABLE users;--"]}}]},
                     {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}], "filters": [{"column": "region", "op": "regex", "value": ".*"}]},
                     {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}], "seed": -1},
                     {"kind": "not-a-kind", "operations": [{"op": "dataset_summary", "params": {}}]}):
            r = self.alice("POST", f"/assets/{self.asset}/analytics/run", body)
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(r.json()["error"]["code"], "VALIDATION_ERROR")

    def test_other_user_sees_only_not_found(self):
        run = self.alice("POST", f"/assets/{self.asset}/analytics/run", {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}]}).json()["data"]
        ins = self.alice("POST", f"/assets/{self.asset}/insights/generate", {}).json()["data"]["insights"][0]["id"]
        seen = set()
        for method, path, body in (("GET", f"/analytics/runs/{run['id']}", None), ("POST", f"/analytics/runs/{run['id']}/rerun", None), ("POST", f"/analytics/runs/{run['id']}/rows", {"select": {"type": "all"}}),
                                   ("POST", f"/analytics/runs/{run['id']}/export", {"what": "result"}), ("POST", f"/analytics/runs/{run['id']}/save", {"saved": True}),
                                   ("GET", f"/assets/{self.asset}/analytics/schema", None), ("GET", f"/assets/{self.asset}/analytics/runs", None),
                                   ("POST", f"/assets/{self.asset}/analytics/run", {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}]}),
                                   ("POST", f"/assets/{self.asset}/insights/generate", {}), ("GET", f"/assets/{self.asset}/insights", None), ("GET", f"/insights/{ins}", None),
                                   ("POST", f"/insights/{ins}/state", {"state": "saved"}), ("POST", f"/assets/{self.asset}/assistant/ask", {"question": "how many rows"}),
                                   ("GET", f"/assets/{self.asset}/assistant/conversations", None), ("GET", "/analytics/runs/" + "0" * 32, None)):
            r = self.bob(method, path, body)
            seen.add((r.status_code, r.json()["error"]["code"]))
            self.assertNotIn("region", r.text)
        self.assertEqual(seen, {(404, "NOT_FOUND")})
        # the owner still sees everything, and Bob's attempts changed nothing
        self.assertEqual(self.alice("GET", f"/insights/{ins}").json()["data"]["state"], "new")

    def test_cross_project_dataset_cannot_be_used_by_guessing_ids(self):
        bpid = self.bob("POST", "/projects", {"name": "B"}).json()["data"]["id"]
        basset = self.upload(self.bob, bpid, "b.csv", sup.csv_of(sup.synth(n=120, seed=3)))
        r = self.alice("POST", f"/assets/{basset}/analytics/run", {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}]})
        self.assertEqual(r.status_code, 404)
        # a version id belonging to someone else's dataset cannot be smuggled in
        bv = self.bob("GET", f"/assets/{basset}/extraction").json()["data"]["version_id"]
        r2 = self.alice("POST", f"/assets/{self.asset}/analytics/run", {"kind": "descriptive", "operations": [{"op": "dataset_summary", "params": {}}], "version_id": bv})
        self.assertEqual(r2.status_code, 404)


class InsightAndAssistantApiTests(AnalyticsApiBase):
    def test_insight_lifecycle_save_dismiss_revisit_and_state_survives_regeneration(self):
        gen = self.alice("POST", f"/assets/{self.asset}/insights/generate", {}).json()["data"]
        self.assertTrue(gen["insights"])
        first = next(i for i in gen["insights"] if i["category"] in ("relationship", "group_difference"))
        for k in ("statement", "category", "supporting_evidence", "affected", "method", "uncertainty", "limitations", "filters", "dataset_version", "lineage"):
            self.assertIn(k, first, k)
        self.assertEqual(self.alice("POST", f"/insights/{first['id']}/state", {"state": "saved"}).json()["data"]["state"], "saved")
        again = self.alice("POST", f"/assets/{self.asset}/insights/generate", {}).json()["data"]["insights"]
        self.assertEqual(next(i for i in again if i["id"] == first["id"])["state"], "saved")
        self.assertEqual(self.alice("POST", f"/insights/{first['id']}/state", {"state": "dismissed"}).json()["data"]["state"], "dismissed")
        listed = self.alice("GET", f"/assets/{self.asset}/insights", params={"state": "dismissed"}).json()["data"]
        self.assertEqual([i["id"] for i in listed["items"]], [first["id"]])
        self.assertEqual(self.alice("POST", f"/insights/{first['id']}/state", {"state": "bogus"}).status_code, 422)

    def test_assistant_answers_clarifies_and_records_the_conversation(self):
        a = self.alice("POST", f"/assets/{self.asset}/assistant/ask", {"question": "What is the average spend?"}).json()["data"]
        self.assertEqual(a["status"], "answered")
        self.assertEqual(a["answer"]["verification"]["status"], "PASSED")
        self.assertAlmostEqual(a["answer"]["facts"][0]["value"], float(self.df["spend"].mean()), places=4)
        amb = self.alice("POST", f"/assets/{self.asset}/assistant/ask", {"question": "what is the average?", "conversation_id": a["conversation_id"]}).json()["data"]
        self.assertEqual(amb["status"], "needs_clarification")
        self.assertTrue(amb["clarification"]["options"])
        conv = self.alice("GET", f"/assistant/conversations/{a['conversation_id']}").json()["data"]
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant", "user", "assistant"])

    def test_assistant_never_uses_an_unconfigured_ai_provider_and_says_so(self):
        a = self.alice("POST", f"/assets/{self.asset}/assistant/ask", {"question": "how many rows", "use_ai": True}).json()["data"]
        self.assertFalse(a["answer"]["ai"]["used"])
        self.assertIn("not sent", a["answer"]["ai"]["reason"].lower())
        self.assertFalse(self.alice("GET", "/assistant/provider").json()["data"]["available"])


if __name__ == "__main__":
    unittest.main()
