"""R8: reports built from an analysis, the honesty rules (provenance, immutable quotes, no invented references), editing, regeneration, versions, exports read back, isolation and deletion."""
import io
import re
import sys
import unittest

sys.path.insert(0, "tests")
from p10_support import RIHarness
from app.modules.research_intelligence.reasoning.analysis import AnalysisService
from app.modules.research_intelligence.reports import builder, export as exporter
from app.modules.research_intelligence.reports.service import ReportService
from app.modules.workspace.errors import AppError
from test_p10_reasoning import S1, S2, S3


def walk_blocks(report):
    for s in report["sections"]:
        for b in s["blocks"]:
            yield s, b


class Base(unittest.TestCase):
    def setUp(self):
        self.t = RIHarness()
        self.addCleanup(self.t.close)
        self.alice = self.t.user("alice@example.com")
        self.wid = self.t.workspace(self.alice)
        self.src = {}
        for name, text, pub in (("trial.txt", S1, "2020-06-01"), ("cohort.txt", S2, "2018"), ("survey.txt", S3, "2022")):
            s = self.t.add_text(self.alice, self.wid, name, text)
            self.t.ctx().conn.execute("UPDATE ri_sources SET published = ?, authors = ? WHERE id = ?", (pub, '["Ann Author", "Bo Writer"]' if name == "trial.txt" else "[]", s["id"]))
            self.src[name] = s
        self.analysis = self.make_analysis()
        cards = AnalysisService(self.t.ctx()).get(self.alice, self.wid, self.analysis)["result"]["sources"]
        by_id = {c["source_id"]: c["label"] for c in cards}
        self.lab = {name: by_id[s["id"]] for name, s in self.src.items()}                      # labels follow the workspace's source order, not the file names

    def make_analysis(self, **kw):
        made = AnalysisService(self.t.ctx()).create(self.alice, self.wid, **kw)
        self.t.run_jobs()
        return made["id"]

    def rs(self):
        return ReportService(self.t.ctx())

    def make(self, rtype="academic", **kw):
        return self.rs().create(self.alice, self.wid, analysis_id=self.analysis, report_type=rtype, **kw)

    def chunk_text(self, chunk_id):
        return self.t.ctx().conn.execute("SELECT text FROM ri_chunks WHERE id = ?", (chunk_id,)).fetchone()["text"].replace("\n", " ")


