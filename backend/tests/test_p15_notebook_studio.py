"""Notebook Stage 2 and 3: structured Studio outputs (flashcards, quiz, mind map, data table, slides, infographic, audio script), downloads, Deep Research and the import of older
research reports. Gemini is a local stand-in; discovery and the older research service are fakes with the same method shapes."""
import io
import json
import unittest

from app.modules.notebook import structured as T
from app.modules.research_intelligence.services.sources import SourceService
from test_p14_notebook import ART, NbBase, SECOND, code_of


def reply(obj):
    return lambda req: json.dumps(obj)


CARDS = {"cards": [{"front": "When did the Revolution begin?", "back": "1789", "sources": ["S1"]}, {"front": "When was the king executed?", "back": "January 1793", "sources": ["S2"]},
                   {"front": "What did the Estates-General do?", "back": "Met at Versailles", "sources": ["S1", "S77"]}, {"front": "Invented card", "back": "no source", "sources": []},
                   {"front": "", "back": "empty front"}]}


class Structured(NbBase):
    def test_flashcards_keep_only_cards_with_a_real_source_and_drop_made_up_marks(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = reply(CARDS)
        out = self.nb.generate(self.alice, wid, "flashcards")
        cards = out["meta"]["data"]["cards"]
        self.assertEqual([c["back"] for c in cards], ["1789", "January 1793", "Met at Versailles"])
        self.assertEqual(cards[2]["sources"], ["S1"])
        self.assertEqual(out["meta"]["check"]["dropped_items"], 2)
        self.assertEqual(sorted(out["citations"]), ["S1", "S2"])
        self.assertIn("**1. When did the Revolution begin?**", out["content"])
        self.assertIn("JSON", next(m["content"] for m in self.gem.requests[0]["messages"] if m["role"] == "system"))

    def test_a_quiz_question_needs_four_distinct_options_and_a_valid_answer(self):
        self.setup_gemini()
        wid = self.notebook()
        good = {"question": "Year the Revolution began?", "options": ["1789", "1792", "1793", "1815"], "answer": 0, "explanation": "Estates-General met", "sources": ["S1"]}
        self.gem.answer = reply({"questions": [good, {**good, "question": "Republic declared?", "answer": 1, "sources": ["S2"]}, {**good, "answer": 4}, {**good, "options": ["a", "a", "b", "c"]},
                                               {**good, "answer": True}, {**good, "options": ["1", "2", "3"]}]})
        out = self.nb.generate(self.alice, wid, "quiz")
        self.assertEqual(len(out["meta"]["data"]["questions"]), 2)
        self.assertIn("Answer: B.", out["content"])

    def test_mind_map_depth_and_size_are_capped(self):
        self.setup_gemini()
        wid = self.notebook()
        deep = {"label": "L1", "sources": ["S1"], "children": [{"label": "L2", "children": [{"label": "L3", "children": [{"label": "L4 too deep", "children": []}]}]}]}
        self.gem.answer = reply({"root": "Revolution", "children": [deep, {"label": "Other", "sources": ["S2"], "children": []}]})
        d = self.nb.generate(self.alice, wid, "mind_map")["meta"]["data"]
        self.assertEqual(d["root"], "Revolution")
        self.assertEqual(d["children"][0]["children"][0]["children"][0]["children"], [])
        many = {"root": "R", "children": [{"label": f"n{i}", "children": [{"label": f"n{i}-{j}", "children": []} for j in range(12)]} for i in range(12)]}
        data, rep = T.validate("mind_map", json.dumps(many), ["S1"])
        self.assertLessEqual(rep["items"], 80)

    def test_table_rows_must_match_the_columns_and_cite_a_source(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = reply({"title": "Events", "columns": ["Year", "Event"], "rows": [{"cells": ["1789", "Estates-General"], "sources": ["S1"]}, {"cells": ["1793"], "sources": ["S2"]},
                                                                                         {"cells": ["1792", "Republic"], "sources": []}, {"cells": [1793, "Execution"], "sources": ["S2"]}]})
        out = self.nb.generate(self.alice, wid, "data_table")
        self.assertEqual([r["cells"] for r in out["meta"]["data"]["rows"]], [["1789", "Estates-General"], ["1793", "Execution"]])

    def test_an_unusable_reply_saves_nothing_and_says_why(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = lambda req: "Sorry, here are some cards: front/back"
        self.assertEqual(code_of(self.nb.generate, self.alice, wid, "flashcards"), "AI_OUTPUT_UNUSABLE")
        self.gem.answer = reply({"cards": [{"front": "x", "back": "y", "sources": []}]})
        self.assertEqual(code_of(self.nb.generate, self.alice, wid, "flashcards"), "AI_OUTPUT_UNUSABLE")
        self.assertEqual(self.nb.list(self.alice, wid)["items"], [])

    def test_structured_outputs_need_an_ai_and_never_fall_back_to_guessing(self):
        self.setup_gemini()
        wid = self.notebook()
        self.switch_off()
        self.assertEqual(code_of(self.nb.generate, self.alice, wid, "quiz"), "AI_UNAVAILABLE")
        self.assertEqual(self.gemini_requests(), 0)

    def test_audio_script_has_two_hosts_and_a_fenced_reply_is_accepted(self):
        self.setup_gemini()
        wid = self.notebook()
        lines = [{"speaker": "AB"[i % 2], "text": f"Line {i} about 1789", "sources": ["S1"] if i % 2 else []} for i in range(6)] + [{"speaker": "C", "text": "third host"}]
        self.gem.answer = lambda req: "```json\n" + json.dumps({"title": "Revolution talk", "lines": lines}) + "\n```"
        out = self.nb.generate(self.alice, wid, "audio_script")
        d = out["meta"]["data"]
        self.assertEqual(len(d["lines"]), 6)
        self.assertEqual({x["speaker"] for x in d["lines"]}, {"A", "B"})
        self.assertEqual(out["meta"]["check"]["uncited_items"], 3)

    def test_infographic_drops_numbers_without_a_source(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = reply({"title": "1789", "stats": [{"value": "1789", "label": "start", "sources": ["S1"]}, {"value": "99%", "label": "made up", "sources": []}],
                                 "points": [{"text": "Debt", "sources": ["S1"]}, {"text": "Bread prices", "sources": ["S1"]}]})
        d = self.nb.generate(self.alice, wid, "infographic")["meta"]["data"]
        self.assertEqual([s["value"] for s in d["stats"]], ["1789"])

    def test_kinds_list_every_output_and_its_downloads(self):
        kinds = {k["kind"]: k for k in self.nb.kinds()["items"]}
        self.assertEqual(set(kinds), {"briefing", "study_guide", "faq", "timeline", "flashcards", "quiz", "mind_map", "data_table", "slides", "infographic", "audio_script"})
        self.assertIn("pptx", kinds["slides"]["exports"])
        self.assertIn("csv", kinds["data_table"]["exports"])


class Exports(NbBase):
    def make(self, kind, obj):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = reply(obj)
        return self.nb.generate(self.alice, wid, kind)

    def test_slides_download_as_a_real_powerpoint_with_sources_in_the_notes(self):
        out = self.make("slides", {"title": "Revolution", "slides": [{"title": "Start", "bullets": ["1789"], "notes": "Begin here", "sources": ["S1"]}, {"title": "End", "bullets": ["1793"], "sources": ["S2"]}]})
        f = self.nb.export(self.alice, out["id"], "pptx")
        self.assertTrue(f["filename"].endswith(".pptx"))
        from pptx import Presentation
        prs = Presentation(io.BytesIO(f["body"]))
        self.assertEqual(len(prs.slides), 3)
        self.assertIn("History", prs.slides[1].notes_slide.notes_text_frame.text)
        self.assertEqual(code_of(self.nb.export, self.alice, out["id"], "csv"), "VALIDATION_ERROR")

    def test_table_csv_neutralises_formulas(self):
        out = self.make("data_table", {"columns": ["A", "B"], "rows": [{"cells": ["=HYPERLINK(1)", "ok"], "sources": ["S1"]}, {"cells": ["x", "y"], "sources": ["S2"]}]})
        body = self.nb.export(self.alice, out["id"], "csv")["body"].decode()
        self.assertIn("'=HYPERLINK(1)", body)
        self.assertTrue(body.startswith("A,B,sources"))

    def test_markdown_download_lists_the_sources_and_others_cannot_download(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = lambda req: "Began in 1789 [S1]."
        out = self.nb.generate(self.alice, wid, "briefing")
        body = self.nb.export(self.alice, out["id"], "markdown")["body"].decode()
        self.assertIn("## Sources", body)
        self.assertIn("[S1] History", body)
        self.assertEqual(code_of(self.nb.export, self.bob, out["id"], "markdown"), "NOT_FOUND")


class FakeDiscovery:
    def __init__(self, results, fail=()):
        self.results, self.fail, self.calls, self.saved, self.imported = results, set(fail), [], [], []

    def search(self, principal, body, workspace_id=None):
        q = body["query"]
        self.calls.append(q)
        if q in self.fail:
            raise RuntimeError("provider down")
        return {"search_id": f"s-{q}", "items": self.results.get(q, [])}

    def save(self, principal, search_id, rid):
        self.saved.append(rid)
        return {"id": f"lib-{rid}"}

    def import_to_workspace(self, principal, wid, lib_id, full_text, sources):
        if lib_id == "lib-dup":
            raise ValueError("already in this notebook")
        self.imported.append((wid, lib_id, full_text))
        return {"id": f"src-{lib_id}"}


def hit(rid, title, score, oa=True):
    return {"rid": rid, "title": title, "score": score, "url": f"https://example.org/{rid}", "oa_url": f"https://example.org/{rid}.pdf" if oa else None, "kind": "paper", "year": 2020}


class DeepResearch(NbBase):
    def test_the_ai_plans_queries_and_the_best_results_become_sources(self):
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = reply({"queries": ["french revolution causes", "bread prices 1789"]})
        disc = FakeDiscovery({"french revolution causes": [hit("a", "Causes", 0.9), hit("b", "Closed paper", 0.95, oa=False)], "bread prices 1789": [hit("c", "Bread", 0.7), hit("a", "Causes", 0.5)]})
        out = self.nb.deep_research(self.alice, wid, "Why did the French Revolution happen?", disc, SourceService(self.ws.ctx), 2)
        self.assertEqual(disc.calls, ["french revolution causes", "bread prices 1789"])
        self.assertEqual([a["title"] for a in out["meta"]["added"]], ["Causes", "Bread"])      # open-access results first, then score; duplicates merged
        self.assertTrue(all(full for _, _, full in disc.imported))
        self.assertEqual(out["kind"], "deep_research")
        self.assertIn("Added 2 source(s)", out["content"])
        req = self.gem.requests[0]
        self.assertIn("Why did the French Revolution happen?", json.dumps(req))

    def test_without_ai_the_question_itself_is_searched_and_failures_are_reported(self):
        wid = self.notebook()
        q = "Why did the French Revolution happen?"
        disc = FakeDiscovery({q: [hit("dup", "Already there", 0.9), hit("x", "New", 0.8)]})
        out = self.nb.deep_research(self.alice, wid, q, disc, SourceService(self.ws.ctx), 5)
        self.assertEqual(disc.calls, [q])
        self.assertEqual(out["mode"], "extractive")
        self.assertEqual([a["title"] for a in out["meta"]["added"]], ["New"])
        self.assertEqual(out["meta"]["skipped"][0]["reason"], "ValueError")
        out2 = self.nb.deep_research(self.alice, wid, q, FakeDiscovery({}, fail={q}), SourceService(self.ws.ctx), 5)
        self.assertTrue(out2["meta"]["failures"])
        self.assertIn("Searches that failed", out2["content"])

    def test_bad_input_and_other_peoples_notebooks_are_refused(self):
        wid = self.notebook()
        d = FakeDiscovery({})
        self.assertEqual(code_of(self.nb.deep_research, self.alice, wid, "", d, None, 3), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.nb.deep_research, self.alice, wid, "q", d, None, 9), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.nb.deep_research, self.bob, wid, "q", d, None, 3), "NOT_FOUND")
        self.assertEqual(d.calls, [])


class FakeResearch:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def export_bundle(self, principal, bundle_id, fmt):
        self.calls.append(bundle_id)
        if bundle_id in self.fail:
            raise RuntimeError("damaged")
        return {"body": f"# Report {bundle_id}\n\nThe Revolution began in 1789.", "content_type": "text/markdown", "filename": f"research-{bundle_id}.md"}


class Legacy(NbBase):
    def session(self, sid, title, bundle="b1", owner=None):
        now = self.h.clock.iso()
        self.ws.ctx.conn.execute("INSERT INTO research_sessions (id, project_id, owner_user_id, title, question, level, status, options, latest_bundle_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (sid, self.pid, (owner or self.alice).user_id, title, "q?", "topic", "completed", "{}", bundle, now, now))

    def test_each_report_becomes_a_notebook_once_and_failures_are_listed(self):
        self.session("s1", "Revolution", "b1")
        self.session("s2", "Revolution", "b2")
        self.session("s3", "Broken", "b3")
        self.session("s4", "No run yet", None)
        self.assertEqual(self.nb.legacy_status(self.alice)["not_imported"], 3)
        fake = FakeResearch(fail={"b3"})
        out = self.nb.import_legacy(self.alice, fake, SourceService(self.ws.ctx))
        self.assertEqual([x["name"] for x in out["imported"]], ["Imported: Revolution", "Imported: Revolution (2)"])
        self.assertEqual(out["failed"][0]["session_id"], "s3")
        self.assertEqual(out["remaining"], 1)
        srcs = self.h.rows("SELECT title FROM ri_sources WHERE workspace_id = ?", (out["imported"][0]["notebook_id"],))
        self.assertEqual(len(srcs), 1)
        again = self.nb.import_legacy(self.alice, FakeResearch(), SourceService(self.ws.ctx))
        self.assertEqual([x["session_id"] for x in again["imported"]], ["s3"])                 # the two already copied are not copied again
        self.assertEqual(self.nb.legacy_status(self.alice), {"not_imported": 0, "imported": 3})

    def test_only_my_own_reports_are_imported(self):
        self.session("s9", "Bob's", "b9", owner=self.bob)
        out = self.nb.import_legacy(self.alice, FakeResearch(), SourceService(self.ws.ctx))
        self.assertEqual(out["imported"], [])


class Migration(unittest.TestCase):
    def test_migration_fifteen_is_additive(self):
        from pathlib import Path
        sql = (Path(__file__).resolve().parents[1] / "app" / "db" / "migrations" / "0015_notebook_imports.sql").read_text()
        self.assertIn("CREATE TABLE nb_imports", sql)
        self.assertNotRegex(sql.upper(), r"DROP TABLE|ALTER TABLE|DELETE FROM")


if __name__ == "__main__":
    unittest.main()
