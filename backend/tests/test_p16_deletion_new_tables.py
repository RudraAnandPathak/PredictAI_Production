"""Deleting a project or an account also removes what Phases 10B-10D added (exam predictions, notebook outputs, import records). Before this fix those rows pointed at deleted rows and
the foreign keys stopped the deletion."""
import json

from test_p13_exam_prediction import PredictBase
from test_p14_notebook import ART, NbBase


class Account(NbBase):
    def test_an_account_with_notebook_outputs_and_imports_can_be_deleted(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = lambda req: "Began in 1789 [S1]."
        self.nb.generate(self.alice, wid, "briefing")
        now = self.h.clock.iso()
        self.ws.ctx.conn.execute("INSERT INTO research_sessions (id, project_id, owner_user_id, title, question, level, status, options, latest_bundle_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 ("s1", self.pid, self.alice.user_id, "T", "q?", "topic", "completed", "{}", None, now, now))
        self.ws.ctx.conn.execute("INSERT INTO nb_imports (session_id, owner_user_id, workspace_id, created_at) VALUES (?,?,?,?)", ("s1", self.alice.user_id, wid, now))
        totals = self.ws.governance.delete_all_for_user(self.alice.user_id, self.alice.user_id)
        self.assertEqual(totals.get("person_ri_nb_outputs"), 1)
        self.assertEqual(totals.get("person_ri_nb_imports"), 1)
        for t in ("nb_outputs", "nb_imports", "rw_workspaces"):
            self.assertEqual(self.h.rows(f"SELECT * FROM {t} WHERE owner_user_id = ?", (self.alice.user_id,)), [], t)


class Project(PredictBase):
    def test_a_project_with_predictions_and_a_predicted_paper_can_be_deleted(self):
        pred = self.predict()
        self.pr.build_paper(self.alice, pred["id"], {"ai": "off", "spec": {"type_counts": {"short": 2}, "seed": 1}})
        self.assertTrue(self.h.rows("SELECT id FROM exam_predictions WHERE project_id = ?", (self.pid,)))
        self.ws.governance.delete_project(self.alice, self.h.rows("SELECT * FROM projects WHERE id = ?", (self.pid,))[0])
        self.assertEqual(self.h.rows("SELECT id FROM exam_predictions WHERE project_id = ?", (self.pid,)), [])
        self.assertEqual(self.h.rows("SELECT id FROM exam_assessments WHERE project_id = ?", (self.pid,)), [])

    def test_an_account_with_predictions_can_be_deleted(self):
        self.predict()
        self.ws.governance.delete_all_for_user(self.alice.user_id, self.alice.user_id)
        self.assertEqual(self.h.rows("SELECT id FROM exam_predictions WHERE owner_user_id = ?", (self.alice.user_id,)), [])
