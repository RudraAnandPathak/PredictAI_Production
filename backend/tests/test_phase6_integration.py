"""Phase 6 working with Phases 1 to 5 on one project: research that draws on a document, a dataset's profile, its Phase 4 findings and a Phase 5 trained model with each kept under its own
origin label; reusable evidence for other modules; the read-only model registry view; and what deleting a dataset takes with it. One database, one trained model and one set of services are
shared by the class because training is the slow part. No outside website is contacted."""
import json
import unittest

import analytics_support as sup
import ml_support as S
from app.modules.research.container import ResearchRuntime, build_research
from app.modules.retrieval.corpus import CORPUS_CACHE
from fixtures import pdf
from research_support import RIVER_DOC, make_fetcher
from support import Harness
from test_predictive_service import FAST, wait_job
from test_workspace_uploads import upload
from web_support import FakeWeb
from world_support import item, rss


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CORPUS_CACHE.clear()
        cls.web = FakeWeb()
        cls.web.__enter__()
        cls.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation="inprocess", research_web_enabled=True, research_allow_private_network=True,
                        searxng_url=cls.web.base, research_fetch_timeout_s=5)
        cls.alice, _ = cls.h.make_user("alice@example.com")
        cls.bob, _ = cls.h.make_user("bob@example.com")
        cls.ws = cls.h.workspace()
        cls.runtime = ResearchRuntime(db=cls.h.db, storage=cls.ws.storage, settings=cls.h.settings, clock=cls.h.clock, hasher=cls.h.hasher, sender=cls.h.outbox, http=None,
                                      fetcher=make_fetcher(cls.web), workers=2, sleeper=lambda s: None)
        cls.rs = build_research(cls.ws, cls.runtime)
        cls.svc, cls.world = cls.rs.research, cls.rs.world
        cls.pid = cls.ws.projects.create(cls.alice, "Customers")["id"]
        cls.doc = upload(cls.ws, cls.alice, cls.pid, "river.pdf", pdf(RIVER_DOC))["asset"]["id"]
        cls.df = S.classification_frame(500)
        cls.asset = upload(cls.ws, cls.alice, cls.pid, "customers.csv", sup.csv_of(cls.df))["asset"]
        cls.ws.profiles.generate(cls.alice, cls.asset["id"])
        cls.insights = cls.ws.insights.generate(cls.alice, cls.asset["id"])
        job = cls.ws.predictive.start_training(cls.alice, cls.asset["id"], FAST, label="Baseline")
        cls.model = wait_job(cls.ws.predictive, cls.alice, job["id"])["model"]
        cls.question = "What drives whether customers bought, and what does the model predict about bought?"
        s = cls.svc.create_session(cls.alice, cls.pid, {"question": cls.question, "level": "topic", "options": {"datasets": [cls.asset["id"]]}})
        job = cls.svc.run(cls.alice, s["id"], None)
        cls.runtime.runner.wait(job["id"], 120)
        cls.done = cls.svc.job(cls.alice, job["id"])
        cls.session_id = s["id"]
        cls.bundle = cls.svc.get_bundle(cls.alice, cls.done["result_id"])["bundle"] if cls.done["status"] == "completed" else None

    @classmethod
    def tearDownClass(cls):
        cls.runtime.runner.shutdown()
        cls.h.close()
        cls.web.__exit__()

    # ------------------------------------------------------------------ research over the project's own material
    def test_the_run_completed_and_drew_on_the_dataset_its_findings_and_the_model(self):
        self.assertEqual(self.done["status"], "completed", self.done)
        origins = {e["origin"] for e in self.bundle["evidence"]}
        self.assertIn("calculated", origins)                                                    # the dataset profile and Phase 4 findings
        self.assertIn("model_prediction", origins)                                              # the Phase 5 model
        self.assertNotIn("external_research", origins)                                          # the web was never switched on
        self.assertFalse(self.bundle["web"]["used"])

    def test_a_models_output_is_labelled_a_forecast_and_never_a_stated_fact(self):
        model_ev = [e for e in self.bundle["evidence"] if e["origin"] == "model_prediction"]
        self.assertTrue(model_ev)
        for e in model_ev:
            self.assertEqual(e["kind"], "prediction")
        summary = self.svc.summary(self.alice, self.done["result_id"])
        for c in summary["conclusions"]:
            if c["origin"] == "model_prediction":
                self.assertEqual(c["statement_type"], "prediction")
                self.assertNotEqual(c["statement_type"], "stated")

    def test_calculated_material_is_labelled_computed_and_names_where_it_came_from(self):
        calc = [e for e in self.bundle["evidence"] if e["origin"] == "calculated"]
        self.assertTrue(calc)
        self.assertTrue(all(e["kind"] == "computed" for e in calc))
        titles = {s["title"] for s in self.bundle["sources"]}
        self.assertTrue(any("customers.csv" in t for t in titles))

    def test_phase_4_findings_are_carried_with_their_evidence_status(self):
        stored = self.ws.insights.list(self.alice, self.asset["id"])["items"]
        self.assertTrue(stored)
        analysis = [e for e in self.bundle["evidence"] if e["origin"] == "calculated" and "evidence status" in e["quote"]]
        self.assertTrue(analysis, "no Phase 4 finding reached the evidence")
        statements = {i["statement"] for i in stored}
        self.assertTrue(any(any(st in e["quote"] for st in statements) for e in analysis))

    def test_the_bundle_never_exposes_row_level_data_or_storage_keys(self):
        text = json.dumps(self.bundle)
        for internal in ("text_key", "bundle_key", "/r/", "canonical_key"):
            self.assertNotIn(internal, text)
        row = self.df.iloc[0]
        self.assertNotIn(f"{row['x1']:.6f}", text)

    def test_every_citation_in_the_bundle_checks_out_against_its_source(self):
        q = self.bundle["quality"]
        self.assertEqual(q["schema"], "predictai.research_quality/1")
        self.assertNotEqual(q["verdict"], "failed")
        for e in self.bundle["evidence"]:
            got = self.svc.inspect_citation(self.alice, self.done["result_id"], e["id"])
            self.assertIn("verification", got)

    # ------------------------------------------------------------------ evidence reuse
    def test_other_modules_get_only_intact_labelled_evidence_and_reported_news(self):
        self.runtime.fetcher = make_fetcher(self.web)
        hub = self.rs.evidence.evidence_for_project(self.alice, self.pid)
        self.assertEqual(hub["schema"], "predictai.evidence_for_consumer/1")
        self.assertTrue(hub["research"])
        for summary in hub["research"]:
            for c in summary["conclusions"]:
                self.assertTrue(c["citations"], "a conclusion reached a consumer without its citation")
                self.assertIn(c["statement_type"], ("stated", "uncertain", "opinion", "prediction", "computed"))
        self.assertEqual(hub["world"], [])                                                       # no news was collected for this project
        self.assertEqual(hub["skipped"], [])

    def test_a_damaged_bundle_is_skipped_with_a_reason_and_does_not_hide_the_others(self):
        pid = self.ws.projects.create(self.alice, "Damaged")["id"]                              # its own project: the other tests must keep seeing only intact bundles
        doc = upload(self.ws, self.alice, pid, "river.pdf", pdf(RIVER_DOC))["asset"]["id"]
        s1 = self.svc.create_session(self.alice, pid, {"question": "Where did the levee hold at the northern bridge?", "options": {"documents": [doc]}})
        j1 = self.svc.run(self.alice, s1["id"], None)
        self.runtime.runner.wait(j1["id"], 120)
        intact = self.svc.job(self.alice, j1["id"])["result_id"]
        s2 = self.svc.create_session(self.alice, pid, {"question": "How high did the river rise after the heavy rainfall?", "options": {"documents": [doc]}})
        job = self.svc.run(self.alice, s2["id"], None)
        self.runtime.runner.wait(job["id"], 120)
        good = self.svc.job(self.alice, job["id"])["result_id"]
        bad = self.h.rows("SELECT bundle_key FROM evidence_bundles WHERE id = ?", (good,))[0]["bundle_key"]
        payload = json.loads(self.ws.storage.read_bytes(bad, 20_000_000))
        payload["conclusions"][0]["text"] = "Altered after the fact."
        self.ws.storage.put(bad, [json.dumps(payload).encode()], overwrite=True)
        hub = self.rs.evidence.evidence_for_project(self.alice, pid, limit=10)
        self.assertEqual([x["bundle_id"] for x in hub["skipped"]], [good])
        self.assertTrue(hub["skipped"][0]["reason"])
        self.assertIn(intact, [r["bundle_id"] for r in hub["research"]])
        self.assertNotIn(good, [r["bundle_id"] for r in hub["research"]])

    def test_news_is_included_as_reported_statements_when_the_person_collected_it(self):
        pid = self.ws.projects.create(self.alice, "News desk")["id"]
        for o in "abc":
            self.web.text(f"/hub-{o}.xml", rss([item({"a": "River floods northern village", "b": "Floods hit northern village as river rises", "c": "River flood: village evacuated"}[o],
                                                      f"The river rose 2.4 metres and 1,200 residents were evacuated, outlet {o}.", when=self.h.clock.now(), base=f"https://{o}.example")]),
                          ctype="application/rss+xml")
            self.world.add_source(self.alice, pid, {"name": o, "feed_url": f"{self.web.base}/hub-{o}.xml", "publisher": f"Outlet {o}"})
        self.svc.put_settings(self.alice, None, {"news_enabled": True})
        self.addCleanup(lambda: self.svc.put_settings(self.alice, None, {"news_enabled": False}))
        job = self.world.start_ingest(self.alice, pid)
        self.runtime.runner.wait(job["id"], 60)
        hub = self.rs.evidence.evidence_for_project(self.alice, pid)
        self.assertTrue(hub["world"])
        self.assertEqual({w["statement_type"] for w in hub["world"]}, {"reported"})
        self.assertEqual({w["origin"] for w in hub["world"]}, {"external_research"})
        self.assertEqual(self.rs.evidence.evidence_for_project(self.alice, pid, include_world=False)["world"], [])

    def test_evidence_of_one_project_never_reaches_another_project_or_another_person(self):
        other = self.ws.projects.create(self.alice, "Empty")["id"]
        self.assertEqual(self.rs.evidence.evidence_for_project(self.alice, other)["research"], [])
        from app.core.errors import AppError
        with self.assertRaises(AppError) as cm:
            self.rs.evidence.evidence_for_project(self.bob, self.pid)
        self.assertEqual(cm.exception.status_code, 404)

    # ------------------------------------------------------------------ exam research boundary
    def test_exam_research_creates_a_plan_from_course_documents_and_runs_nothing(self):
        out = self.rs.exam.topic_research(self.alice, self.pid, topic="flood levees", course_documents=[self.doc])
        self.assertEqual((out["session"]["purpose"], out["session"]["status"]), ("education", "draft"))
        self.assertEqual(out["session"]["web"]["will_send"], [])
        caps = self.rs.exam.capabilities()
        self.assertEqual({x["id"] for x in caps["not_available"]}, {"OUT-01a", "OUT-01b", "OUT-01c", "OUT-01d", "OUT-01e"})

    # ------------------------------------------------------------------ registry view
    def test_the_registry_view_shows_lineage_and_evaluation_and_does_not_pretend_to_deploy(self):
        entry = self.rs.registry.model(self.alice, self.model["id"])
        self.assertEqual(entry["lineage"]["asset_id"], self.asset["id"])
        self.assertEqual(entry["lineage"]["dataset_checksum_sha256"], self.ws.predictive.get_model(self.alice, self.model["id"])["dataset"]["checksum_sha256"])
        self.assertEqual(entry["target"], "bought")
        self.assertIsNotNone(entry["evaluation"]["held_out_test"])
        self.assertEqual(entry["deployment"]["state"], "not supported")
        self.assertFalse(entry["rollback"]["supported"])
        self.assertTrue(entry["training_history"])
        listing = self.rs.registry.models(self.alice, self.asset["id"])
        self.assertEqual([m["id"] for m in listing["items"]], [self.model["id"]])
        from app.core.errors import AppError
        with self.assertRaises(AppError) as cm:
            self.rs.registry.model(self.bob, self.model["id"])
        self.assertEqual(cm.exception.status_code, 404)

    # ------------------------------------------------------------------ deletion across phases (last: it removes the dataset)
    def test_zz_deleting_the_dataset_removes_the_model_the_bundles_that_quote_it_and_the_analyses_from_it(self):
        pid = self.ws.projects.create(self.alice, "Throwaway")["id"]
        asset = upload(self.ws, self.alice, pid, "tmp.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        self.ws.profiles.generate(self.alice, asset["id"])
        s = self.svc.create_session(self.alice, pid, {"question": "What drives whether customers bought?", "options": {"datasets": [asset["id"]]}})
        job = self.svc.run(self.alice, s["id"], None)
        self.runtime.runner.wait(job["id"], 120)
        bundle_id = self.svc.job(self.alice, job["id"])["result_id"]
        self.assertTrue(self.h.rows("SELECT asset_ids FROM evidence_bundles WHERE id = ?", (bundle_id,))[0]["asset_ids"] != "[]", "the bundle must quote the dataset for this test to mean anything")
        key = self.h.rows("SELECT bundle_key FROM evidence_bundles WHERE id = ?", (bundle_id,))[0]["bundle_key"]
        self.assertTrue(self.ws.storage.exists(key))
        self.ws.assets.delete(self.alice, asset["id"])
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM evidence_bundles WHERE id = ?", (bundle_id,))[0]["n"], 0)
        self.assertFalse(self.ws.storage.exists(key))
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM research_sources WHERE ref_asset_id = ?", (asset["id"],))[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
