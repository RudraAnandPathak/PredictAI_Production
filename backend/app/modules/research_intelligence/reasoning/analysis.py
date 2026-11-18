"""Multi-source analysis (R5): compare many sources of one workspace and report what they share, where they may disagree, how they were made, what is missing, how strong the evidence
looks and how the work developed over time.

An analysis is a background job (the queue the ingestion already uses), so a large set never blocks a request, shows progress, can be cancelled, and survives a worker restart. The result is
deterministic and extractive: every item points at exact sentences of stored passages. A model is OPTIONAL and only writes a short narrative on top; that narrative goes through the same
sentence-by-sentence verification as assistant answers (unsupported sentences are removed), the workspace's AI data policy decides whether an external provider may see the quotes, and
without a model, or when the model fails, the deterministic result stands on its own."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from ....db import transaction
from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...identity.tokens import new_id
from ...p7 import jload
from ...providers.base import Consent, build_messages
from ...workspace.errors import conflict, invalid, not_found
from ..citations.verify import verify_answer
from ..core.common import EV_ANALYSIS, EV_EXTERNAL, check_daily, record_usage
from ..services.jobs import Cancelled, JobService
from . import engine, facts as factsmod
from .engine import Src

MAX_QUESTION = 500
MAX_TITLE = 120
ACTIVE_PER_WORKSPACE = 3
NARRATIVE_INSTRUCTION = ("Write a neutral synthesis of at most five short sentences about what these sources report, using only the passages below. Put the source id in square brackets, "
                         "like [S1], at the end of every sentence. Do not add anything that is not in the passages. If they do not support a synthesis, reply exactly: INSUFFICIENT")


class AnalysisService:
    def __init__(self, ctx: Context, *, router_factory: Optional[Callable[[], Any]] = None) -> None:
        self.ctx, self.conn, self.router_factory = ctx, ctx.conn, router_factory
        self.jobs = JobService(ctx)

    # ------------------------------------------------------------------ person side
    def _workspace(self, principal: Principal, workspace_id: Any, writable: bool = False) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        row = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if row is None:
            raise not_found("research workspace")
        if writable and row["status"] != "active":
            raise conflict("WORKSPACE_ARCHIVED", "This workspace is archived. Restore it to run an analysis.")
        return row

    def _row(self, principal: Principal, workspace_id: str, analysis_id: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_analyses WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (analysis_id if isinstance(analysis_id, str) else "", workspace_id, principal.user_id)).fetchone()
        if r is None:
            raise not_found("analysis")
        return r

    @staticmethod
    def _present(r: sqlite3.Row, full: bool) -> Dict[str, Any]:
        out = {"id": r["id"], "workspace_id": r["workspace_id"], "title": r["title"], "question": r["question"], "source_ids": jload(r["source_ids"], []), "use_model": bool(r["use_model"]),
               "status": r["status"], "stage": r["stage"], "progress": r["progress"], "job_id": r["job_id"], "error_code": r["error_code"], "error_message": r["error_message"],
               "created_at": r["created_at"], "finished_at": r["finished_at"]}
        if full:
            out["result"] = jload(r["result"], None)
        return out

    def create(self, principal: Principal, workspace_id: str, *, title: Optional[str] = None, question: Optional[str] = None, source_ids: Optional[List[str]] = None, use_model: bool = False) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_analyse:user", principal.user_id)
        if question is not None and (not isinstance(question, str) or len(question) > MAX_QUESTION):
            raise invalid("question", f"Keep the focus question under {MAX_QUESTION} characters.")
        question = (question or "").strip() or None
        title = (title or "").strip()[:MAX_TITLE] or (f"Analysis: {question[:80]}" if question else "Analysis of workspace sources")
        s = self.ctx.settings
        ready = self.conn.execute("SELECT id FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ? AND status = 'ready' AND included = 1 ORDER BY created_at, id", (ws["id"], principal.user_id)).fetchall()
        ready_ids = [r["id"] for r in ready]
        if source_ids is None:
            chosen = ready_ids
        else:
            if not isinstance(source_ids, list) or not all(isinstance(x, str) for x in source_ids):
                raise invalid("source_ids", "Choose sources from this workspace.")
            chosen = [i for i in dict.fromkeys(source_ids)]
            bad = [i for i in chosen if i not in set(ready_ids)]
            if bad:
                raise invalid("source_ids", "Every chosen source must be in this workspace, ready, and included.")
        if len(chosen) < 2:
            raise invalid("source_ids", "Choose at least two ready sources to compare." if source_ids is not None else "An analysis needs at least two ready sources in this workspace.")
        if len(chosen) > s.ri_analysis_max_sources:
            raise invalid("source_ids", f"An analysis can compare at most {s.ri_analysis_max_sources} sources. Choose fewer.")
        active = self.conn.execute("SELECT COUNT(*) FROM ri_analyses WHERE workspace_id = ? AND owner_user_id = ? AND status IN ('queued','running')", (ws["id"], principal.user_id)).fetchone()[0]
        if active >= ACTIVE_PER_WORKSPACE:
            raise conflict("ANALYSES_RUNNING", f"{active} analyses of this workspace are still running. Wait for one to finish or cancel it.")
        check_daily(self.ctx, principal, "reasoning", "analyses_per_day", "You have reached today's limit of {limit} analyses. It resets tomorrow, or activate Special access for a higher limit.")
        aid, now = new_id(), self.ctx.clock.iso()
        with transaction(self.conn):
            jid = self.jobs.enqueue(principal.user_id, ws["id"], "analysis", None, {"analysis_id": aid})
            self.conn.execute("INSERT INTO ri_analyses (id, workspace_id, owner_user_id, title, question, source_ids, use_model, status, stage, progress, job_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (aid, ws["id"], principal.user_id, title, question, json.dumps(chosen), int(bool(use_model)), "queued", "queued", 0, jid, now))
            record_usage(self.ctx, principal.user_id, ws["id"], "reasoning", 1, f"{len(chosen)} sources")
        self.ctx.audit.record(EV_ANALYSIS, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "analysis_id": aid, "action": "created", "sources": len(chosen), "use_model": bool(use_model)})
        return self._present(self._row(principal, ws["id"], aid), False)

    def list(self, principal: Principal, workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        ws = self._workspace(principal, workspace_id)
        rows = self.conn.execute("SELECT * FROM ri_analyses WHERE workspace_id = ? AND owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (ws["id"], principal.user_id, max(1, min(int(limit), 200)))).fetchall()
        return [self._present(r, False) for r in rows]

    def get(self, principal: Principal, workspace_id: str, analysis_id: str) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        return self._present(self._row(principal, ws["id"], analysis_id), True)

    def cancel(self, principal: Principal, workspace_id: str, analysis_id: str) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        row = self._row(principal, ws["id"], analysis_id)
        if row["status"] not in ("queued", "running"):
            raise conflict("ANALYSIS_FINISHED", "This analysis has already finished.")
        job = self.jobs.cancel(principal, row["job_id"]) if row["job_id"] else None
        if job is None or job["status"] == "cancelled":                                  # never started: nothing will notice, so close it here. A running one stops at its next stage.
            self.conn.execute("UPDATE ri_analyses SET status='cancelled', stage='cancelled', finished_at=? WHERE id=? AND owner_user_id=?", (self.ctx.clock.iso(), row["id"], principal.user_id))
        self.ctx.audit.record(EV_ANALYSIS, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "analysis_id": row["id"], "action": "cancel"})
        return self._present(self._row(principal, ws["id"], analysis_id), False)

    def delete(self, principal: Principal, workspace_id: str, analysis_id: str) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        row = self._row(principal, ws["id"], analysis_id)
        if row["status"] in ("queued", "running"):
            raise conflict("ANALYSIS_ACTIVE", "Cancel this analysis before deleting it.")
        self.conn.execute("DELETE FROM ri_analyses WHERE id = ? AND owner_user_id = ?", (row["id"], principal.user_id))
        self.ctx.audit.record(EV_ANALYSIS, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "analysis_id": row["id"], "action": "deleted"})
        return {"deleted": True, "id": row["id"]}

    # ------------------------------------------------------------------ worker side
    def run(self, job: Dict[str, Any]) -> None:
        aid = (jload(job["payload"], {}) or {}).get("analysis_id")
        row = self.conn.execute("SELECT * FROM ri_analyses WHERE id = ? AND owner_user_id = ? AND workspace_id = ?", (aid if isinstance(aid, str) else "", job["owner_user_id"], job["workspace_id"])).fetchone()
        if row is None or row["status"] in ("cancelled", "succeeded"):
            raise Cancelled()
        try:
            self._compute(job, row)
        except Cancelled:
            self.conn.execute("UPDATE ri_analyses SET status='cancelled', stage='cancelled', finished_at=? WHERE id=?", (self.ctx.clock.iso(), row["id"]))
            raise

    def mark_failed(self, job: Dict[str, Any], code: str, message: str, final: bool) -> None:
        aid = (jload(job.get("payload"), {}) or {}).get("analysis_id")
        if not isinstance(aid, str):
            return
        if final:
            self.conn.execute("UPDATE ri_analyses SET status='failed', stage='failed', error_code=?, error_message=?, finished_at=? WHERE id=? AND status IN ('queued','running')", (code, message[:500], self.ctx.clock.iso(), aid))
        else:
            self.conn.execute("UPDATE ri_analyses SET status='queued', stage='waiting to retry', error_code=?, error_message=? WHERE id=? AND status IN ('queued','running')", (code, message[:500], aid))

    def _step(self, job: Dict[str, Any], aid: str, pct: int, stage: str) -> None:
        self.jobs.heartbeat(job["id"], pct, stage)                                        # raises Cancelled when the person cancelled
        self.conn.execute("UPDATE ri_analyses SET status='running', stage=?, progress=? WHERE id=? AND status IN ('queued','running')", (stage, pct, aid))

    def _allow_external(self, workspace_id: str, owner_id: str) -> bool:
        from ..policy import external_allowed
        return external_allowed(self.conn, self.ctx.settings, owner_id, workspace_id)

    def _compute(self, job: Dict[str, Any], row: sqlite3.Row) -> None:
        aid, owner, wid = row["id"], row["owner_user_id"], row["workspace_id"]
        wanted = jload(row["source_ids"], [])
        self._step(job, aid, 3, "Reading sources")
        srcs: List[Src] = []
        skipped: List[str] = []
        for n, sid in enumerate(wanted, 1):
            r = self.conn.execute("SELECT * FROM ri_sources WHERE id = ? AND owner_user_id = ? AND workspace_id = ? AND status = 'ready'", (sid, owner, wid)).fetchone()
            if r is None:
                skipped.append(sid)                                                       # deleted or re-processing since the analysis was requested
                continue
            with transaction(self.conn):
                f = factsmod.load(self.conn, r, self.ctx.clock.iso())
            srcs.append(Src(f"S{len(srcs) + 1}", dict(r), f))
            if n % 5 == 0 or n == len(wanted):
                self._step(job, aid, 3 + int(42 * n / len(wanted)), f"Reading sources ({n}/{len(wanted)})")
        if len(srcs) < 2:
            from ..services.pipeline import PipelineError
            raise PipelineError("TOO_FEW_SOURCES", "Fewer than two of the chosen sources are still ready, so there is nothing to compare.", retry=False)
        focus = row["question"]
        self._step(job, aid, 50, "Finding what sources share and where they may disagree")
        found = engine.analyse_findings(srcs, focus)
        self._step(job, aid, 62, "Comparing methods")
        methods = engine.compare_methods(srcs)
        self._step(job, aid, 72, "Looking for gaps")
        gaps = engine.find_gaps(srcs, focus)
        self._step(job, aid, 80, "Grading the evidence")
        grades = engine.grade_sources(srcs)
        themes_conf = engine.grade_themes(found["common"], grades, found["contradictions"])
        self._step(job, aid, 86, "Building the timeline")
        tl = engine.timeline(srcs)
        self._step(job, aid, 90, "Writing the synthesis")
        synth = engine.synthesise(srcs, found, themes_conf)
        generated = {"mode": "deterministic", "provider": None, "model": None, "external": False, "removed_unsupported": 0, "extractor_version": factsmod.EXTRACTOR_VERSION, "method": engine.METHOD}
        if row["use_model"]:
            self._narrative(job, row, srcs, synth, generated)
        result = {"question": focus, "sources": [self._source_card(s) for s in srcs], "skipped_sources": skipped, "findings": found, "methods": methods, "gaps": gaps,
                  "evidence": {"sources": grades, "themes": themes_conf, "note": "Grades come from the study design the text states and a few modifiers. They are a reading aid, not a judgement of quality."},
                  "timeline": tl, "synthesis": synth, "generated": generated,
                  "limitations": ["Sentences are grouped by shared words, not by meaning, so related findings phrased differently can be missed and unrelated ones can occasionally be joined.",
                                  "Contradictions are only 'possible': opposite wording does not always mean opposite results. Read the quotes."]}
        self._step(job, aid, 98, "Saving")
        self.conn.execute("UPDATE ri_analyses SET status='succeeded', stage='done', progress=100, result=?, error_code=NULL, error_message=NULL, finished_at=? WHERE id=?", (json.dumps(result), self.ctx.clock.iso(), aid))
        self.ctx.audit.record(EV_ANALYSIS, "success", actor=owner, metadata={"workspace_id": wid, "analysis_id": aid, "action": "completed", "sources": len(srcs), "mode": generated["mode"]})

    @staticmethod
    def _source_card(s: Src) -> Dict[str, Any]:
        r = s.row
        return {"label": s.label, "source_id": r["id"], "title": r["title"], "authors": engine.row_authors(r), "year": s.year, "venue": r.get("venue"), "doi": r.get("doi"), "access": r["access"],
                "citation": s.short, "findings_found": len(s.facts["findings"])}

    def _narrative(self, job: Dict[str, Any], row: sqlite3.Row, srcs: List[Src], synth: Dict[str, Any], generated: Dict[str, Any]) -> None:
        """Optional model-written overview. Failure of any kind leaves the deterministic result untouched."""
        if self.router_factory is None:
            generated["note"] = "No AI model is configured, so only the extractive analysis was produced."
            return
        passages = {s.label: " ".join(f["text"] for f in s.facts["findings"][:3]) for s in srcs if s.facts["findings"]}
        if len(passages) < 2:
            generated["note"] = "Too few sources had extractable findings for a narrative."
            return
        allow_external = self._allow_external(row["workspace_id"], row["owner_user_id"])
        try:
            router = self.router_factory()
            messages = build_messages(NARRATIVE_INSTRUCTION, list(passages.items()), row["question"] or "What do these sources report?")
            out = router.chat(row["owner_user_id"], None, "synthesize", messages, data_class="private", consent=Consent(external=allow_external), max_tokens=500, temperature=0.0)
        except Exception:                                                                    # noqa: BLE001 - a provider fault means the extractive analysis only
            generated["note"] = "The AI model could not be reached, so only the extractive analysis was produced."
            return
        if not out.ok or not out.text:
            generated["note"] = "The AI model gave no usable answer, so only the extractive analysis was produced."
            return
        v = verify_answer(out.text, passages)
        generated["removed_unsupported"] = v.removed
        if not v.kept or "INSUFFICIENT" in out.text.upper()[:30]:
            generated["note"] = "The model's overview was not supported by the sources, so it was discarded."
            return
        external = False
        try:
            r = self.conn.execute("SELECT privacy_class FROM ai_providers WHERE key = ?", (out.provider,)).fetchone()
            external = bool(r and r["privacy_class"] == "external")
        except sqlite3.Error:
            pass
        synth["narrative"] = {"text": v.text, "labels": v.labels}
        generated.update({"mode": "model", "provider": out.provider, "model": out.model, "external": external})
        if external:
            self.ctx.audit.record(EV_EXTERNAL, "success", actor=row["owner_user_id"], metadata={"workspace_id": row["workspace_id"], "provider": out.provider, "purpose": "analysis"})
