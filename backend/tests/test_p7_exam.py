"""Exam Intelligence: ingestion, question extraction, curriculum mapping with corrections, frequency/emphasis wording, blueprint, mock exam, validation, approval, report."""
import json

from p7_support import P7Base, code_of
from test_workspace_uploads import upload

SYLLABUS = """Chapter 1: Cell Biology
- mitochondrion; ribosome; nucleus
Chapter 2: Photosynthesis
- chlorophyll; light reaction
Chapter 3: Genetics
- chromosome; allele
Chapter 4: Ecology
- ecosystem
"""
PAPER_2021 = """Biology Paper 2021
1. Which organelle produces most of the cell's ATP? [1 mark]
A) Nucleus
B) Mitochondrion
C) Ribosome
D) Vacuole
2. Explain the process of photosynthesis and the role of chlorophyll. [5 marks]
3. Describe the structure of a chromosome and an allele. [3 marks]
4. State whether true or false: The nucleus contains genetic material. [1 mark]
"""
PAPER_2022 = """Biology Paper 2022
1. Name the pigment chlorophyll used in the light reaction of photosynthesis. [2 marks]
2. Explain how the mitochondrion releases energy in the cell. [4 marks]
3. Discuss the role of the ribosome and mitochondrion. [5 marks]
4. Define the term ecosystem. [2 marks]
5. Q?? [1 mark]
"""
NOTES = """The mitochondrion is the organelle that releases energy for the cell during respiration. The ribosome is the structure that builds proteins from amino acids inside the cell.
Chlorophyll is the green pigment that absorbs light energy during photosynthesis in the leaf. The chromosome carries genes and is made of DNA wrapped around proteins.
An ecosystem is a community of living things interacting with their physical environment. The nucleus controls the activities of the cell and stores the hereditary information."""


