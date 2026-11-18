"""R7: the Research Studio. Findings (comment, kind), links, groups, outlines, generators with quoted basis, evidence explorer, activity and home; isolation and deletion cascades."""
import sys
import unittest

sys.path.insert(0, "tests")
from p10_support import RIHarness
from app.modules.research_intelligence.reasoning.analysis import AnalysisService
from app.modules.research_intelligence.reports.service import ReportService
from app.modules.research_intelligence.studio import generators
from app.modules.research_intelligence.studio.service import StudioService
from app.modules.workspace.errors import AppError
from test_p10_reasoning import S1, S2, S3, mk, A, B, C, D
from app.modules.research_intelligence.reasoning import engine


class Base(unittest.TestCase):
    def setUp(self):
        self.t = RIHarness()
        self.addCleanup(self.t.close)
        self.alice = self.t.user("alice@example.com")
        self.wid = self.t.workspace(self.alice)
        self.src = {n: self.t.add_text(self.alice, self.wid, n, txt) for n, txt in (("trial.txt", S1), ("cohort.txt", S2), ("survey.txt", S3))}

    def st(self):
        return StudioService(self.t.ctx())

    def finding(self, name="trial.txt", kind="finding", who=None, wid=None, comment=None):
        who, wid = who or self.alice, wid or self.wid
        sid = self.src[name]["id"] if who is self.alice and wid == self.wid else self.t.ctx().conn.execute("SELECT id FROM ri_sources WHERE owner_user_id = ? LIMIT 1", (who.user_id,)).fetchone()["id"]
        chunk = self.t.ctx().conn.execute("SELECT id FROM ri_chunks WHERE source_id = ? ORDER BY ordinal LIMIT 1", (sid,)).fetchone()["id"]
        return self.t.notes().create_finding(who, wid, sid, None, chunk_id=chunk, kind=kind, comment=comment)

    def analysis(self):
        made = AnalysisService(self.t.ctx()).create(self.alice, self.wid)
        self.t.run_jobs()
        return made["id"]