class Building(Base):
    def test_every_type_has_its_sections_and_the_notice(self):
        for key, (label, keys) in builder.TYPES.items():
            r = self.make(key)
            self.assertEqual([s["heading"] for s in r["sections"]], [builder.HEADINGS[k] for k in keys], key)
            self.assertIn("not been peer reviewed", r["notice"])
            self.assertEqual(r["type_label"], label)
            self.assertEqual(r["version"], 1)
        self.assertEqual(len(self.rs().types()), 7)

    def test_academic_has_the_suggested_structure_in_order(self):
        r = self.make("academic")
        self.assertEqual([s["heading"] for s in r["sections"]][0], "Abstract")
        self.assertEqual([s["heading"] for s in r["sections"]][-1], "References")
        for h in ("Introduction", "Background", "Literature review", "Methodology comparison", "Findings", "Possible contradictions", "Research gaps", "Discussion", "Conclusion"):
            self.assertIn(h, [s["heading"] for s in r["sections"]])

    def test_provenance_labels_and_exact_quotes(self):
        r = self.make("academic")
        quotes = 0
        for s, b in walk_blocks(r):
            self.assertIn(b["origin"], ("source", "analysis", "author"))
            if b["type"] == "quote":
                quotes += 1
                self.assertEqual(b["origin"], "source")
                self.assertIn(b["text"], self.chunk_text(b["ref"]["chunk_id"]))
                self.assertEqual(b["cite"], [b["ref"]["label"]])
            else:
                self.assertNotEqual(b["origin"], "source")                                   # only exact quotes may claim to be source text
        self.assertGreater(quotes, 5)

    def test_contradiction_is_reported_with_quotes_from_both_sides(self):
        r = self.make("academic")
        sec = next(s for s in r["sections"] if s["id"] == "contradictions")
        labels = {b["ref"]["label"] for b in sec["blocks"] if b["type"] == "quote"}
        self.assertEqual(labels, set(self.lab.values()))
        self.assertTrue(any("Possible contradiction" in (b.get("text") or "") for b in sec["blocks"]))

    def test_references_come_only_from_stored_metadata(self):
        r = self.make("academic")
        refs = {x["label"]: x["text"] for x in r["sources"]}
        titles = {s["title"] for s in self.t.sources().list(self.alice, self.wid)["items"]}
        self.assertEqual(len(refs), 3)
        for text in refs.values():
            self.assertTrue(any(t.rstrip(".") in text for t in titles))
        with_authors = next(t for t in refs.values() if "Author" in t)
        self.assertTrue(with_authors.startswith("Author, A., & Writer, B. (2020)."), with_authors)
        bare = [t for t in refs.values() if "Author" not in t]
        for t in bare:
            self.assertNotIn("n.d.", t)
            self.assertNotIn("Unknown", t)
            self.assertNotIn("doi.org", t)                                                    # no DOI stored, so none shown
        ref_section = next(s for s in r["sections"] if s["id"] == "references")
        self.assertEqual(len(ref_section["blocks"][0]["items"]), 3)

    def test_source_selection_limits_everything_to_the_chosen_sources(self):
        ids = [self.src["trial.txt"]["id"], self.src["cohort.txt"]["id"]]
        r = self.make("academic", source_ids=ids)
        keep = {self.lab["trial.txt"], self.lab["cohort.txt"]}
        self.assertEqual({x["label"] for x in r["sources"]}, keep)
        for s, b in walk_blocks(r):
            self.assertTrue(set(b.get("cite", [])) <= keep)
            if b["type"] == "quote":
                self.assertIn(b["ref"]["label"], keep)
        contra = next(s for s in r["sections"] if s["id"] == "contradictions")
        self.assertIn("No possible contradictions", contra["blocks"][0]["text"])           # the conflict needed S3

    def test_rules(self):
        with self.assertRaises(AppError):
            self.make("poem")
        with self.assertRaises(AppError):
            self.make("academic", source_ids=["nope"])
        with self.assertRaises(AppError):
            self.make("academic", source_ids=[])
        pending = AnalysisService(self.t.ctx()).create(self.alice, self.wid)["id"]
        with self.assertRaises(AppError) as e:
            self.rs().create(self.alice, self.wid, analysis_id=pending, report_type="academic")
        self.assertEqual(e.exception.code, "ANALYSIS_NOT_READY")
        with self.assertRaises(AppError):
            self.rs().create(self.alice, self.wid, analysis_id="x", report_type="academic")

    def test_empty_author_blocks_carry_a_hint_not_text(self):
        r = self.make("academic")
        a = [b for s, b in walk_blocks(r) if b["origin"] == "author"]
        self.assertTrue(a)
        self.assertTrue(all(b["text"] == "" and b["hint"] for b in a))


