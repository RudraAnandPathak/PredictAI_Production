"""Regression: a report built from saved dataset analyses (source_kind "dataset" or "combined") used to fail with a 500 because the
composer called .get() on a database row. The API-token agent skill relies on this path."""
from p7_support import P7Base, code_of


class DatasetReports(P7Base):
    def test_dataset_report_builds_and_exports_markdown(self):
        run = self.ws.analytics.preset(self.alice, self.asset["id"], "dashboard")
        r = self.p7.reports.create(self.alice, self.project, source_kind="dataset", source={"run_ids": [run["id"]]}, title="Dataset report")
        self.assertEqual(r["source_kind"], "dataset")
        e = self.p7.reports.export(self.alice, r["id"], "md")
        self.assertTrue(e["export_id"])
        self.assertEqual(e["format"], "md")
        name, mime, data = self.p7.reports.download(self.alice, e["export_id"])
        self.assertTrue(name.endswith(".md"))
        self.assertIn(b"#", data)

    def test_combined_dataset_and_model_report_builds(self):
        reg = self.p7.registry.register(self.alice, self.train(), name="Dataset report model")
        run = self.ws.analytics.preset(self.alice, self.asset["id"], "relationships")
        r = self.p7.reports.create(self.alice, self.project, source_kind="combined", source={"run_ids": [run["id"]], "version_ids": [reg["versions"][0]["id"]]})
        self.assertEqual(r["source_kind"], "combined")

    def test_another_users_run_is_still_refused(self):
        run = self.ws.analytics.preset(self.alice, self.asset["id"], "dashboard")
        self.assertEqual(code_of(self.p7.reports.create, self.bob, self.project, source_kind="dataset", source={"run_ids": [run["id"]]})[1], 404)