class Findings(Base):
    def test_update_comment_and_kind(self):
        f = self.finding()
        out = self.st().update_finding(self.alice, self.wid, f["id"], comment="Key result", kind="highlight")
        self.assertEqual((out["comment"], out["kind"]), ("Key result", "highlight"))
        self.assertTrue(out["source_title"] and out["locator"])
        self.assertEqual(self.st().update_finding(self.alice, self.wid, f["id"], comment="")["comment"], None)
        for bad in ({"kind": "opinion"}, {"comment": "x" * 2001}):
            with self.assertRaises(AppError):
                self.st().update_finding(self.alice, self.wid, f["id"], **bad)

    def test_links_validation_and_cascade(self):
        a, b = self.finding("trial.txt"), self.finding("cohort.txt")
        l = self.st().link(self.alice, self.wid, a["id"], b["id"], "supports", "same direction")
        self.assertEqual((l["relation"], l["note"]), ("supports", "same direction"))
        with self.assertRaises(AppError) as e:
            self.st().link(self.alice, self.wid, a["id"], b["id"], "supports")
        self.assertEqual(e.exception.code, "LINK_EXISTS")
        self.st().link(self.alice, self.wid, b["id"], a["id"], "related")
        for args in ((a["id"], a["id"], "supports"), (a["id"], b["id"], "causes"), (a["id"], "nope", "supports")):
            with self.assertRaises(AppError):
                self.st().link(self.alice, self.wid, *args)
        self.assertEqual(len(self.st().links(self.alice, self.wid)), 2)
        self.t.notes().delete_finding(self.alice, self.wid, a["id"])
        self.assertEqual(self.st().links(self.alice, self.wid), [])
        c = self.finding("survey.txt")
        l2 = self.st().link(self.alice, self.wid, b["id"], c["id"], "contradicts")
        self.assertTrue(self.st().unlink(self.alice, self.wid, l2["id"])["deleted"])
        with self.assertRaises(AppError):
            self.st().unlink(self.alice, self.wid, l2["id"])

    def test_groups(self):
        a, b = self.finding("trial.txt"), self.finding("cohort.txt")
        g = self.st().create_group(self.alice, self.wid, "Effect on temperature")
        with self.assertRaises(AppError):
            self.st().create_group(self.alice, self.wid, "Effect on temperature")
        out = self.st().set_group_items(self.alice, self.wid, g["id"], [b["id"], a["id"], b["id"]])
        self.assertEqual(out["finding_ids"], [b["id"], a["id"]])
        with self.assertRaises(AppError):
            self.st().set_group_items(self.alice, self.wid, g["id"], ["nope"])
        self.assertEqual(self.st().rename_group(self.alice, self.wid, g["id"], "Renamed")["name"], "Renamed")
        self.t.notes().delete_finding(self.alice, self.wid, a["id"])
        self.assertEqual(self.st().groups(self.alice, self.wid)[0]["finding_ids"], [b["id"]])
        self.assertTrue(self.st().delete_group(self.alice, self.wid, g["id"])["deleted"])
        self.assertEqual(len(self.t.notes().list_findings(self.alice, self.wid)["items"]), 1)         # the finding stays

    def test_outlines(self):
        a = self.finding()
        o = self.st().create_outline(self.alice, self.wid, "Review plan", [{"heading": "Intro", "notes": "scope", "finding_ids": [a["id"]], "children": [{"heading": "Aims"}]}, {"heading": "Methods"}])
        self.assertEqual([i["heading"] for i in o["items"]], ["Intro", "Methods"])
        self.assertEqual(o["items"][0]["children"][0]["heading"], "Aims")
        upd = self.st().update_outline(self.alice, self.wid, o["id"], title="Plan v2", items=[{"id": o["items"][0]["id"], "heading": "Intro"}])
        self.assertEqual((upd["title"], len(upd["items"]), upd["items"][0]["id"]), ("Plan v2", 1, o["items"][0]["id"]))
        self.assertEqual(self.st().outlines(self.alice, self.wid)[0]["entries"], 1)
        deep = [{"heading": "a", "children": [{"heading": "b", "children": [{"heading": "c", "children": [{"heading": "d"}]}]}]}]
        for bad in (deep, [{"heading": ""}], [{"heading": "x", "finding_ids": ["nope"]}], "x", [{"heading": "x"}] * 201):
            with self.assertRaises(AppError, msg=str(bad)[:30]):
                self.st().create_outline(self.alice, self.wid, "t", bad)
        self.assertTrue(self.st().delete_outline(self.alice, self.wid, o["id"])["deleted"])

    def test_evidence_explorer_filters_and_counts(self):
        self.finding("trial.txt", "finding", comment="primary result")
        self.finding("trial.txt", "highlight")
        self.finding("cohort.txt", "evidence")
        ev = self.st().evidence(self.alice, self.wid)
        self.assertEqual(len(ev["items"]), 3)
        per = {s["id"]: s for s in ev["sources"]}
        t = per[self.src["trial.txt"]["id"]]
        self.assertEqual((t["findings"], t["highlights"], t["evidence"]), (1, 1, 0))
        self.assertEqual(len(ev["sources"]), 3)
        self.assertEqual(len(self.st().evidence(self.alice, self.wid, kind="evidence")["items"]), 1)
        self.assertEqual(len(self.st().evidence(self.alice, self.wid, source_id=self.src["trial.txt"]["id"])["items"]), 2)
        self.assertEqual(len(self.st().evidence(self.alice, self.wid, q="primary")["items"]), 1)
        self.assertEqual(self.st().evidence(self.alice, self.wid, q="%")["items"].__len__(), 3)             # wildcard characters are not special
        with self.assertRaises(AppError):
            self.st().evidence(self.alice, self.wid, kind="x")

    def test_saved_findings_survive_a_new_session(self):
        f = self.finding(comment="keep me")
        again = StudioService(self.t.ctx())
        self.assertEqual(again.evidence(self.alice, self.wid)["items"][0]["id"], f["id"])