class Editing(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make("academic")

    def sections(self):
        return self.rs().get(self.alice, self.wid, self.r["id"])["sections"]

    def test_editing_text_relabels_it_as_author_and_versions(self):
        secs = self.sections()
        sec = next(s for s in secs if s["id"] == "abstract")
        sec["blocks"][0]["text"] = "My own abstract."
        out = self.rs().update(self.alice, self.wid, self.r["id"], title="My title", sections=secs)
        b = next(s for s in out["sections"] if s["id"] == "abstract")["blocks"][0]
        self.assertEqual((b["text"], b["origin"], b["edited_from"]), ("My own abstract.", "author", "analysis"))
        self.assertEqual((out["title"], out["version"], out["edited"]), ("My title", 2, True))
        vs = self.rs().versions(self.alice, self.wid, self.r["id"])
        self.assertEqual([(v["version"], v["reason"]) for v in vs], [(1, "edit")])

    def test_no_change_makes_no_version(self):
        out = self.rs().update(self.alice, self.wid, self.r["id"], sections=self.sections())
        self.assertEqual(out["version"], 1)
        self.assertEqual(self.rs().versions(self.alice, self.wid, self.r["id"]), [])

    def test_quotations_and_tables_cannot_be_edited_but_can_be_removed(self):
        secs = self.sections()
        sec = next(s for s in secs if s["id"] == "findings")
        q = next(b for b in sec["blocks"] if b["type"] == "quote")
        q["text"] = "Something the source never said."
        with self.assertRaises(AppError) as e:
            self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        self.assertIn("cannot be edited", e.exception.details[0]["message"])
        secs = self.sections()
        meth = next(s for s in secs if s["id"] == "methodology")
        next(b for b in meth["blocks"] if b["type"] == "table")["rows"][0][1] = "Invented design"
        with self.assertRaises(AppError):
            self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        secs = self.sections()
        sec = next(s for s in secs if s["id"] == "findings")
        n = len(sec["blocks"])
        sec["blocks"] = [b for b in sec["blocks"] if b["type"] != "quote"]
        out = self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        self.assertLess(len(next(s for s in out["sections"] if s["id"] == "findings")["blocks"]), n)

    def test_cannot_add_a_quotation_or_cite_a_foreign_source(self):
        secs = self.sections()
        secs[0]["blocks"].append({"type": "quote", "text": "Fake", "cite": ["S1"]})
        with self.assertRaises(AppError):
            self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        secs = self.sections()
        secs[0]["blocks"].append({"type": "text", "text": "x", "cite": ["S99"]})
        with self.assertRaises(AppError):
            self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)

    def test_adding_text_and_sections_marks_them_as_author(self):
        secs = self.sections()
        secs[0]["blocks"].append({"type": "text", "text": "A note of mine.", "cite": ["S1"], "origin": "source"})                 # a client cannot choose the origin
        secs.append({"heading": "My appendix", "blocks": [{"type": "bullets", "items": ["one", "two"]}]})
        out = self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        added = out["sections"][0]["blocks"][-1]
        self.assertEqual((added["origin"], added["text"], added["cite"]), ("author", "A note of mine.", ["S1"]))
        app = out["sections"][-1]
        self.assertEqual((app["kind"], app["heading"], app["blocks"][0]["origin"]), ("author", "My appendix", "author"))
        self.assertTrue(app["id"].startswith("author-"))

    def test_validation_limits(self):
        for bad in ("x", [{"id": "abstract", "heading": "", "blocks": []}], [{"heading": "h", "blocks": "x"}], [{"heading": "h", "blocks": [1]}], [{"heading": "h", "blocks": [{"type": "text", "text": "x" * 20001}]}]):
            with self.assertRaises(AppError, msg=str(bad)[:40]):
                self.rs().update(self.alice, self.wid, self.r["id"], sections=bad)
        secs = self.sections()
        with self.assertRaises(AppError):
            self.rs().update(self.alice, self.wid, self.r["id"], sections=[secs[0], secs[0]])

    def test_regenerate_keeps_the_authors_work(self):
        secs = self.sections()
        secs[0]["blocks"][0]["text"] = "Hand-written abstract."
        discussion = next(s for s in secs if s["id"] == "discussion")
        mine = next(b for b in discussion["blocks"] if b["origin"] == "author")
        mine["text"] = "My discussion."
        secs.append({"heading": "Appendix", "blocks": [{"type": "text", "text": "Mine."}]})
        removed = next(s for s in secs if s["id"] == "findings")
        removed["blocks"] = [b for b in removed["blocks"] if b["type"] != "quote"]
        self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        out = self.rs().regenerate(self.alice, self.wid, self.r["id"])
        by = {s["id"]: s for s in out["sections"]}
        self.assertEqual(by["abstract"]["blocks"][0]["text"], "Hand-written abstract.")
        self.assertEqual(next(b for b in by["discussion"]["blocks"] if b["origin"] == "author")["text"], "My discussion.")
        self.assertEqual(out["sections"][-1]["heading"], "Appendix")
        self.assertTrue(any(b["type"] == "quote" for b in by["findings"]["blocks"]))        # generated evidence comes back
        self.assertEqual(out["version"], 3)
        reasons = [v["reason"] for v in self.rs().versions(self.alice, self.wid, self.r["id"])]
        self.assertEqual(reasons, ["regenerate", "edit"])

    def test_regenerate_can_discard_author_text_and_change_sources(self):
        secs = self.sections()
        secs[0]["blocks"][0]["text"] = "Mine."
        self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        out = self.rs().regenerate(self.alice, self.wid, self.r["id"], keep_author_text=False, source_ids=[self.src["trial.txt"]["id"], self.src["survey.txt"]["id"]])
        self.assertNotEqual(out["sections"][0]["blocks"][0]["text"], "Mine.")
        self.assertEqual({x["label"] for x in out["sources"]}, {self.lab["trial.txt"], self.lab["survey.txt"]})

    def test_restore_an_earlier_version(self):
        secs = self.sections()
        original = secs[0]["blocks"][0]["text"]
        secs[0]["blocks"][0]["text"] = "Changed."
        self.rs().update(self.alice, self.wid, self.r["id"], title="Renamed", sections=secs)
        vid = self.rs().versions(self.alice, self.wid, self.r["id"])[0]["id"]
        out = self.rs().restore(self.alice, self.wid, self.r["id"], vid)
        self.assertEqual(out["sections"][0]["blocks"][0]["text"], original)
        self.assertEqual(out["title"], self.r["title"])
        self.assertEqual(out["version"], 3)
        with self.assertRaises(AppError):
            self.rs().restore(self.alice, self.wid, self.r["id"], 99999)

    def test_version_history_is_capped(self):
        for i in range(25):
            secs = self.sections()
            secs[0]["blocks"][0]["text"] = f"edit {i}"
            self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        self.assertEqual(len(self.rs().versions(self.alice, self.wid, self.r["id"])), 20)

    def test_regenerate_fails_clearly_when_the_analysis_is_gone(self):
        self.t.ctx().conn.execute("DELETE FROM ri_analyses WHERE id = ?", (self.analysis,))
        with self.assertRaises(AppError) as e:
            self.rs().regenerate(self.alice, self.wid, self.r["id"])
        self.assertEqual(e.exception.code, "ANALYSIS_GONE")


