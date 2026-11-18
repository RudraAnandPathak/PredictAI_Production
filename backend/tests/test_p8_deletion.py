"""Phase 8: deleting a file, a project or an account removes Phase 7 and Phase 8 data and objects (closes the Phase 7 deletion gap)."""
import analytics_support as sup
import ml_support as S
from app.modules.researchspace.service import ResearchSpaceService
from p7_support import P7Base
from test_predictive_service import FAST, wait_job
from test_workspace_uploads import upload

P7_TABLES = ("reg_models", "reg_versions", "reg_events", "rep_reports", "rep_versions", "rep_evidence", "rep_exports", "sch_schedules", "sch_runs", "adv_runs",
             "exam_subjects", "exam_documents", "exam_questions")


class DeletionTests(P7Base):
    def build_project(self, name):
        """A project with a file, a trained and registered model, a report, an export, a schedule, a share and a research source: every kind of Phase 7/8 residue."""
        pid = self.ws.projects.create(self.alice, name)["id"]
        asset = upload(self.ws, self.alice, pid, "d.csv", sup.csv_of(S.classification_frame(300)))["asset"]
        job = self.ws.predictive.start_training(self.alice, asset["id"], dict(FAST))
        model_id = wait_job(self.ws.predictive, self.alice, job["id"])["model_id"]
        reg = self.p7.registry.register(self.alice, model_id, name=f"{name} model")
        rep = self.p7.reports.create(self.alice, pid, source_kind="prediction", source={"version_ids": [reg["versions"][0]["id"]]})
        export = self.p7.reports.export(self.alice, rep["id"], "md")
        sched = self.p7.schedule.create(self.alice, rep["id"], {"frequency": "daily", "timezone": "Asia/Kolkata", "local_time": "09:00", "export_format": "md", "mode": "refresh"})
        cw = self.p7.collab.create_workspace(self.alice, name=f"team-{name}")
        share = self.p7.collab.share(self.alice, cw["id"], resource_type="report", resource_id=rep["id"], permission="view")
        rw = ResearchSpaceService(self.ws.ctx)
        w = rw.create(self.alice, f"lit-{name}")
        self.ws.ctx.conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,ref_asset_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (f"src-{name}", w["id"], self.alice.user_id, "csv", "d", asset["id"], "ready", "t", "t"))
        return dict(pid=pid, asset=asset, reg=reg, rep=rep, export=export, sched=sched, cw=cw, share=share, w=w)

    def count(self, table, where="1=1", args=()):
        return self.ws.ctx.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]

    def objects(self, project_id):
        st = self.h.storage
        return st.list_prefix(f"registry/{project_id}/") + st.list_prefix(f"reports/{project_id}/")

    def test_the_data_exists_before_deletion(self):
        b = self.build_project("Before")
        self.assertTrue(self.objects(b["pid"]), "registry copy and export are stored outside the project prefix")
        self.assertEqual(self.count("rep_reports", "project_id = ?", (b["pid"],)), 1)

    def test_deleting_a_project_removes_every_phase_seven_row_and_object_and_revokes_shares(self):
        b = self.build_project("Doomed")
        self.assertTrue(self.objects(b["pid"]))
        self.ws.projects.delete(self.alice, b["pid"], "Doomed")
        for table in ("reg_models", "rep_reports", "sch_schedules", "adv_runs", "exam_subjects"):
            self.assertEqual(self.count(table, "project_id = ?", (b["pid"],)), 0, table)
        self.assertEqual(self.count("reg_versions", "model_id = ?", (b["reg"]["id"],)), 0)
        self.assertEqual(self.count("reg_events", "model_id = ?", (b["reg"]["id"],)), 0)
        for table in ("rep_versions", "rep_evidence", "rep_exports"):
            self.assertEqual(self.count(table, "report_id = ?", (b["rep"]["id"],)), 0, table)
        self.assertEqual(self.count("sch_runs", "schedule_id = ?", (b["sched"]["id"],)), 0)
        self.assertEqual(self.objects(b["pid"]), [])
        self.assertEqual(self.count("rw_sources", "ref_asset_id = ?", (b["asset"]["id"],)), 0)
        self.assertEqual(self.count("ws_resources", "resource_id = ? AND revoked_at IS NULL", (b["rep"]["id"],)), 0)
        self.assertEqual(self.count("ws_resources", "resource_id = ?", (b["rep"]["id"],)), 1, "the share row is kept as history, revoked")
        # the research workspace itself belongs to the person, not the project: it stays
        self.assertEqual(self.count("rw_workspaces", "id = ?", (b["w"]["id"],)), 1)

    def test_deleting_one_project_leaves_another_projects_data_alone(self):
        keep, gone = self.build_project("Keep"), self.build_project("Gone")
        self.ws.projects.delete(self.alice, gone["pid"], "Gone")
        self.assertEqual(self.count("rep_reports", "project_id = ?", (keep["pid"],)), 1)
        self.assertEqual(self.count("reg_models", "project_id = ?", (keep["pid"],)), 1)
        self.assertTrue(self.objects(keep["pid"]))
        self.assertEqual(self.count("ws_resources", "resource_id = ? AND revoked_at IS NULL", (keep["rep"]["id"],)), 1)
        self.assertEqual(self.count("rw_sources", "ref_asset_id = ?", (keep["asset"]["id"],)), 1)

    def test_deleting_a_file_removes_what_was_built_from_it_but_not_other_files_data(self):
        a, b = self.build_project("FileA"), self.build_project("FileB")
        self.ws.assets.delete(self.alice, a["asset"]["id"])
        self.assertEqual(self.count("rw_sources", "ref_asset_id = ?", (a["asset"]["id"],)), 0)
        self.assertEqual(self.count("rw_sources", "ref_asset_id = ?", (b["asset"]["id"],)), 1)
        self.assertEqual(self.count("rw_workspaces", "id = ?", (a["w"]["id"],)), 1)

    def test_deleting_an_account_removes_research_workspaces_templates_and_memberships_and_revokes_shares(self):
        b = self.build_project("Account")
        conn = self.ws.ctx.conn
        conn.execute("INSERT INTO rw_conversations (id,workspace_id,owner_user_id,title,created_at,updated_at) VALUES ('c1',?,?,'q','t','t')", (b["w"]["id"], self.alice.user_id))
        conn.execute("INSERT INTO rw_messages (id,conversation_id,workspace_id,owner_user_id,role,body,source_refs,created_at) VALUES ('m1','c1',?,?, 'user','hi','[]','t')", (b["w"]["id"], self.alice.user_id))
        conn.execute("INSERT INTO rw_notes (id,workspace_id,owner_user_id,body,created_at,updated_at) VALUES ('n1',?,?,'note','t','t')", (b["w"]["id"], self.alice.user_id))
        self.ws.governance.delete_all_for_user(self.alice.user_id, self.alice.user_id)
        for table in ("rw_workspaces", "rw_sources", "rw_conversations", "rw_messages", "rw_notes"):
            self.assertEqual(self.count(table, "owner_user_id = ?", (self.alice.user_id,)), 0, table)
        self.assertEqual(self.count("rep_reports", "owner_user_id = ?", (self.alice.user_id,)), 0)
        self.assertEqual(self.count("ws_members", "user_id = ? AND status = 'active'", (self.alice.user_id,)), 0)
        self.assertEqual(self.count("ws_resources", "resource_owner_id = ? AND revoked_at IS NULL", (self.alice.user_id,)), 0)
        self.assertEqual(self.objects(b["pid"]), [])
