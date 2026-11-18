"""Exam Intelligence service: subjects, documents, extracted questions, curriculum, mapping (append-only corrections), analysis, blueprints, mock exams, validation and approval.

What is read from the person's documents is data, never instructions: nothing here sends document text to an AI model or runs it. A generated paper is always a draft until a person approves it."""
from __future__ import annotations

import hashlib
import random
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from ...core.errors import AppError
from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page, sha256_text
from ..reports import composer as C
from ..reports import evidence as ev
from . import engine as X

DOC_KINDS = ("syllabus", "curriculum", "notes", "slides", "past_paper", "other")
QTYPES = ("mcq", "short", "long", "true_false", "fill_blank")
DEFAULT_MARKS = {"mcq": 1, "true_false": 1, "fill_blank": 1, "short": 3, "long": 5}
LOW_CONF = 0.6


class ExamService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver) -> None:
        super().__init__(ctx, ws)
        self.access = access

    # ------------------------------------------------------------------ subjects and documents
    def create_subject(self, principal: Principal, project_id: str, *, name: Any, level: Any = None) -> Dict[str, Any]:
        self.project(principal, project_id, writable=True)
        nm, lv = clean(name, "name", maximum=120), clean(level, "level", maximum=60, required=False)
        if self.conn.execute("SELECT 1 FROM exam_subjects WHERE owner_user_id = ? AND project_id = ? AND name = ? AND COALESCE(level,'') = ?", (principal.user_id, project_id, nm, lv or "")).fetchone():
            raise conflict("SUBJECT_EXISTS", "You already have a subject with this name and level in this project.")
        sid = new_id()
        with self.conn:
            self.conn.execute("INSERT INTO exam_subjects (id, project_id, owner_user_id, name, level, created_at) VALUES (?,?,?,?,?,?)", (sid, project_id, principal.user_id, nm, lv, self.now()))
        return self.subject(principal, sid)

    def _subject(self, principal: Principal, subject_id: Any, writable: bool = False):
        row = self.conn.execute("SELECT * FROM exam_subjects WHERE id = ? AND owner_user_id = ?", (subject_id, principal.user_id)).fetchone() if isinstance(subject_id, str) and len(subject_id) <= 64 else None
        if row is None:
            raise not_found("subject")
        self.project(principal, row["project_id"], writable=writable)
        return row

    def subject(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        s = self._subject(principal, subject_id)
        c = lambda sql: self.conn.execute(sql, (subject_id,)).fetchone()[0]  # noqa: E731
        return {"id": s["id"], "project_id": s["project_id"], "name": s["name"], "level": s["level"], "created_at": s["created_at"],
                "counts": {"documents": c("SELECT COUNT(*) FROM exam_documents WHERE subject_id = ?"), "questions": c("SELECT COUNT(*) FROM exam_questions WHERE subject_id = ?"), "curriculum_rows": c("SELECT COUNT(*) FROM exam_curriculum WHERE subject_id = ?")}}

    def subjects(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self.project(principal, project_id)
        rows = self.conn.execute("SELECT id FROM exam_subjects WHERE owner_user_id = ? AND project_id = ? ORDER BY name", (principal.user_id, project_id)).fetchall()
        return {"items": [self.subject(principal, r["id"]) for r in rows]}

    def add_document(self, principal: Principal, subject_id: str, *, asset_id: Any, kind: Any, title: Any = None, exam_year: Any = None) -> Dict[str, Any]:
        self.limit("p7_exam_analyse:user", principal)
        s = self._subject(principal, subject_id, writable=True)
        kind = choice(kind, "kind", DOC_KINDS)
        if exam_year is not None and (isinstance(exam_year, bool) or not isinstance(exam_year, int) or not 1900 <= exam_year <= 2100):
            raise invalid("exam_year", "Use a four-digit year.")
        asset, version, _row, doc = self.ws.data.document_json(principal, asset_id)      # authorisation: the asset must belong to this person
        if asset["project_id"] != s["project_id"]:
            raise conflict("PROJECT_MISMATCH", "The document must be in the same project as the subject.")
        if self.conn.execute("SELECT 1 FROM exam_documents WHERE subject_id = ? AND asset_id = ? AND version_id = ?", (subject_id, asset["id"], version["id"])).fetchone():
            raise conflict("ALREADY_ADDED", "This version of the document is already part of the subject.")
        pages = doc["pages"]
        confs = [p["ocr_confidence"] for p in pages if p.get("ocr_confidence") is not None]
        warnings = [w.get("message") or str(w) for w in doc.get("warnings", [])]
        quality = {"pages": len(pages), "ocr_pages": len(confs), "ocr_mean_confidence": round(sum(confs) / len(confs), 1) if confs else None, "ocr_min_confidence": min(confs) if confs else None,
                   "warnings": warnings[:20]}
        if confs and quality["ocr_mean_confidence"] < 85:
            quality["warnings"].append(f"OCR confidence is {quality['ocr_mean_confidence']}% on average: check extracted wording against the original.")
        if any(p.get("warning") for p in pages):
            quality["warnings"].append("Some pages were read with a warning (see the page list in the document view).")
        did = new_id()
        structure = {"headings": [{"text": h.get("text"), "level": h.get("level"), "page": h.get("page")} for h in doc.get("headings", [])[:200]], "page_count": len(pages)}
        counts = {"questions": 0, "curriculum_rows": 0}
        with self.conn:
            self.conn.execute("INSERT INTO exam_documents (id, project_id, owner_user_id, subject_id, asset_id, version_id, kind, title, exam_year, status, quality, structure, analysed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (did, s["project_id"], principal.user_id, subject_id, asset["id"], version["id"], kind, clean(title, "title", maximum=160, required=False) or asset["name"], exam_year, "analysed", "{}", jdump(structure), self.now()))
            if kind in ("syllabus", "curriculum"):
                rows = X.parse_curriculum(pages, doc.get("headings", []))
                for r in rows:
                    self.conn.execute("INSERT INTO exam_curriculum (id, subject_id, document_id, chapter, topic, subtopic, keywords, source_ref) VALUES (?,?,?,?,?,?,?,?)",
                                      (new_id(), subject_id, did, r["chapter"], r["topic"], r["subtopic"], jdump(r["keywords"]), f"{asset['name']} p{r['page']}" if r.get("page") else asset["name"]))
                counts["curriculum_rows"] = len(rows)
                if not rows:
                    quality["warnings"].append("No chapters or topics were found in this document.")
                self._remap(subject_id, principal)
            elif kind == "past_paper":
                qs = X.parse_questions(pages)
                for q in qs:
                    qid = new_id()
                    self.conn.execute("INSERT INTO exam_questions (id, subject_id, document_id, owner_user_id, number, text, qtype, marks, options, answer_text, page, confidence, warnings, source_ref, text_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (qid, subject_id, did, principal.user_id, q["number"], q["text"], q["qtype"], q["marks"], jdump(q["options"]) if q["options"] else None, None, q["page"], q["confidence"], jdump(q["warnings"]),
                                       f"{asset['name']} p{q['page']} Q{q['number']}", sha256_text(q["text"])))
                    self._auto_map(subject_id, qid, q["text"], principal)
                counts["questions"] = len(qs)
                if not qs:
                    quality["warnings"].append("No numbered questions were found. Check that the paper uses numbering such as '1.' or 'Q1.'.")
            quality["counts"] = counts
            status = "analysed_with_warnings" if quality["warnings"] else "analysed"
            self.conn.execute("UPDATE exam_documents SET quality = ?, status = ? WHERE id = ?", (jdump(quality), status, did))
        self.audit(E.EXAM_ANALYSED, principal, did, kind=kind, **counts)
        return self.document(principal, did)

    def _doc_row(self, principal: Principal, document_id: Any):
        row = self.conn.execute("SELECT * FROM exam_documents WHERE id = ? AND owner_user_id = ?", (document_id, principal.user_id)).fetchone() if isinstance(document_id, str) and len(document_id) <= 64 else None
        if row is None:
            raise not_found("document")
        return row

    def document(self, principal: Principal, document_id: str) -> Dict[str, Any]:
        d = self._doc_row(principal, document_id)
        return {"id": d["id"], "subject_id": d["subject_id"], "asset_id": d["asset_id"], "kind": d["kind"], "title": d["title"], "exam_year": d["exam_year"], "status": d["status"],
                "quality": jload(d["quality"], {}), "structure": jload(d["structure"], {}), "analysed_at": d["analysed_at"]}

    def documents(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        self._subject(principal, subject_id)
        return {"items": [self.document(principal, r["id"]) for r in self.conn.execute("SELECT id FROM exam_documents WHERE subject_id = ? AND owner_user_id = ? ORDER BY analysed_at", (subject_id, principal.user_id))]}

    # ------------------------------------------------------------------ curriculum and mapping
    def _curriculum_rows(self, subject_id: str) -> List[Dict[str, Any]]:
        return [{"id": r["id"], "chapter": r["chapter"], "topic": r["topic"], "subtopic": r["subtopic"], "keywords": jload(r["keywords"], []), "source_ref": r["source_ref"]}
                for r in self.conn.execute("SELECT * FROM exam_curriculum WHERE subject_id = ? ORDER BY rowid", (subject_id,))]

    def curriculum(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        self._subject(principal, subject_id)
        rows = self._curriculum_rows(subject_id)
        chapters: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            chapters.setdefault(r["chapter"], [])
            if r["topic"]:
                chapters[r["chapter"]].append({"id": r["id"], "topic": r["topic"]})
        return {"chapters": [{"chapter": c, "topics": t} for c, t in chapters.items()], "rows": len(rows)}

    def _auto_map(self, subject_id: str, question_id: str, text: str, principal: Principal) -> None:
        m = X.map_question(text, self._curriculum_rows(subject_id))
        self.conn.execute("INSERT INTO exam_mappings (question_id, curriculum_id, chapter, topic, subtopic, confidence, method, reason, actor_user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (question_id, m["curriculum_id"], m["chapter"], m["topic"], None, m["confidence"], "auto", m["reason"], principal.user_id, self.now()))

    def _remap(self, subject_id: str, principal: Principal) -> None:
        """New curriculum rows: re-run the automatic mapping for questions whose current mapping is automatic. A person's correction is never touched."""
        for q in self.conn.execute("SELECT id, text FROM exam_questions WHERE subject_id = ?", (subject_id,)).fetchall():
            cur = self._current(q["id"])
            if cur is None or cur["method"] == "auto":
                self._auto_map(subject_id, q["id"], q["text"], principal)

    def _current(self, question_id: str):
        return self.conn.execute("SELECT * FROM exam_mappings WHERE question_id = ? ORDER BY id DESC LIMIT 1", (question_id,)).fetchone()

    def _question_view(self, q) -> Dict[str, Any]:
        cur = self._current(q["id"])
        hist = self.conn.execute("SELECT id, method, chapter, topic, confidence, reason, actor_user_id, created_at FROM exam_mappings WHERE question_id = ? ORDER BY id", (q["id"],)).fetchall()
        return {"id": q["id"], "document_id": q["document_id"], "number": q["number"], "text": q["text"], "qtype": q["qtype"], "marks": q["marks"], "options": jload(q["options"]), "page": q["page"],
                "confidence": q["confidence"], "low_confidence": q["confidence"] < LOW_CONF, "warnings": jload(q["warnings"], []), "source_ref": q["source_ref"],
                "mapping": {"chapter": cur["chapter"], "topic": cur["topic"], "confidence": cur["confidence"], "method": cur["method"], "reason": cur["reason"]} if cur else None,
                "mapping_history": [{"method": h["method"], "chapter": h["chapter"], "topic": h["topic"], "confidence": h["confidence"], "reason": h["reason"], "at": h["created_at"]} for h in hist]}

    def questions(self, principal: Principal, subject_id: str, *, document_id: Any = None, unmapped: bool = False, low_confidence: bool = False, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        self._subject(principal, subject_id)
        lim, off = page(limit, offset, maximum=200, default=50)
        sql, args = "SELECT * FROM exam_questions WHERE subject_id = ? AND owner_user_id = ?", [subject_id, principal.user_id]
        if document_id:
            sql += " AND document_id = ?"
            args.append(document_id)
        rows = [self._question_view(r) for r in self.conn.execute(sql + " ORDER BY rowid", args).fetchall()]
        if unmapped:
            rows = [r for r in rows if not (r["mapping"] and r["mapping"]["chapter"])]
        if low_confidence:
            rows = [r for r in rows if r["low_confidence"]]
        return {"items": rows[off:off + lim], "total": len(rows), "limit": lim, "offset": off}

    def correct_mapping(self, principal: Principal, question_id: str, *, chapter: Any, topic: Any = None, reason: Any = None) -> Dict[str, Any]:
        q = self.conn.execute("SELECT * FROM exam_questions WHERE id = ? AND owner_user_id = ?", (question_id, principal.user_id)).fetchone() if isinstance(question_id, str) and len(question_id) <= 64 else None
        if q is None:
            raise not_found("question")
        self._subject(principal, q["subject_id"], writable=True)
        ch = clean(chapter, "chapter", maximum=120, required=False)
        tp = clean(topic, "topic", maximum=140, required=False)
        row = None
        if ch:
            known = [r for r in self._curriculum_rows(q["subject_id"]) if r["chapter"] == ch and (tp is None or r["topic"] == tp)]
            if not known:
                raise invalid("chapter", "Choose a chapter (and topic) from the subject's curriculum, or leave the chapter empty to mark the question as unmapped.")
            row = known[0]
        with self.conn:
            self.conn.execute("INSERT INTO exam_mappings (question_id, curriculum_id, chapter, topic, subtopic, confidence, method, reason, actor_user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (question_id, row["id"] if row else None, ch, tp, None, 1.0 if ch else 0.0, "user", clean(reason, "reason", maximum=300, required=False) or "Corrected by the person who owns the subject.", principal.user_id, self.now()))
        self.audit(E.EXAM_CORRECTED, principal, question_id)
        return self._question_view(q)

    # ------------------------------------------------------------------ analysis
    def analyse(self, principal: Principal, subject_id: str, params: Any = None) -> Dict[str, Any]:
        self.limit("p7_exam_analyse:user", principal)
        s = self._subject(principal, subject_id, writable=True)
        params = params if isinstance(params, dict) else {}
        year_from, year_to = params.get("year_from"), params.get("year_to")
        for k, v in (("year_from", year_from), ("year_to", year_to)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise invalid(k, "Use a four-digit year.")
        papers = [{"id": d["id"], "year": d["exam_year"], "title": d["title"]} for d in self.conn.execute("SELECT * FROM exam_documents WHERE subject_id = ? AND kind = 'past_paper' ORDER BY exam_year, title", (subject_id,))
                  if (year_from is None or (d["exam_year"] or 0) >= year_from) and (year_to is None or (d["exam_year"] or 9999) <= year_to)]
        if not papers:
            raise invalid("subject", "Add at least one past paper before analysing.")
        ids = {p["id"] for p in papers}
        yr = {p["id"]: p["year"] for p in papers}
        qs = []
        for q in self.conn.execute("SELECT * FROM exam_questions WHERE subject_id = ?", (subject_id,)).fetchall():
            if q["document_id"] not in ids:
                continue
            cur = self._current(q["id"])
            qs.append({"id": q["id"], "document_id": q["document_id"], "year": yr[q["document_id"]], "marks": q["marks"], "chapter": cur["chapter"] if cur else None, "topic": cur["topic"] if cur else None})
        chapters = list(dict.fromkeys(r["chapter"] for r in self._curriculum_rows(subject_id)))
        result = X.analyse(qs, chapters, papers)
        low = self.conn.execute("SELECT COUNT(*) FROM exam_questions WHERE subject_id = ? AND confidence < ?", (subject_id, LOW_CONF)).fetchone()[0]
        corrections = self.conn.execute("SELECT COUNT(*) FROM exam_mappings m JOIN exam_questions q ON q.id = m.question_id WHERE q.subject_id = ? AND m.method = 'user'", (subject_id,)).fetchone()[0]
        result["inputs"] = {"papers": papers, "low_confidence_questions": low, "user_corrections": corrections, "curriculum_present": bool(chapters)}
        result["warnings"] = ([] if chapters else ["No syllabus was added, so questions could not be grouped by chapter."]) + ([f"{low} question(s) were extracted with low confidence."] if low else []) \
            + ([f"{result['unmapped_questions']} question(s) could not be matched to a chapter."] if result["unmapped_questions"] else []) + ([f"Only {len(papers)} paper(s) were analysed; patterns from so few papers are weak."] if len(papers) < 3 else [])
        aid = new_id()
        result["reproducibility"] = {"heuristic": X.HEURISTIC_VERSION, "input_hash": sha256_text(jdump(sorted((q["id"], q["chapter"], q["topic"], q["marks"], q["year"]) for q in qs)))}
        with self.conn:
            self.conn.execute("INSERT INTO exam_analyses (id, subject_id, owner_user_id, project_id, params, result, created_at) VALUES (?,?,?,?,?,?,?)", (aid, subject_id, principal.user_id, s["project_id"], jdump(params), jdump(result), self.now()))
        self.audit(E.EXAM_ANALYSED, principal, aid, papers=len(papers), questions=len(qs))
        return self.get_analysis(principal, aid)

    def get_analysis(self, principal: Principal, analysis_id: str) -> Dict[str, Any]:
        acc = self.access.resolve(principal, "exam_analysis", analysis_id, "view")
        r = self.conn.execute("SELECT * FROM exam_analyses WHERE id = ?", (analysis_id,)).fetchone()
        return {"id": r["id"], "subject_id": r["subject_id"], "project_id": r["project_id"], "params": jload(r["params"], {}), "result": jload(r["result"], {}), "created_at": r["created_at"], "your_access": acc.level}

    def analyses(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        self._subject(principal, subject_id)
        return {"items": [{"id": r["id"], "created_at": r["created_at"], "params": jload(r["params"], {})} for r in self.conn.execute("SELECT id, created_at, params FROM exam_analyses WHERE subject_id = ? AND owner_user_id = ? ORDER BY created_at DESC", (subject_id, principal.user_id))]}

    # ------------------------------------------------------------------ blueprint
    def blueprint(self, principal: Principal, subject_id: str, spec: Any) -> Dict[str, Any]:
        s = self._subject(principal, subject_id, writable=True)
        spec = spec if isinstance(spec, dict) else {}
        counts = spec.get("type_counts") or {"mcq": 10, "short": 5, "long": 2}
        if not isinstance(counts, dict) or not counts or any(k not in QTYPES or isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 60 for k, v in counts.items()) or sum(counts.values()) < 1 or sum(counts.values()) > 100:
            raise invalid("type_counts", "Give a count (0 to 60) for question types from: " + ", ".join(QTYPES) + ".")
        mpt = {**DEFAULT_MARKS, **(spec.get("marks_per_type") or {})}
        if any(k not in QTYPES or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 50 for k, v in mpt.items()):
            raise invalid("marks_per_type", "Marks per question must be between 0 and 50.")
        mix = spec.get("difficulty_mix") or {"easy": 30, "medium": 50, "hard": 20}
        if not isinstance(mix, dict) or set(mix) != {"easy", "medium", "hard"} or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in mix.values()) or abs(sum(mix.values()) - 100) > 0.01:
            raise invalid("difficulty_mix", "Give easy, medium and hard percentages that add up to 100.")
        emphasis = choice(spec.get("emphasis", "prediction" if spec.get("prediction_id") else "analysis" if self.conn.execute("SELECT 1 FROM exam_analyses WHERE subject_id = ?", (subject_id,)).fetchone() else "even"), "emphasis", ("analysis", "even", "prediction"))
        curriculum = list(dict.fromkeys(r["chapter"] for r in self._curriculum_rows(subject_id)))
        chapters = spec.get("chapters") or curriculum
        if not chapters:
            raise invalid("chapters", "Add a syllabus first, or name the chapters to cover.")
        if not isinstance(chapters, list) or any(c not in curriculum for c in chapters) and curriculum:
            raise invalid("chapters", "Choose chapters from the subject's curriculum.")
        weights: Dict[str, float] = {c: 1.0 for c in chapters}
        basis, warnings = "even", []
        if emphasis == "prediction":
            pr = self.conn.execute("SELECT * FROM exam_predictions WHERE id = ? AND subject_id = ? AND owner_user_id = ?", (spec.get("prediction_id"), subject_id, principal.user_id)).fetchone() if isinstance(spec.get("prediction_id"), str) else None
            if pr is None:
                raise invalid("prediction_id", "Choose a prediction of this subject, or choose 'even' emphasis.")
            score = {r["chapter"]: r["score"] for r in jload(pr["result"], {}).get("chapters", [])}
            weights = {c: 1.0 + score.get(c, 0.0) for c in chapters}
            basis = f"relative emphasis from prediction {pr['id'][:8]} (an estimate from past papers, not the probability of any topic being asked)"
        elif emphasis == "analysis":
            aid = spec.get("analysis_id")
            row = (self.conn.execute("SELECT * FROM exam_analyses WHERE id = ? AND subject_id = ?", (aid, subject_id)) if aid else self.conn.execute("SELECT * FROM exam_analyses WHERE subject_id = ? ORDER BY created_at DESC LIMIT 1", (subject_id,))).fetchone()
            if row is None:
                raise invalid("analysis_id", "Run an analysis first, or choose 'even' emphasis.")
            res = jload(row["result"], {})
            score = {r["chapter"]: r["score"] for r in res["chapters"]}
            weights = {c: 1.0 + score.get(c, 0.0) for c in chapters}                               # +1 keeps never-asked chapters in the paper
            basis = f"relative emphasis from analysis {row['id'][:8]} (heuristic score, historical frequency; not a forecast)"
        total_target = spec.get("total_marks")
        computed = sum(counts[t] * mpt[t] for t in counts)
        if total_target is not None and (isinstance(total_target, bool) or not isinstance(total_target, (int, float)) or abs(total_target - computed) > 0.01):
            warnings.append(f"The counts and marks per question add up to {computed:g} marks, not {total_target}. Change the counts or the marks per question.")
        slots = []
        for t in QTYPES:
            slots += [{"qtype": t, "marks": mpt[t]} for _ in range(counts.get(t, 0))]
        tot_w = sum(weights.values())
        assigned = {c: 0 for c in chapters}
        for i, sl in enumerate(slots):
            pick = max(chapters, key=lambda c: (weights[c] / tot_w * (i + 1) - assigned[c], -chapters.index(c)))
            sl["chapter"] = pick
            assigned[pick] += 1
        by_marks = sorted(range(len(slots)), key=lambda i: (slots[i]["marks"], i))
        cut_e, cut_m = round(len(slots) * mix["easy"] / 100), round(len(slots) * (mix["easy"] + mix["medium"]) / 100)
        for rank, i in enumerate(by_marks):
            slots[i]["difficulty"] = "easy" if rank < cut_e else ("medium" if rank < cut_m else "hard")
        if len(chapters) > len(slots):
            warnings.append(f"{len(chapters) - len(set(s['chapter'] for s in slots))} chapter(s) get no question because the paper has only {len(slots)} questions.")
        plan = {"slots": slots, "chapters": chapters, "weights": {c: round(w / tot_w, 4) for c, w in weights.items()}, "basis": basis, "total_marks": computed, "target_marks": total_target, "warnings": warnings, "difficulty_mix": mix}
        bid = new_id()
        with self.conn:
            self.conn.execute("INSERT INTO exam_blueprints (id, subject_id, project_id, owner_user_id, spec, plan, created_at) VALUES (?,?,?,?,?,?,?)",
                              (bid, subject_id, s["project_id"], principal.user_id, jdump({"type_counts": counts, "marks_per_type": mpt, "difficulty_mix": mix, "emphasis": emphasis, "chapters": chapters, "total_marks": total_target,
                                                                                         "reuse_past_questions": spec.get("reuse_past_questions", True), **({"prediction_id": spec["prediction_id"]} if emphasis == "prediction" else {})}), jdump(plan), self.now()))
        return self.get_blueprint(principal, bid)

    def get_blueprint(self, principal: Principal, blueprint_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM exam_blueprints WHERE id = ? AND owner_user_id = ?", (blueprint_id, principal.user_id)).fetchone() if isinstance(blueprint_id, str) and len(blueprint_id) <= 64 else None
        if r is None:
            raise not_found("blueprint")
        return {"id": r["id"], "subject_id": r["subject_id"], "spec": jload(r["spec"], {}), "plan": jload(r["plan"], {}), "created_at": r["created_at"]}

    # ------------------------------------------------------------------ generation
    def _source_sentences(self, principal: Principal, subject_id: str) -> List[Dict[str, Any]]:
        out = []
        for d in self.conn.execute("SELECT * FROM exam_documents WHERE subject_id = ? AND kind IN ('notes','slides') AND owner_user_id = ?", (subject_id, principal.user_id)).fetchall():
            try:
                _a, _v, _r, doc = self.ws.data.document_json(principal, d["asset_id"], d["version_id"])
            except AppError:
                continue                                                        # a deleted or damaged source simply contributes nothing; the plan reports unfilled slots
            for pg in doc["pages"]:
                for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", str(pg.get("text") or ""))):
                    if 40 <= len(sent) <= 220 and sent[:1].isupper():
                        out.append({"text": sent.strip(), "ref": f"{d['title']} p{pg['number']}", "tokens": set(X.tokens(sent))})
        return out

    def generate(self, principal: Principal, blueprint_id: str, *, seed: Any = None, title: Any = None, rows_hook: Any = None, prediction_id: Optional[str] = None) -> Dict[str, Any]:
        """`rows_hook(plan, spec, by_slot, unfilled) -> (by_slot, unfilled, extra)` lets a caller (exam prediction) replace or fill slots before the draft is stored. Without it nothing changes."""
        self.limit("p7_exam_generate:user", principal)
        bp = self.get_blueprint(principal, blueprint_id)
        self._subject(principal, bp["subject_id"], writable=True)
        if seed is None:
            seed = int(uuid.uuid4().int % 2_000_000_000)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2_000_000_000:
            raise invalid("seed", "Use a whole number from 0 to 2 billion.")
        rng = random.Random(seed)
        plan, spec = bp["plan"], bp["spec"]
        curriculum = self._curriculum_rows(bp["subject_id"])
        kw_by_chapter: Dict[str, List[str]] = {}
        for r in curriculum:
            kw_by_chapter.setdefault(r["chapter"], [])
            kw_by_chapter[r["chapter"]] += [k for k in r["keywords"] if k not in kw_by_chapter[r["chapter"]]]
        all_kw = sorted({k for v in kw_by_chapter.values() for k in v})
        sentences = self._source_sentences(principal, bp["subject_id"])
        past = []
        for q in self.conn.execute("SELECT * FROM exam_questions WHERE subject_id = ? AND owner_user_id = ?", (bp["subject_id"], principal.user_id)).fetchall():
            cur = self._current(q["id"])
            if cur and cur["chapter"]:
                past.append({"q": q, "chapter": cur["chapter"], "topic": cur["topic"]})
        used: set = set()
        by_slot: Dict[int, Dict[str, Any]] = {}
        unfilled: List[Dict[str, Any]] = []
        for i, sl in enumerate(plan["slots"]):
            made = self._fill(sl, rng, past if spec.get("reuse_past_questions", True) else [], sentences, kw_by_chapter, all_kw, curriculum, used)
            if made is None:
                unfilled.append({"position": i + 1, "chapter": sl["chapter"], "qtype": sl["qtype"], "reason": "No past question or note sentence in your material fits this slot."})
                continue
            by_slot[i] = {**made, "difficulty": sl["difficulty"], "chapter": sl["chapter"]}
        extra: Dict[str, Any] = {}
        if rows_hook is not None:
            by_slot, unfilled, extra = rows_hook(plan, spec, by_slot, unfilled)
        out = [by_slot[i] for i in sorted(by_slot)]
        now = self.now()
        aid = new_id()
        with self.conn:
            self.conn.execute("INSERT INTO exam_assessments (id, blueprint_id, subject_id, project_id, owner_user_id, title, status, validation, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (aid, blueprint_id, bp["subject_id"], self.conn.execute("SELECT project_id FROM exam_subjects WHERE id = ?", (bp["subject_id"],)).fetchone()[0], principal.user_id,
                               clean(title, "title", maximum=160, required=False) or "Mock exam", "draft", jdump({"seed": seed, "unfilled": unfilled}), now, now))
            for pos, q in enumerate(out, 1):
                self._insert_q(aid, pos, q)
        if prediction_id:
            with self.conn:
                self.conn.execute("UPDATE exam_assessments SET prediction_id = ? WHERE id = ?", (prediction_id, aid))
        self._revalidate(aid, extra={"seed": seed, "unfilled": unfilled, **extra})
        self.audit(E.EXAM_ANALYSED, principal, aid, generated=len(out), unfilled=len(unfilled), predicted=bool(prediction_id))
        return self.get_assessment(principal, aid)

    def _insert_q(self, aid: str, pos: int, q: Dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO exam_assessment_questions (id, assessment_id, position, text, qtype, marks, difficulty, options, answer, marking, chapter, topic, origin, source_question_id, source_ref, included, edited) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (new_id(), aid, pos, q["text"], q["qtype"], q["marks"], q["difficulty"], jdump(q.get("options")) if q.get("options") else None, jdump(q["answer"]) if q.get("answer") is not None else None,
                           jdump(q.get("marking")) if q.get("marking") else None, q.get("chapter"), q.get("topic"), q["origin"], q.get("source_question_id"), q.get("source_ref"), 1, 0))

    def _fill(self, slot, rng, past, sentences, kw_by_chapter, all_kw, curriculum, used) -> Optional[Dict[str, Any]]:
        ch, qt, marks = slot["chapter"], slot["qtype"], slot["marks"]
        cands = [p for p in past if p["chapter"] == ch and p["q"]["qtype"] == qt and p["q"]["id"] not in used and (p["q"]["confidence"] >= LOW_CONF)]
        if cands:
            best = min(abs((p["q"]["marks"] or marks) - marks) for p in cands)
            pool = sorted([p for p in cands if abs((p["q"]["marks"] or marks) - marks) == best], key=lambda p: p["q"]["id"])
            p = rng.choice(pool)
            used.add(p["q"]["id"])
            q = p["q"]
            return {"text": q["text"], "qtype": qt, "marks": q["marks"] or marks, "options": jload(q["options"]), "answer": q["answer_text"], "marking": None, "topic": p["topic"], "origin": "past_paper",
                    "source_question_id": q["id"], "source_ref": q["source_ref"]}
        kws = kw_by_chapter.get(ch, [])
        here = [s for s in sentences if s["tokens"] & set(kws)]
        topic_rows = [r for r in curriculum if r["chapter"] == ch]
        label = (rng.choice([r for r in topic_rows if r["topic"]])["topic"] if any(r["topic"] for r in topic_rows) else ch.split(". ", 1)[-1])
        if qt in ("fill_blank", "mcq", "true_false"):
            pool = [s for s in here if s["text"] not in used]
            if not pool:
                return None
            s = rng.choice(sorted(pool, key=lambda x: x["text"]))
            used.add(s["text"])
            hit = sorted(s["tokens"] & set(kws), key=lambda w: (-len(w), w))
            key = hit[0]
            blank = re.sub(rf"\b{re.escape(key)}\b", "_____", s["text"], count=1, flags=re.I)
            if qt == "fill_blank":
                return {"text": blank, "qtype": qt, "marks": marks, "answer": key, "marking": [{"point": key, "origin": "source_derived", "ref": s["ref"]}], "origin": "source_derived", "source_ref": s["ref"], "topic": label}
            others = [w for w in rng.sample(all_kw, min(len(all_kw), 12)) if w != key and w not in s["tokens"]][:3]
            if qt == "mcq":
                if len(others) < 3:
                    return None
                opts = others + [key]
                rng.shuffle(opts)
                labels = "ABCD"
                return {"text": blank + " Choose the word that completes the statement.", "qtype": qt, "marks": marks, "options": [{"label": labels[i], "text": o} for i, o in enumerate(opts)], "answer": labels[opts.index(key)],
                        "marking": [{"point": f"The statement in the notes uses “{key}”.", "origin": "source_derived", "ref": s["ref"], "note": "Wrong options were chosen automatically from the syllabus vocabulary; check that none is also correct."}],
                        "origin": "source_derived", "source_ref": s["ref"], "topic": label}
            if rng.random() < 0.5 or not others:
                return {"text": s["text"] + " (True or False)", "qtype": qt, "marks": marks, "answer": "True", "marking": [{"point": "The statement is taken from your notes.", "origin": "source_derived", "ref": s["ref"]}], "origin": "source_derived", "source_ref": s["ref"], "topic": label}
            swapped = re.sub(rf"\b{re.escape(key)}\b", others[0], s["text"], count=1, flags=re.I)
            return {"text": swapped + " (True or False)", "qtype": qt, "marks": marks, "answer": "False", "marking": [{"point": f"A key term in the notes (“{key}”) was replaced by “{others[0]}”, so the statement is false.", "origin": "template_generated", "ref": s["ref"]}],
                    "origin": "template_generated", "source_ref": s["ref"], "topic": label}
        n = 3 if qt == "short" else 5
        ranked = sorted(here, key=lambda s: (-len(s["tokens"] & set(kws)), s["text"]))[:n]
        text = f"Write short notes on {label}." if qt == "short" else f"Discuss {label} in detail."
        marking = [{"point": s["text"], "origin": "source_derived", "ref": s["ref"]} for s in ranked]
        return {"text": text, "qtype": qt, "marks": marks, "answer": None, "marking": marking or [{"point": "No model answer is available from your material; the author must add one.", "origin": "template_generated"}],
                "origin": "template_generated", "source_ref": ranked[0]["ref"] if ranked else None, "topic": label}

    # ------------------------------------------------------------------ assessments
    def _assessment(self, principal: Principal, assessment_id: str, need: str):
        acc = self.access.resolve(principal, "assessment", assessment_id, need)
        return self.conn.execute("SELECT * FROM exam_assessments WHERE id = ?", (assessment_id,)).fetchone(), acc

    def get_assessment(self, principal: Principal, assessment_id: str) -> Dict[str, Any]:
        a, acc = self._assessment(principal, assessment_id, "view")
        qs = self.conn.execute("SELECT * FROM exam_assessment_questions WHERE assessment_id = ? ORDER BY position", (assessment_id,)).fetchall()
        val = jload(a["validation"], {})
        return {"id": a["id"], "title": a["title"], "status": a["status"], "subject_id": a["subject_id"], "blueprint_id": a["blueprint_id"], "approved_by": a["approved_by"], "approved_at": a["approved_at"],
                "validation": val, "your_access": acc.level, "created_at": a["created_at"], "updated_at": a["updated_at"],
                "notice": "A draft is not published. Review every question and answer, then approve." if a["status"] == "draft" else None,
                "prediction_id": a["prediction_id"],
                "prediction_notice": ("This paper is an estimate built from your past papers (and web articles, if you allowed them). It is not the real exam, and nothing guarantees that these topics will be asked." if a["prediction_id"] else None),
                "questions": [{"id": q["id"], "position": q["position"], "text": q["text"], "qtype": q["qtype"], "marks": q["marks"], "difficulty": q["difficulty"], "options": jload(q["options"]), "answer": jload(q["answer"]),
                               "marking": jload(q["marking"], []), "chapter": q["chapter"], "topic": q["topic"], "origin": q["origin"], "source_ref": q["source_ref"], "included": bool(q["included"]), "edited": bool(q["edited"])} for q in qs],
                "origin_labels": {"past_paper": "Taken as written from a past paper you supplied", "source_derived": "Built from a sentence in your notes or slides", "template_generated": "Built from a template; check it", "user_edited": "Edited by a person",
                                  "ai_written": "Written by an AI model from your syllabus and past-paper style; check it"}}

    def assessments(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        self._subject(principal, subject_id)
        return {"items": [{"id": r["id"], "title": r["title"], "status": r["status"], "created_at": r["created_at"]} for r in self.conn.execute("SELECT * FROM exam_assessments WHERE subject_id = ? AND owner_user_id = ? ORDER BY created_at DESC", (subject_id, principal.user_id))],
                "shared_with_me": self.access.shared_with(principal, "assessment")}

    def _editable(self, principal: Principal, assessment_id: str):
        a, acc = self._assessment(principal, assessment_id, "edit")
        if a["status"] != "draft":
            raise conflict("ASSESSMENT_LOCKED", f"This paper is {a['status']}. Generate a new one to change it.")
        return a

    def edit_question(self, principal: Principal, assessment_id: str, question_id: str, changes: Any) -> Dict[str, Any]:
        self._editable(principal, assessment_id)
        q = self.conn.execute("SELECT * FROM exam_assessment_questions WHERE id = ? AND assessment_id = ?", (question_id, assessment_id)).fetchone()
        if q is None:
            raise not_found("question")
        c = changes if isinstance(changes, dict) else {}
        sets, args = [], []
        if "text" in c:
            sets.append("text = ?")
            args.append(clean(c["text"], "text", maximum=2000, minimum=5))
        if "marks" in c:
            if isinstance(c["marks"], bool) or not isinstance(c["marks"], (int, float)) or not 0 < c["marks"] <= 100:
                raise invalid("marks", "Use a number from 0 to 100.")
            sets.append("marks = ?")
            args.append(c["marks"])
        if "options" in c:
            o = c["options"]
            if not isinstance(o, list) or len(o) > 6 or not all(isinstance(x, dict) and isinstance(x.get("label"), str) and isinstance(x.get("text"), str) for x in o):
                raise invalid("options", "Give options as [{label, text}].")
            sets.append("options = ?")
            args.append(jdump([{"label": x["label"][:2], "text": x["text"][:300]} for x in o]))
        if "answer" in c:
            sets.append("answer = ?")
            args.append(jdump(clean(c["answer"], "answer", maximum=3000, required=False)) if c["answer"] is not None else None)
        if "marking" in c:
            if not isinstance(c["marking"], list) or len(c["marking"]) > 20:
                raise invalid("marking", "Give up to 20 marking points.")
            sets.append("marking = ?")
            args.append(jdump([{"point": clean(p if isinstance(p, str) else (p or {}).get("point"), "marking", maximum=500), "origin": "user_edited"} for p in c["marking"]]))
        if "included" in c:
            sets.append("included = ?")
            args.append(1 if c["included"] is True else 0)
        if not sets:
            raise invalid("changes", "Nothing to change.")
        with self.conn:
            self.conn.execute(f"UPDATE exam_assessment_questions SET {', '.join(sets)}, edited = 1, origin = CASE WHEN ? = 1 THEN origin ELSE 'user_edited' END WHERE id = ?", (*args, 1 if list(c) == ["included"] else 0, question_id))
            self.conn.execute("UPDATE exam_assessments SET updated_at = ? WHERE id = ?", (self.now(), assessment_id))
        self._revalidate(assessment_id)
        return self.get_assessment(principal, assessment_id)

    def regenerate_question(self, principal: Principal, assessment_id: str, question_id: str, *, seed: Any = None) -> Dict[str, Any]:
        self.limit("p7_exam_generate:user", principal)
        a = self._editable(principal, assessment_id)
        q = self.conn.execute("SELECT * FROM exam_assessment_questions WHERE id = ? AND assessment_id = ?", (question_id, assessment_id)).fetchone()
        if q is None:
            raise not_found("question")
        owner = principal if a["owner_user_id"] == principal.user_id else None
        if owner is None:
            raise not_found("assessment")
        rng = random.Random(int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else int(uuid.uuid4().int % 2_000_000_000))
        curriculum = self._curriculum_rows(a["subject_id"])
        kw: Dict[str, List[str]] = {}
        for r in curriculum:
            kw.setdefault(r["chapter"], [])
            kw[r["chapter"]] += [k for k in r["keywords"] if k not in kw[r["chapter"]]]
        used = {x["text"] for x in self.conn.execute("SELECT text FROM exam_assessment_questions WHERE assessment_id = ?", (assessment_id,))} | {x["source_question_id"] for x in self.conn.execute("SELECT source_question_id FROM exam_assessment_questions WHERE assessment_id = ?", (assessment_id,)) if x["source_question_id"]}
        made = self._fill({"chapter": q["chapter"], "qtype": q["qtype"], "marks": q["marks"]}, rng, [], self._source_sentences(principal, a["subject_id"]), kw, sorted({k for v in kw.values() for k in v}), curriculum, used)
        if made is None:
            raise conflict("NOTHING_TO_USE", "Your notes do not contain another suitable sentence for this chapter and type.")
        with self.conn:
            self.conn.execute("UPDATE exam_assessment_questions SET text = ?, options = ?, answer = ?, marking = ?, origin = ?, source_ref = ?, source_question_id = NULL, edited = 0 WHERE id = ?",
                              (made["text"], jdump(made.get("options")) if made.get("options") else None, jdump(made["answer"]) if made.get("answer") is not None else None, jdump(made["marking"]) if made.get("marking") else None, made["origin"], made.get("source_ref"), question_id))
        self._revalidate(assessment_id)
        return self.get_assessment(principal, assessment_id)

    def validate(self, principal: Principal, assessment_id: str) -> Dict[str, Any]:
        self._assessment(principal, assessment_id, "view")
        return self._revalidate(assessment_id, persist=False)

    def _revalidate(self, aid: str, extra: Optional[Dict[str, Any]] = None, persist: bool = True) -> Dict[str, Any]:
        a = self.conn.execute("SELECT * FROM exam_assessments WHERE id = ?", (aid,)).fetchone()
        qs = [q for q in self.conn.execute("SELECT * FROM exam_assessment_questions WHERE assessment_id = ? ORDER BY position", (aid,)).fetchall()]
        inc = [q for q in qs if q["included"]]
        bp = self.conn.execute("SELECT * FROM exam_blueprints WHERE id = ?", (a["blueprint_id"],)).fetchone()
        plan = jload(bp["plan"], {})
        prev = jload(a["validation"], {})
        issues: List[Dict[str, Any]] = []
        add = lambda sev, code, msg, q=None: issues.append({"severity": sev, "code": code, "message": msg, "question_id": q["id"] if q else None, "position": q["position"] if q else None})  # noqa: E731
        if not inc:
            add("blocking", "EMPTY", "The paper has no questions.")
        seen: Dict[str, int] = {}
        for q in inc:
            key = re.sub(r"\W+", " ", q["text"].lower()).strip()
            if key in seen:
                add("blocking", "DUPLICATE", f"Question {q['position']} repeats question {seen[key]}.", q)
            seen.setdefault(key, q["position"])
            if q["qtype"] == "mcq":
                opts = jload(q["options"], []) or []
                labels = [o["label"] for o in opts]
                if len(opts) < 2 or len({o["text"].strip().lower() for o in opts}) != len(opts) or jload(q["answer"]) not in labels:
                    add("blocking", "MCQ_INVALID", f"Question {q['position']} needs at least two different options and an answer that is one of them.", q)
            if len(q["text"]) < 12 or q["text"].count("?") > 2:
                add("warning", "AMBIGUOUS", f"Question {q['position']} may be unclear (very short, or asks several things).", q)
            if jload(q["answer"]) is None and not jload(q["marking"], []):
                add("warning", "NO_ANSWER", f"Question {q['position']} has no answer or marking scheme.", q)
            elif q["origin"] == "template_generated" and q["qtype"] in ("short", "long") and not any(p.get("origin") == "source_derived" for p in jload(q["marking"], [])):
                add("warning", "AI_OR_TEMPLATE_ANSWER", f"Question {q['position']}: the marking scheme is not taken from your material.", q)
            if q["origin"] == "past_paper":
                add("warning", "REUSED_PAST", f"Question {q['position']} is a past-paper question used as written.", q)
            if q["origin"] == "ai_written":
                add("warning", "AI_WRITTEN", f"Question {q['position']} was written by an AI model. Check the wording, the answer and the marking before approving.", q)
            if q["source_question_id"]:
                src = self.conn.execute("SELECT confidence, warnings FROM exam_questions WHERE id = ?", (q["source_question_id"],)).fetchone()
                if src and src["confidence"] < LOW_CONF:
                    add("warning", "LOW_EXTRACTION", f"Question {q['position']} was read with low confidence.", q)
        total = sum(q["marks"] for q in inc)
        target = plan.get("target_marks") or plan.get("total_marks")
        if target is not None and abs(total - target) > 0.01:
            add("blocking", "MARKS_MISMATCH", f"The questions add up to {total:g} marks but the blueprint asks for {target:g}.")
        covered = {q["chapter"] for q in inc}
        for ch in plan.get("chapters", []):
            if ch not in covered:
                add("warning", "UNCOVERED_CHAPTER", f"No question covers “{ch}”.")
        if inc:
            mix = plan.get("difficulty_mix") or {}
            for d in ("easy", "medium", "hard"):
                share = 100 * sum(q["marks"] for q in inc if q["difficulty"] == d) / (total or 1)
                if mix and abs(share - mix.get(d, 0)) > 25:
                    add("warning", "DIFFICULTY_MIX", f"{d.title()} questions carry {share:.0f}% of the marks; the blueprint asked for {mix.get(d)}%.")
        unfilled = (extra or {}).get("unfilled", prev.get("unfilled", []))
        if unfilled:
            add("warning", "UNFILLED", f"{len(unfilled)} planned question(s) could not be built from your material.")
        res = {"issues": issues, "ok": not any(i["severity"] == "blocking" for i in issues), "blocking": sum(i["severity"] == "blocking" for i in issues), "warnings": sum(i["severity"] == "warning" for i in issues),
               "total_marks": total, "questions": len(inc), "seed": (extra or {}).get("seed", prev.get("seed")), "unfilled": unfilled}
        if (extra or {}).get("ai") or prev.get("ai"):
            res["ai"] = (extra or {}).get("ai") or prev.get("ai")
        if persist:
            with self.conn:
                self.conn.execute("UPDATE exam_assessments SET validation = ? WHERE id = ?", (jdump(res), aid))
        return res

    def approve(self, principal: Principal, assessment_id: str, *, confirm: Any = False, acknowledge_warnings: Any = False) -> Dict[str, Any]:
        a = self._editable(principal, assessment_id)
        res = self._revalidate(assessment_id)
        if not res["ok"]:
            raise AppError("VALIDATION_FAILED", f"{res['blocking']} problem(s) must be fixed before this paper can be approved.", 409)
        if confirm is not True:
            raise AppError("CONFIRMATION_REQUIRED", "Approving marks this paper as reviewed and ready. Send confirm=true to approve.", 409)
        if res["warnings"] and acknowledge_warnings is not True:
            raise AppError("CONFIRMATION_REQUIRED", f"There are {res['warnings']} warning(s). Review them, then send acknowledge_warnings=true to approve anyway.", 409)
        with self.conn:
            self.conn.execute("UPDATE exam_assessments SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?", (principal.user_id, self.now(), self.now(), assessment_id))
        self.audit(E.EXAM_APPROVED, principal, assessment_id, warnings=res["warnings"])
        return self.get_assessment(principal, assessment_id)

    def reject(self, principal: Principal, assessment_id: str, *, reason: Any = None) -> Dict[str, Any]:
        self._editable(principal, assessment_id)
        with self.conn:
            self.conn.execute("UPDATE exam_assessments SET status = 'rejected', updated_at = ? WHERE id = ?", (self.now(), assessment_id))
        self.audit(E.EXAM_APPROVED, principal, assessment_id, outcome_state="rejected")
        return self.get_assessment(principal, assessment_id)

    def paper_text(self, principal: Principal, assessment_id: str, *, with_key: bool = False) -> Dict[str, Any]:
        a = self.get_assessment(principal, assessment_id)
        lines = [a["title"], f"Status: {a['status']}", ""]
        n = 0
        for q in a["questions"]:
            if not q["included"]:
                continue
            n += 1
            lines.append(f"{n}. {q['text']} [{q['marks']:g} mark{'s' if q['marks'] != 1 else ''}]")
            for o in q["options"] or []:
                lines.append(f"   {o['label']}) {o['text']}")
        if with_key:
            lines += ["", "ANSWER KEY AND MARKING SCHEME", ""]
            n = 0
            for q in a["questions"]:
                if not q["included"]:
                    continue
                n += 1
                lines.append(f"{n}. Answer: {q['answer'] if q['answer'] is not None else '(none recorded)'}  [question: {q['origin']}]")
                for p in q["marking"] or []:
                    lines.append(f"   - {p['point']}  [{p.get('origin', 'unknown')}{', ' + p['ref'] if p.get('ref') else ''}]")
        return {"title": a["title"], "status": a["status"], "text": "\n".join(lines), "includes_key": with_key}

    # ------------------------------------------------------------------ for reports
    def report_pool(self, principal: Principal, project_id: str, analysis_ids: Sequence[str], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
        summary, scope, method, findings, analysis, limits, unresolved, subs = [], [], [], [], [], [], [], []
        for aid in analysis_ids:
            a = self.get_analysis(principal, aid)
            if a["project_id"] != project_id:
                raise conflict("PROJECT_MISMATCH", "All sources of a report must come from the same project.")
            r = a["result"]
            subj = self.conn.execute("SELECT name FROM exam_subjects WHERE id = ?", (a["subject_id"],)).fetchone()
            name = subj["name"] if subj else "the subject"
            years = f"{r['years'][0]}–{r['years'][-1]}" if r["years"] else "undated"
            ref = ev.calculation(es, key=f"exam:{aid}", title=f"Exam Intelligence analysis of {name}", source_type="exam_analysis", calculation_ref=f"exam_analysis:{aid}", when=a["created_at"],
                                 passage=f"{r['papers']} past papers ({years}), {r['questions']} questions, {r['marks_total']:g} marks; heuristic {r['heuristic']}", limitation=X.NOTICE,
                                 uncertainty="Chapter mapping is automatic unless a person corrected it.", source_id=aid)
            ranked = [c for c in r["chapters"] if c["questions"]]
            ranked.sort(key=lambda c: c.get("rank", 999))
            top = ranked[0] if ranked else None
            summary.append(C.claim(f"{r['questions']} questions from {r['papers']} past paper(s) ({years}) were grouped into {len(ranked)} chapter(s) for {name}." + (f" The highest heuristic score belongs to “{top['chapter']}” ({top['score']}), which carried {top['marks_share'] * 100:.0f}% of the marks in these papers." if top else ""), [ref], "calculation"))
            scope.append(C.claim(f"The analysis used {r['papers']} paper(s); {r['inputs']['user_corrections']} chapter assignment(s) were corrected by a person; {r['unmapped_questions']} question(s) could not be matched to a chapter.", [ref], "process"))
            method.append(C.claim("Each chapter's heuristic score blends four shares, each scaled to the highest chapter: " + ", ".join(f"{k.replace('_', ' ')} {w:g}" for k, w in r["weights"].items()) + f" (calculation {r['heuristic']}).", [ref], "process"))
            subs.append({"heading": f"{name}: chapters by heuristic score", "blocks": [
                C.table("Historical frequency and relative emphasis by chapter (past papers only)", ["Rank", "Chapter", "Questions (historical frequency)", "Marks", "Share of marks (relative emphasis)", "Papers containing it", "Last year", "Heuristic score"],
                        [[c.get("rank"), c["chapter"], c["questions"], c["marks"], f"{c['marks_share'] * 100:.1f}%", c["papers_with_topic"], c["last_year"] or "", c["score"]] for c in ranked][:40], [ref]),
                C.figure("Heuristic score by chapter", "Heuristic score (0–100)", [c["chapter"][:24] for c in ranked][:15], [c["score"] for c in ranked][:15], [ref])], "status": "complete", "why": None})
            if r["never_asked"]:
                analysis.append(C.claim("Chapters with no question in these papers: " + "; ".join(r["never_asked"][:15]) + ". That describes these papers only.", [ref], "calculation"))
            limits.append(C.claim(X.NOTICE, [ref], "process"))
            limits += [C.claim(w, [ref], "process") for w in r["warnings"]]
            if r["unmapped_questions"] or r["inputs"]["low_confidence_questions"]:
                unresolved.append(C.claim(f"{r['unmapped_questions']} unmapped and {r['inputs']['low_confidence_questions']} low-confidence question(s) need a person's review.", [ref], "process"))
        return {"summary": C.sec([C.para(summary)]), "scope": C.sec([C.para(scope)]), "method": C.sec([C.para(method)]), "findings": C.sec(subsections=subs),
                "analysis": C.sec([C.para(analysis)]) if analysis else C.empty("Every chapter appeared at least once."), "limitations": C.sec([C.para(limits)]),
                "unresolved": C.sec([C.para(unresolved)]) if unresolved else C.sec([C.note("No unmapped or low-confidence questions were recorded.")]), "appendix": C.sec([C.note("The full question list with each chapter assignment and its history is in the Exam Intelligence area.")])}