class Exam(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ex = cls.p7.exam
        cls.sid = ex.create_subject(cls.alice, cls.project, name="Biology", level="Grade 10")["id"]
        up = lambda name, text: upload(cls.ws, cls.alice, cls.project, name, text.encode())["asset"]["id"]
        cls.syl = ex.add_document(cls.alice, cls.sid, asset_id=up("syllabus.txt", SYLLABUS), kind="syllabus")
        cls.p21 = ex.add_document(cls.alice, cls.sid, asset_id=up("paper2021.txt", PAPER_2021), kind="past_paper", exam_year=2021)
        cls.p22 = ex.add_document(cls.alice, cls.sid, asset_id=up("paper2022.txt", PAPER_2022), kind="past_paper", exam_year=2022)
        cls.notes = ex.add_document(cls.alice, cls.sid, asset_id=up("notes.txt", NOTES), kind="notes")

    def qs(self, **kw):
        return self.p7.exam.questions(self.alice, self.sid, **kw)["items"]

    def test_curriculum_and_questions_are_extracted_with_types_and_marks(self):
        cur = self.p7.exam.curriculum(self.alice, self.sid)
        self.assertEqual([c["chapter"] for c in cur["chapters"]], ["1. Cell Biology", "2. Photosynthesis", "3. Genetics", "4. Ecology"])
        qs = self.qs()
        self.assertEqual(len(qs), 9)
        first = qs[0]
        self.assertEqual((first["qtype"], first["marks"]), ("mcq", 1.0))
        self.assertEqual(len(first["options"]), 4)
        self.assertIn("ATP", first["text"])
        self.assertEqual(first["mapping"]["chapter"], "1. Cell Biology")

    def test_poor_extraction_is_flagged_not_hidden(self):
        bad = [q for q in self.qs() if q["text"].startswith("Q??")]
        self.assertTrue(bad and bad[0]["low_confidence"] and bad[0]["warnings"])
        self.assertIsNone(bad[0]["mapping_history"][0]["chapter"], "the automatic mapping gave up instead of guessing")
        self.assertTrue(self.qs(low_confidence=True))

    def test_corrections_never_overwrite_the_original_mapping(self):
        q = next(x for x in self.qs() if x["text"].startswith("Q??"))
        self.assertEqual(code_of(self.p7.exam.correct_mapping, self.alice, q["id"], chapter="9. Nonexistent")[0], "VALIDATION_ERROR")
        out = self.p7.exam.correct_mapping(self.alice, q["id"], chapter="4. Ecology", reason="OCR damaged the text")
        self.assertEqual(out["mapping"]["method"], "user")
        self.assertEqual([h["method"] for h in out["mapping_history"]], ["auto", "user"])
        self.assertIsNone(out["mapping_history"][0]["chapter"], "the original automatic result is kept")
        self.assertEqual(code_of(self.p7.exam.correct_mapping, self.bob, q["id"], chapter="4. Ecology")[1], 404)

    def test_analysis_uses_frequency_and_emphasis_wording_and_no_probability(self):
        a = self.p7.exam.analyse(self.alice, self.sid)
        r = a["result"]
        self.assertEqual((r["papers"], r["years"]), (2, [2021, 2022]))
        text = json.dumps({k: v for k, v in r.items() if k != "notice"}).lower()
        for banned in ("probab", "likely", "will appear", "chance", "predict", "forecast"):
            self.assertNotIn(banned, text, "results must never be worded as a prediction")
        self.assertIn("not the probability", r["notice"])
        self.assertEqual(set(r["labels"].values()), {"historical frequency", "heuristic score", "relative emphasis"})
        ch = {c["chapter"]: c for c in r["chapters"]}
        self.assertGreater(ch["2. Photosynthesis"]["questions"], 0)
        self.assertEqual(ch["1. Cell Biology"]["papers_with_topic"], 2)
        self.assertTrue(all(0 <= c["score"] <= 100 for c in r["chapters"]))
        self.assertEqual(len(r["reproducibility"]["input_hash"]), 64)
        self.assertEqual(self.p7.exam.analyse(self.alice, self.sid)["result"]["reproducibility"]["input_hash"], r["reproducibility"]["input_hash"])

    def test_mock_exam_is_a_draft_with_labelled_origins_and_cannot_be_approved_when_invalid(self):
        bp = self.p7.exam.blueprint(self.alice, self.sid, {"type_counts": {"mcq": 3, "fill_blank": 2, "short": 2, "long": 1}, "total_marks": 14, "emphasis": "analysis"})
        self.assertEqual(len(bp["plan"]["slots"]), 8)
        self.assertIn("not a forecast", bp["plan"]["basis"])
        a = self.p7.exam.generate(self.alice, bp["id"], seed=7)
        self.assertEqual(a["status"], "draft")
        self.assertTrue(a["questions"])
        origins = {q["origin"] for q in a["questions"]}
        self.assertTrue(origins <= {"past_paper", "source_derived", "template_generated"})
        self.assertIn("source_derived", origins)
        for q in a["questions"]:
            if q["qtype"] == "mcq" and q["origin"] != "past_paper":
                self.assertIn(q["answer"], [o["label"] for o in q["options"]])
        same = self.p7.exam.generate(self.alice, bp["id"], seed=7)
        self.assertEqual([q["text"] for q in same["questions"]], [q["text"] for q in a["questions"]], "same seed, same paper")
        self.assertEqual(code_of(self.p7.exam.approve, self.alice, a["id"], confirm=True)[0] in ("VALIDATION_FAILED", "CONFIRMATION_REQUIRED"), True)

    def test_validation_catches_duplicates_and_marks_and_approval_needs_confirmation(self):
        bp = self.p7.exam.blueprint(self.alice, self.sid, {"type_counts": {"fill_blank": 2}, "emphasis": "even", "reuse_past_questions": False})
        a = self.p7.exam.generate(self.alice, bp["id"], seed=3)
        qid = a["questions"][0]["id"]
        # a change that duplicates another question and breaks the marks total is blocking
        other = a["questions"][1]["text"] if len(a["questions"]) > 1 else a["questions"][0]["text"]
        if len(a["questions"]) > 1:
            self.p7.exam.edit_question(self.alice, a["id"], qid, {"text": other})
            v = self.p7.exam.validate(self.alice, a["id"])
            self.assertIn("DUPLICATE", {i["code"] for i in v["issues"]})
            self.assertFalse(v["ok"])
            self.assertEqual(code_of(self.p7.exam.approve, self.alice, a["id"], confirm=True)[0], "VALIDATION_FAILED")
            self.p7.exam.edit_question(self.alice, a["id"], qid, {"text": "Fill in the missing word: the _____ is a structure inside the cell that does work."})
        self.p7.exam.edit_question(self.alice, a["id"], qid, {"marks": 2})
        self.assertIn("MARKS_MISMATCH", {i["code"] for i in self.p7.exam.validate(self.alice, a["id"])["issues"]})
        self.p7.exam.edit_question(self.alice, a["id"], qid, {"marks": 1})
        v = self.p7.exam.validate(self.alice, a["id"])
        if v["ok"]:
            self.assertEqual(code_of(self.p7.exam.approve, self.alice, a["id"])[0], "CONFIRMATION_REQUIRED")
            done = self.p7.exam.approve(self.alice, a["id"], confirm=True, acknowledge_warnings=True)
            self.assertEqual(done["status"], "approved")
            self.assertEqual(code_of(self.p7.exam.edit_question, self.alice, a["id"], qid, {"marks": 3})[0], "ASSESSMENT_LOCKED")

    def test_answer_key_distinguishes_source_derived_from_template_generated(self):
        bp = self.p7.exam.blueprint(self.alice, self.sid, {"type_counts": {"short": 2, "mcq": 2}, "emphasis": "even", "reuse_past_questions": False})
        a = self.p7.exam.generate(self.alice, bp["id"], seed=11)
        paper = self.p7.exam.paper_text(self.alice, a["id"], with_key=True)["text"]
        self.assertIn("ANSWER KEY", paper)
        self.assertRegex(paper, r"\[(source_derived|template_generated)")
        self.assertNotIn("ANSWER KEY", self.p7.exam.paper_text(self.alice, a["id"])["text"])

    def test_other_users_see_nothing(self):
        for fn, args in [(self.p7.exam.subject, (self.sid,)), (self.p7.exam.questions, (self.sid,)), (self.p7.exam.analyse, (self.sid,)), (self.p7.exam.curriculum, (self.sid,))]:
            self.assertEqual(code_of(fn, self.bob, *args)[1], 404)
        self.assertEqual(code_of(self.p7.exam.add_document, self.bob, self.sid, asset_id=self.asset["id"], kind="notes")[1], 404)

    def test_exam_report_uses_the_stored_analysis_and_keeps_the_wording(self):
        a = self.p7.exam.analyse(self.alice, self.sid)
        r = self.p7.reports.create(self.alice, self.project, source_kind="exam", source={"analysis_ids": [a["id"]]})
        text = json.dumps(r["content"]).lower()
        self.assertIn("historical frequency", text)
        self.assertIn("not the probability", text)
        self.assertIn("heuristic", text)
        self.assertTrue(r["evidence"])
        md = self.p7.reports.download(self.alice, self.p7.reports.export(self.alice, r["id"], "md")["export_id"])[2].decode()
        self.assertIn("Heuristic score", md)

    def test_document_must_belong_to_the_person_and_project(self):
        self.assertEqual(code_of(self.p7.exam.add_document, self.alice, self.sid, asset_id="nope", kind="notes")[1], 404)
        self.assertEqual(code_of(self.p7.exam.add_document, self.alice, self.sid, asset_id=self.asset["id"], kind="notes")[0], "VALIDATION_ERROR", "a dataset is not an exam document")