class Generators(unittest.TestCase):
    def result(self, srcs, **kw):
        found = engine.analyse_findings(srcs)
        grades = engine.grade_sources(srcs)
        conf = engine.grade_themes(found["common"], grades, found["contradictions"])
        return {"sources": [{"label": s.label, "source_id": s.row["id"]} for s in srcs], "findings": found, "methods": engine.compare_methods(srcs), "gaps": engine.find_gaps(srcs),
                "evidence": {"sources": grades, "themes": conf}, "timeline": engine.timeline(srcs), "synthesis": engine.synthesise(srcs, found, conf)}

    def test_questions_from_contradiction_variation_and_author_need(self):
        srcs = [mk(1, [A], design="rct", limits=["Future research should examine other climates and seasons."]), mk(2, [B], design="cohort"), mk(3, [C], design="survey")]
        r = self.result(srcs)
        qs = generators.research_questions(r)
        kinds = {q["kind"] for q in qs}
        self.assertIn("contradiction", kinds)
        self.assertIn("author_need", kinds)
        for q in qs:
            self.assertTrue(q["basis"], q)
            self.assertIn("not a validated", q["note"])
            for b in q["basis"]:
                self.assertTrue(any(b["text"] == f["text"] for s in srcs for f in s.facts["findings"] + s.facts["limitations"]), "basis must be an exact extracted sentence")
        self.assertEqual(len({q["id"] for q in qs}), len(qs))

    def test_variation_and_design_questions(self):
        r = self.result([mk(1, [A], design="cohort"), mk(2, [B], design="survey")])
        kinds = {q["kind"] for q in generators.research_questions(r)}
        self.assertTrue({"variation", "design"} <= kinds)
        r2 = self.result([mk(1, [A], design="rct"), mk(2, [B], design="meta_analysis")])
        self.assertNotIn("design", {q["kind"] for q in generators.research_questions(r2)})

    def test_hypotheses_are_labelled_untested_and_have_a_test(self):
        r = self.result([mk(1, [A], design="rct"), mk(2, [D], design="survey"), mk(3, [B], design="cohort")])
        hs = generators.hypotheses(r)
        self.assertTrue(hs)
        for h in hs:
            self.assertEqual(h["status"], "untested suggestion")
            self.assertTrue(h["how_to_test"] and h["basis"])
        mod = next(h for h in hs if h["kind"] == "moderator")
        self.assertIn("design", mod["how_to_test"].lower())

    def test_subset_removes_what_needs_dropped_sources(self):
        r = self.result([mk(1, [A], design="rct"), mk(2, [B], design="cohort"), mk(3, [C], design="survey")])
        self.assertIn("contradiction", {q["kind"] for q in generators.research_questions(r)})
        only_two_agreeing = generators.research_questions(r, ["S1", "S2"])
        self.assertNotIn("contradiction", {q["kind"] for q in only_two_agreeing})

    def test_nothing_to_suggest_gives_an_empty_list(self):
        r = self.result([mk(1, ["Participants completed the questionnaire in the morning session."]), mk(2, ["The weather was mild throughout the whole visit."])])
        self.assertEqual((generators.research_questions(r), generators.hypotheses(r)), ([], []))


