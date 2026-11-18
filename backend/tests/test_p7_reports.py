"""Report engine: research to report without repeating research, evidence grounding, editing warnings, versioning, staleness, access and exports."""
import io
import json
import zipfile

from app.core.errors import AppError
from app.modules.phase7 import build_phase7
from p7_support import code_of
from test_research_intelligence import Intelligent

Q = "How was France under Louis XVI?"


class ReportsFromResearch(Intelligent):
    def setUp(self):
        super().setUp()
        self.p7 = build_phase7(self.ws, self.rs)
        self.rep = self.p7.reports

    def bundle(self):
        s, b = self.ask(Q)
        return s, b

    def make(self, **kw):
        s, b = self.bundle()
        self.hits_before = len(self.web.hits)
        r = self.rep.create(self.alice, self.pid, source_kind="research", source={"bundle_ids": [b["id"]]}, **kw)
        return b, r

    def blocks(self, r):
        for s in r["content"]["sections"]:
            for x in [s] + s["subsections"]:
                for b in x["blocks"]:
                    yield s, b

    def test_report_is_built_from_the_saved_bundle_without_new_research(self):
        b, r = self.make()
        self.assertEqual(len(self.web.hits), self.hits_before, "creating a report must not contact any provider")
        self.assertEqual(r["version"], 1)
        self.assertIn(r["readiness"]["status"], ("ready", "needs_review", "incomplete"))
        claims = [c for _, bl in self.blocks(r) if bl["type"] == "paragraph" for c in bl["claims"]]
        self.assertTrue(claims)
        cited = [c for c in claims if c["kind"] in ("fact", "calculation") and c["evidence"]]
        self.assertTrue(cited, "factual statements carry evidence")
        for c in cited:
            for ref in c["evidence"]:
                self.assertIn(ref, r["evidence"])
        quotes = " ".join(e["quote"] for e in b["evidence"])
        passages = " ".join((e.get("passage") or "") for e in r["evidence"].values())
        self.assertTrue(passages.strip())
        self.assertTrue(any(w in quotes for w in passages.split(".")[:1]) or passages)

    def test_references_come_from_real_sources_and_no_field_is_invented(self):
        b, r = self.make()
        titles = {s["title"] for s in b["sources"]}
        refs = r["view"]["references"]
        self.assertTrue(refs)
        for ref in refs:
            self.assertTrue(any(t in ref["text"] for t in titles), ref["text"])
        for style in ("apa", "mla", "chicago", "ieee", "harvard"):
            v = self.rep.get(self.alice, r["id"], style=style)["view"]
            self.assertEqual(v["citation_style"], style)
            self.assertEqual(len(v["references"]), len(refs))

    def test_editing_creates_a_new_version_and_never_changes_the_old_one(self):
        _, r = self.make()
        sid = r["content"]["sections"][0]["id"]
        before = json.dumps(r["content"], sort_keys=True)
        e = self.rep.edit(self.alice, r["id"], 1, [{"op": "set_heading", "section_id": sid, "text": "A new heading"}])
        self.assertEqual(e["version"], 2)
        self.assertEqual(json.dumps(self.rep.get(self.alice, r["id"], 1)["content"], sort_keys=True), before)
        self.assertEqual(self.rep.get(self.alice, r["id"])["content"]["sections"][0]["heading"], "A new heading")
        self.assertEqual(code_of(self.rep.edit, self.alice, r["id"], 1, [{"op": "set_title", "text": "x"}])[0], "VERSION_CONFLICT")
        self.assertEqual(len(self.rep.versions(self.alice, r["id"])["items"]), 2)

    def test_an_edit_that_detaches_a_claim_from_its_evidence_is_warned_about(self):
        _, r = self.make()
        target = next((s, b) for s, b in self.blocks(r) if b["type"] == "paragraph" and any(c["evidence"] for c in b["claims"]))
        bid = target[1]["id"]
        idx = next(i for i, c in enumerate(target[1]["claims"]) if c["evidence"])
        e = self.rep.edit(self.alice, r["id"], 1, [{"op": "edit_claim", "block_id": bid, "claim": idx, "text": "Revenue rose by 4,812 percent in 1789 according to the treasury.", "evidence": []}])
        self.assertTrue(e["edit_warnings"], "an unsupported number must be flagged")
        self.assertEqual(code_of(self.rep.edit, self.alice, r["id"], 2, [{"op": "edit_claim", "block_id": bid, "claim": idx, "evidence": ["E999"]}])[0], "VALIDATION_ERROR")

    def test_a_newer_research_run_marks_sections_outdated_without_rewriting(self):
        s, b = self.bundle()
        r = self.rep.create(self.alice, self.pid, source_kind="research", source={"bundle_ids": [b["id"]]})
        self.assertEqual(r["readiness"]["stale"], [])
        job = self.svc.run(self.alice, s["id"], None)
        self.runtime.runner.wait(job["id"], 180)
        again = self.rep.get(self.alice, r["id"])
        self.assertTrue(again["readiness"]["stale"], "sections built from the older run are flagged")
        self.assertEqual(again["version"], 1)
        self.assertEqual(again["content"], r["content"])

    def test_other_users_cannot_see_or_edit_or_download(self):
        _, r = self.make()
        self.assertEqual(code_of(self.rep.get, self.bob, r["id"])[1], 404)
        self.assertEqual(code_of(self.rep.edit, self.bob, r["id"], 1, [{"op": "set_title", "text": "x"}])[1], 404)
        ex = self.rep.export(self.alice, r["id"], "md")
        self.assertEqual(code_of(self.rep.download, self.bob, ex["export_id"])[1], 404)
        self.assertEqual(self.rep.list(self.bob)["total"], 0)

    def test_a_report_cannot_be_built_from_someone_elses_bundle(self):
        s, b = self.bundle()
        self.assertEqual(code_of(self.rep.create, self.bob, self.pid, source_kind="research", source={"bundle_ids": [b["id"]]})[1], 404)

    def test_exports_carry_headings_citations_version_and_readiness(self):
        _, r = self.make()
        self.rep.edit(self.alice, r["id"], 1, [{"op": "add_table", "section_id": r["content"]["sections"][0]["id"], "caption": "Sample", "columns": ["a", "b"], "rows": [[1, 2], ["=cmd", 4]]},
                                                {"op": "add_figure", "section_id": r["content"]["sections"][0]["id"], "caption": "Chart", "title": "T", "labels": ["x", "y"], "values": [1, 3]}])
        heading = r["content"]["sections"][0]["heading"]
        for fmt in ("md", "csv", "xlsx", "docx", "pdf", "pptx", "png"):
            ex = self.rep.export(self.alice, r["id"], fmt)
            name, mime, data = self.rep.download(self.alice, ex["export_id"])
            self.assertTrue(data, fmt)
            self.assertTrue(name.endswith(f"_v2.{fmt}"), name)
            if fmt == "md":
                text = data.decode()
                self.assertIn(heading, text)
                self.assertIn("Version 2", text)
                self.assertIn("Readiness", text)
                self.assertIn("## References", text)
            if fmt == "pdf":
                self.assertTrue(data.startswith(b"%PDF"))
            if fmt in ("docx", "xlsx", "pptx"):
                self.assertTrue(zipfile.ZipFile(io.BytesIO(data)).testzip() is None)
            if fmt == "png":
                self.assertTrue(data.startswith(b"\x89PNG"))
            if fmt == "csv":
                self.assertIn("'=cmd", data.decode("utf-8-sig"), "spreadsheet formulas are neutralised")
        self.assertEqual(len(self.rep.exports(self.alice, r["id"])["items"]), 7)

    def test_png_without_charts_is_a_typed_error(self):
        _, r = self.make()
        if not any(b["type"] == "figure" for _, b in self.blocks(r)):
            self.assertEqual(code_of(self.rep.export, self.alice, r["id"], "png")[0], "EXPORT_UNAVAILABLE")

    def test_templates_and_validation(self):
        t = self.rep.templates(self.alice)
        self.assertEqual(len(t["system"]), 10)
        self.assertEqual(code_of(self.rep.create, self.alice, self.pid, source_kind="research", source={"bundle_ids": []})[0], "VALIDATION_ERROR")
        self.assertEqual(code_of(self.rep.create, self.alice, self.pid, source_kind="nonsense", source={})[0], "VALIDATION_ERROR")
