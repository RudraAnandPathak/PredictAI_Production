"""HTTP tests for the Phase 7 registry and report routes: authentication, CSRF, the lifecycle flow, model -> report -> export -> download, and 404-only isolation."""
import unittest

try:
    import test_predictive_api as P  # raises SkipTest when fastapi/httpx are missing
except unittest.SkipTest:
    raise

VALUES = P.VALUES


class Phase7Api(P.PredictiveApiBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        r = cls.alice("POST", f"/projects/{cls.pid}/registry/models", {"source_model_id": cls.model["id"], "name": "Churn model"})
        assert r.status_code == 201, r.text
        cls.reg = r.json()["data"]
        cls.vid = cls.reg["versions"][0]["id"]
        cls.mid = cls.reg["id"]

    def routes(self):
        v, m = self.vid, self.mid
        return [("GET", "/registry/models"), ("GET", f"/registry/models/{m}"), ("GET", f"/registry/models/{m}/history"), ("GET", f"/registry/versions/{v}"), ("POST", f"/registry/versions/{v}/verify"),
                ("POST", f"/registry/versions/{v}/transition"), ("POST", f"/registry/versions/{v}/predict"), ("GET", f"/registry/versions/{v}/predictions"), ("POST", "/registry/compare"),
                ("POST", f"/registry/models/{m}/rollback"), ("GET", "/reports"), ("GET", "/reports/templates"), ("GET", "/reports/x"), ("PATCH", "/reports/x"), ("DELETE", "/reports/x"),
                ("POST", f"/projects/{self.pid}/reports"), ("POST", "/reports/x/exports"), ("GET", "/report-exports/x/download"),
                ("POST", f"/projects/{self.pid}/exam/subjects"), ("GET", "/exam/subjects/x"), ("POST", "/exam/subjects/x/documents"), ("GET", "/exam/subjects/x/questions"), ("POST", "/exam/questions/x/mapping"),
                ("POST", "/exam/subjects/x/analyses"), ("POST", "/exam/subjects/x/blueprints"), ("POST", "/exam/blueprints/x/generate"), ("GET", "/exam/assessments/x"), ("POST", "/exam/assessments/x/approve"),
                ("POST", f"/assets/{self.asset}/intelligence/root-cause"), ("POST", "/intelligence/metric"), ("POST", "/intelligence/scenarios"), ("POST", f"/assets/{self.asset}/intelligence/causal-candidates"),
                ("GET", "/intelligence/runs"), ("GET", "/intelligence/runs/x"),
                ("POST", "/workspaces"), ("GET", "/workspaces"), ("GET", "/workspaces/x"), ("POST", "/workspaces/x/invitations"), ("POST", "/workspaces/invitations/respond"), ("POST", "/workspaces/x/shares"),
                ("DELETE", "/shares/x"), ("GET", "/workspaces/x/events"),
                ("GET", "/schedules/adapters"), ("POST", "/reports/x/schedules"), ("GET", "/schedules"), ("GET", "/schedules/x"), ("POST", "/schedules/x/action"), ("POST", "/schedules/x/run"),
                ("GET", "/schedules/x/runs"), ("POST", "/schedule-deliveries/x/retry"), ("GET", "/schedule-runs/x/download"), ("GET", "/dashboard"),
                ("GET", "/owner/overview"), ("GET", "/owner/monitoring"), ("GET", "/owner/maintenance"), ("PUT", "/owner/maintenance"), ("GET", "/owner/quota-policy"),
                ("PUT", "/owner/quota-policy/standard_max_documents"), ("DELETE", "/owner/quota-policy/standard_max_documents"), ("GET", "/owner/events"), ("GET", "/owner/users/x/summary")]

    def test_every_route_requires_a_session(self):
        for method, path in self.routes():
            self.assertEqual(self.api.call(method, path, csrf=False).status_code, 401, f"{method} {path}")

    def test_changes_without_csrf_are_refused(self):
        other = P.base.Api()
        self.addCleanup(other.close)
        other.signed_in()
        for method, path in self.routes():
            if method != "GET":
                self.assertEqual(other.call(method, path, {}, csrf=False).status_code, 403, f"{method} {path}")

    def test_lifecycle_needs_confirmation_and_prediction_names_its_version(self):
        a = self.alice
        for to in ("evaluated", "staging"):
            self.assertEqual(a("POST", f"/registry/versions/{self.vid}/transition", {"to": to}).status_code, 200, to)
        refused = a("POST", f"/registry/versions/{self.vid}/transition", {"to": "active"})
        self.assertEqual(self.err(refused, 409), "CONFIRMATION_REQUIRED")
        ok = a("POST", f"/registry/versions/{self.vid}/transition", {"to": "active", "confirm": True, "reason": "go"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.err(a("POST", f"/registry/versions/{self.vid}/transition", {"to": "draft"}), 409), "INVALID_TRANSITION")
        pred = a("POST", f"/registry/versions/{self.vid}/predict", {"values": VALUES})
        self.assertEqual(pred.status_code, 200, pred.text)
        self.assertEqual(pred.json()["data"]["registry"]["version_id"], self.vid)

    def test_model_to_report_export_and_download(self):
        a = self.alice
        r = a("POST", f"/projects/{self.pid}/reports", {"source_kind": "prediction", "source": {"version_ids": [self.vid]}})
        self.assertEqual(r.status_code, 201, r.text)
        rep = r.json()["data"]
        self.assertEqual(rep["version"], 1)
        self.assertTrue(rep["evidence"])
        ex = a("POST", f"/reports/{rep['id']}/exports", {"format": "md"})
        self.assertEqual(ex.status_code, 201, ex.text)
        dl = a("GET", f"/report-exports/{ex.json()['data']['export_id']}/download")
        self.assertEqual(dl.status_code, 200)
        self.assertIn("attachment", dl.headers["content-disposition"])
        self.assertIn(rep["title"], dl.text)
        self.assertEqual(self.err(self.bob("GET", f"/reports/{rep['id']}"), 404), "NOT_FOUND")
        self.assertEqual(self.bob("GET", f"/report-exports/{ex.json()['data']['export_id']}/download").status_code, 404)

    def test_other_people_get_404_for_registry_resources(self):
        b = self.bob
        for method, path in [("GET", f"/registry/models/{self.mid}"), ("GET", f"/registry/versions/{self.vid}"), ("POST", f"/registry/versions/{self.vid}/predict")]:
            self.assertEqual(b(method, path, {"values": VALUES} if method == "POST" else None).status_code, 404, path)
        self.assertEqual(b("GET", "/registry/models").json()["data"]["total"], 0)
        self.assertEqual(b("POST", f"/projects/{self.pid}/registry/models", {"source_model_id": self.model["id"]}).status_code, 404)

    def test_invalid_bodies_are_validation_errors(self):
        self.assertEqual(self.err(self.alice("POST", f"/projects/{self.pid}/reports", {"source_kind": "research", "source": {"bundle_ids": []}}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.alice("POST", "/registry/compare", {}).status_code, 422)


    def test_intelligence_over_http_stores_runs_and_hides_them_from_others(self):
        a = self.alice
        r = a("POST", f"/assets/{self.asset}/intelligence/root-cause", {"outcome": "bought"})
        self.assertEqual(r.status_code, 201, r.text)
        run = r.json()["data"]
        self.assertEqual(set(run["registers"]), {"retrieved_facts", "calculated_results", "model_interpretations", "suggestions", "unresolved_questions"})
        self.assertEqual(a("GET", f"/intelligence/runs/{run['id']}").status_code, 200)
        self.assertEqual(self.bob("GET", f"/intelligence/runs/{run['id']}").status_code, 404)
        self.assertEqual(self.bob("POST", f"/assets/{self.asset}/intelligence/root-cause", {"outcome": "bought"}).status_code, 404)
        sc = a("POST", "/intelligence/scenarios", {"model_id": self.model["id"], "baseline": {"x1": 0.5, "x2": 0.2, "plan": "basic", "noise": 0.1}, "scenarios": [{"name": "up", "changes": {"x1": 2.0}}]})
        self.assertEqual(sc.status_code, 201, sc.text)
        bad = a("POST", "/intelligence/scenarios", {"model_id": self.model["id"], "baseline": {"x1": "abc"}, "scenarios": [{"name": "up", "changes": {"x1": 2.0}}]})
        self.assertEqual(self.err(bad, 422), "VALIDATION_ERROR")

    def test_exam_over_http_subject_isolation_and_validation(self):
        a = self.alice
        s = a("POST", f"/projects/{self.pid}/exam/subjects", {"name": "Chemistry"})
        self.assertEqual(s.status_code, 201, s.text)
        sid = s.json()["data"]["id"]
        self.assertEqual(self.bob("GET", f"/exam/subjects/{sid}").status_code, 404)
        self.assertEqual(a("POST", f"/projects/{self.pid}/exam/subjects", {"name": "Chemistry"}).status_code, 409)
        self.assertEqual(self.err(a("POST", f"/exam/subjects/{sid}/analyses", {}), 422), "VALIDATION_ERROR")
        self.assertEqual(self.err(a("POST", f"/exam/subjects/{sid}/documents", {"asset_id": self.asset, "kind": "notes"}), 422), "VALIDATION_ERROR", "a dataset is not an exam document")


    def test_sharing_over_http_grants_then_revokes_access(self):
        a, b = self.alice, self.bob
        w = a("POST", "/workspaces", {"name": "HTTP team"})
        self.assertEqual(w.status_code, 201, w.text)
        wid = w.json()["data"]["id"]
        rep = a("POST", f"/projects/{self.pid}/reports", {"source_kind": "prediction", "source": {"version_ids": [self.vid]}}).json()["data"]
        self.assertEqual(b("GET", f"/reports/{rep['id']}").status_code, 404)
        inv = a("POST", f"/workspaces/{wid}/invitations", {"email": "bob@example.com", "role": "viewer"})
        self.assertEqual(inv.status_code, 201, inv.text)
        token = inv.json()["data"]["invite_link"].split("token=")[1]
        self.assertEqual(b("POST", "/workspaces/invitations/respond", {"token": token, "accept": True}).status_code, 200)
        self.assertEqual(b("GET", f"/reports/{rep['id']}").status_code, 404, "membership alone shares nothing")
        g = a("POST", f"/workspaces/{wid}/shares", {"resource_type": "report", "resource_id": rep["id"], "permission": "edit"})
        self.assertEqual(g.status_code, 201, g.text)
        got = b("GET", f"/reports/{rep['id']}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["data"]["your_access"], "view")
        self.assertEqual(b("PATCH", f"/reports/{rep['id']}", {"base_version": 1, "ops": [{"op": "set_title", "text": "x"}]}).status_code, 404)
        self.assertEqual(b("POST", f"/workspaces/{wid}/invitations", {"email": "z@example.com", "role": "viewer"}).status_code, 403)
        self.assertEqual(a("DELETE", f"/shares/{g.json()['data']['grant_id']}").status_code, 200)
        self.assertEqual(b("GET", f"/reports/{rep['id']}").status_code, 404)
        self.assertEqual(b("GET", f"/workspaces/{wid}/events").status_code, 403)

    def test_dashboard_is_scoped_and_owner_routes_are_owner_only(self):
        d = self.alice("GET", "/dashboard")
        self.assertEqual(d.status_code, 200, d.text)
        self.assertGreaterEqual(d.json()["data"]["sections"]["work"]["projects"], 1)
        self.assertEqual(self.bob("GET", "/dashboard").json()["data"]["sections"]["work"]["projects"], 0)
        for method, path in (("GET", "/owner/overview"), ("GET", "/owner/monitoring"), ("PUT", "/owner/maintenance"), ("GET", "/owner/quota-policy")):
            self.assertEqual(self.alice(method, path, {"active": True, "confirm": True} if method == "PUT" else None).status_code, 403, path)