class Saved(Base):
    def test_generate_save_list_delete(self):
        aid = self.analysis()
        for kind in ("research_questions", "hypotheses", "literature_review"):
            g = self.st().generate(self.alice, self.wid, kind, aid)
            self.assertEqual(g["kind"], kind)
            self.assertIn("Not validated", g["label"])
        self.assertEqual({x["kind"] for x in self.st().generated_list(self.alice, self.wid)}, {"research_questions", "hypotheses", "literature_review"})
        lit = next(x for x in self.st().generated_list(self.alice, self.wid) if x["kind"] == "literature_review")
        self.assertIn("sections", self.st().generated(self.alice, self.wid, lit["id"])["content"])
        self.assertTrue(self.st().delete_generated(self.alice, self.wid, lit["id"])["deleted"])
        with self.assertRaises(AppError):
            self.st().generated(self.alice, self.wid, lit["id"])

    def test_generate_rules(self):
        aid = self.analysis()
        for args in (("poem", aid), ("hypotheses", "nope")):
            with self.assertRaises(AppError):
                self.st().generate(self.alice, self.wid, *args)
        with self.assertRaises(AppError):
            self.st().generate(self.alice, self.wid, "hypotheses", aid, ["nope"])
        pending = AnalysisService(self.t.ctx()).create(self.alice, self.wid)["id"]
        with self.assertRaises(AppError) as e:
            self.st().generate(self.alice, self.wid, "hypotheses", pending)
        self.assertEqual(e.exception.code, "ANALYSIS_NOT_READY")

    def test_deleting_a_source_removes_outputs_that_used_it(self):
        aid = self.analysis()
        self.st().generate(self.alice, self.wid, "research_questions", aid)
        self.st().generate(self.alice, self.wid, "hypotheses", aid, [self.src["trial.txt"]["id"], self.src["cohort.txt"]["id"]])
        self.t.sources().delete(self.alice, self.wid, self.src["survey.txt"]["id"])
        self.assertEqual([x["kind"] for x in self.st().generated_list(self.alice, self.wid)], ["hypotheses"])

    def test_activity_and_home(self):
        aid = self.analysis()
        self.finding(comment="x")
        self.st().generate(self.alice, self.wid, "hypotheses", aid)
        ReportService(self.t.ctx()).create(self.alice, self.wid, analysis_id=aid, report_type="research_summary")
        self.st().create_outline(self.alice, self.wid, "Plan")
        self.t.notes().create_note(self.alice, self.wid, "a note")
        kinds = {a["kind"] for a in self.st().activity(self.alice, self.wid, 50)}
        self.assertTrue({"analysis", "report", "generated", "outline", "finding", "note", "source"} <= kinds, kinds)
        home = self.st().home(self.alice)
        self.assertEqual(home["workspaces"][0]["sources"], 3)
        self.assertTrue(home["activity"] and home["sources"] and home["findings"] and home["reports"])
        self.assertEqual(home["activity"][0]["workspace_name"], "Study")
        bob = self.t.user("bob@example.com")
        empty = self.st().home(bob)
        self.assertEqual((empty["workspaces"], empty["activity"], empty["sources"], empty["findings"], empty["reports"]), ([], [], [], [], []))
        with self.assertRaises(AppError):
            self.st().activity(bob, self.wid)


class Isolation(Base):
    def test_other_users_cannot_use_my_findings_or_workspace(self):
        a, b = self.finding("trial.txt"), self.finding("cohort.txt")
        bob = self.t.user("bob@example.com")
        bw = self.t.workspace(bob, "Bobs")
        x = self.t.add_text(bob, bw, "x.txt", S1)
        chunk = self.t.ctx().conn.execute("SELECT id FROM ri_chunks WHERE source_id = ?", (x["id"],)).fetchone()["id"]
        bf = self.t.notes().create_finding(bob, bw, x["id"], None, chunk_id=chunk)
        st = self.st()
        for fn in (lambda: st.link(bob, bw, bf["id"], a["id"], "supports"), lambda: st.link(bob, self.wid, a["id"], b["id"], "supports"), lambda: st.update_finding(bob, self.wid, a["id"], "x"),
                   lambda: st.update_finding(bob, bw, a["id"], "x"), lambda: st.evidence(bob, self.wid), lambda: st.groups(bob, self.wid), lambda: st.outlines(bob, self.wid),
                   lambda: st.create_outline(bob, bw, "t", [{"heading": "h", "finding_ids": [a["id"]]}]), lambda: st.create_group(bob, self.wid, "g")):
            with self.assertRaises(AppError):
                fn()
        g = st.create_group(bob, bw, "mine")
        with self.assertRaises(AppError):
            st.set_group_items(bob, bw, g["id"], [a["id"]])


if __name__ == "__main__":
    unittest.main()