def norm(s):
    return re.sub(r"\s+", " ", s)


class Exports(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make("academic", title="Canopy and heat")

    def exp(self, fmt):
        return self.rs().export(self.alice, self.wid, self.r["id"], fmt)

    def quotes(self):
        return [b["text"] for s, b in walk_blocks(self.rs().get(self.alice, self.wid, self.r["id"])) if b["type"] == "quote"]

    def test_markdown_keeps_notice_quotes_citations_and_references(self):
        data, ctype, name = self.exp("md")
        text = data.decode()
        self.assertEqual((ctype.split(";")[0], name), ("text/markdown", "Canopy_and_heat.md"))
        self.assertTrue(text.startswith("# Canopy and heat"))
        self.assertIn("not been peer reviewed", text)
        for q in self.quotes():
            self.assertIn(f"“{q}”", text)
        self.assertRegex(text, r"\[S1\]")
        self.assertIn("## References", text)
        self.assertIn("Source quote", text)
        self.assertIn("Tool analysis", text)
        for x in self.r["sources"]:
            self.assertIn(x["text"], text)

    def test_docx_reads_back(self):
        from docx import Document
        data, ctype, name = self.exp("docx")
        d = Document(io.BytesIO(data))
        body = norm("\n".join(p.text for p in d.paragraphs))
        self.assertTrue(name.endswith(".docx"))
        self.assertIn("Canopy and heat", body)
        self.assertIn("not been peer reviewed", body)
        for q in self.quotes():
            self.assertIn(norm(q), body)
        self.assertGreaterEqual(len(d.tables), 2)                                         # methods table and evidence table
        self.assertIn("Design", [c.text for c in d.tables[0].rows[0].cells])
        for x in self.r["sources"]:
            self.assertIn(norm(x["text"]), body.replace("[" + x["label"] + "] ", ""))

    def test_pdf_reads_back(self):
        from pypdf import PdfReader
        data, ctype, name = self.exp("pdf")
        self.assertTrue(data.startswith(b"%PDF"))
        text = norm(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages))
        self.assertIn("Canopy and heat", text)
        self.assertIn("not been peer reviewed", text)
        for q in self.quotes():
            self.assertIn(norm(q)[:60], text)
        self.assertIn("References", text)
        self.assertIn("Source quote", text)

    def test_pptx_reads_back(self):
        from pptx import Presentation
        data, ctype, name = self.exp("pptx")
        prs = Presentation(io.BytesIO(data))
        texts = [norm(sh.text_frame.text) for s in prs.slides for sh in s.shapes if sh.has_text_frame]
        joined = " ".join(texts)
        self.assertGreater(len(prs.slides), 6)
        self.assertIn("Canopy and heat", joined)
        self.assertIn("not been peer reviewed", joined)
        self.assertIn("References", joined)
        self.assertIn(norm(self.quotes()[0])[:60], joined)

    def test_exports_include_the_authors_edits_and_survive_unusual_text(self):
        secs = self.rs().get(self.alice, self.wid, self.r["id"])["sections"]
        secs[0]["blocks"][0]["text"] = "Edge cases: ≥ α → 日本語 <b>&</b> “quoted” — done."
        self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        for fmt in ("md", "docx", "pdf", "pptx"):
            data, _, _ = self.exp(fmt)
            self.assertGreater(len(data), 500, fmt)
        self.assertIn("日本語", self.exp("md")[0].decode())
        from docx import Document
        self.assertIn("日本語", "\n".join(p.text for p in Document(io.BytesIO(self.exp("docx")[0])).paragraphs))
        from pypdf import PdfReader
        self.assertIn("Edge cases", norm(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(self.exp("pdf")[0])).pages)))

    def test_unknown_format_and_filename_safety(self):
        with self.assertRaises(AppError):
            self.exp("exe")
        self.assertEqual(exporter.safe_filename("../../etc/passwd; rm -rf", "pdf"), "etcpasswd_rm_-rf.pdf")
        self.assertEqual(exporter.safe_filename("", "md"), "report.md")


class MissingLibrary(Base):
    def test_a_missing_export_library_is_a_clear_503_not_a_crash(self):
        r = self.make("academic")
        original = exporter.to_pdf
        def broken(_report):
            raise ImportError("No module named reportlab")
        exporter.to_pdf = broken
        try:
            with self.assertRaises(AppError) as cm:
                self.rs().export(self.alice, self.wid, r["id"], "pdf")
        finally:
            exporter.to_pdf = original
        self.assertEqual(cm.exception.status_code, 503)
        self.assertIn("reportlab", cm.exception.message)
        self.assertNotIn("Traceback", cm.exception.message)
        self.assertGreater(len(self.rs().export(self.alice, self.wid, r["id"], "md")[0]), 100)


class Security(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make("academic")
        self.bob = self.t.user("bob@example.com")
        self.bw = self.t.workspace(self.bob, "Bobs")

    def test_other_users_cannot_touch_reports(self):
        rs = self.rs()
        for fn in (lambda: rs.get(self.bob, self.wid, self.r["id"]), lambda: rs.get(self.bob, self.bw, self.r["id"]), lambda: rs.export(self.bob, self.wid, self.r["id"], "md"),
                   lambda: rs.update(self.bob, self.wid, self.r["id"], title="x"), lambda: rs.delete(self.bob, self.wid, self.r["id"]), lambda: rs.regenerate(self.bob, self.wid, self.r["id"]),
                   lambda: rs.versions(self.bob, self.wid, self.r["id"]), lambda: rs.create(self.bob, self.bw, analysis_id=self.analysis, report_type="academic"),
                   lambda: rs.create(self.bob, self.wid, analysis_id=self.analysis, report_type="academic")):
            with self.assertRaises(AppError):
                fn()
        self.assertEqual(rs.list(self.bob, self.bw), [])
        self.assertEqual(rs.list_all(self.bob), [])
        self.assertEqual([x["id"] for x in rs.list_all(self.alice)], [self.r["id"]])

    def test_archived_workspace_cannot_create_reports(self):
        self.t.ctx().conn.execute("UPDATE rw_workspaces SET status = 'archived' WHERE id = ?", (self.wid,))
        with self.assertRaises(AppError) as e:
            self.make("academic")
        self.assertEqual(e.exception.code, "WORKSPACE_ARCHIVED")
        self.assertTrue(self.rs().get(self.alice, self.wid, self.r["id"]))                 # reading and exporting stay possible
        self.assertTrue(self.rs().export(self.alice, self.wid, self.r["id"], "md"))

    def test_deleting_a_source_redacts_quotes_and_history_but_keeps_the_authors_text(self):
        secs = self.rs().get(self.alice, self.wid, self.r["id"])["sections"]
        secs[0]["blocks"][0]["text"] = "My own abstract."
        self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        gone = self.src["survey.txt"]
        self.t.sources().delete(self.alice, self.wid, gone["id"])
        out = self.rs().get(self.alice, self.wid, self.r["id"])
        dump = str(out)
        self.assertNotIn("did not significantly reduce", dump)
        self.assertEqual({x["label"] for x in out["sources"]}, {self.lab["trial.txt"], self.lab["cohort.txt"]})
        self.assertEqual(out["sections"][0]["blocks"][0]["text"], "My own abstract.")
        refs = next(s for s in out["sections"] if s["id"] == "references")["blocks"][0]["items"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(self.rs().versions(self.alice, self.wid, self.r["id"]), [])
        self.assertNotIn("did not significantly reduce", self.rs().export(self.alice, self.wid, self.r["id"], "md")[0].decode())
        with self.assertRaises(AppError) as e:
            self.rs().regenerate(self.alice, self.wid, self.r["id"])
        self.assertEqual(e.exception.code, "ANALYSIS_GONE")

    def test_deleting_the_workspace_removes_reports_and_versions(self):
        from app.modules.researchspace.service import ResearchSpaceService
        secs = self.rs().get(self.alice, self.wid, self.r["id"])["sections"]
        secs[0]["blocks"][0]["text"] = "x"
        self.rs().update(self.alice, self.wid, self.r["id"], sections=secs)
        ResearchSpaceService(self.t.ctx()).delete(self.alice, self.wid)
        c = self.t.ctx().conn
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_reports").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_report_versions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
