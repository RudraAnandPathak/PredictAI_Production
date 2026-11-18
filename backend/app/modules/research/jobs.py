"""The job lifecycle shared by research runs, news ingestion and embedding builds: queued -> running -> completed | failed | cancelled.
Cancel is a flag in the database that the work polls; a heartbeat lets a restart recognise jobs whose thread is gone. A handler only does the work: this module decides how the
job ends, so no handler can leave a job 'running' forever."""
from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

from ...core.clock import to_iso
from ...core.errors import AppError
from .errors import Cancelled, ResearchFailed
from .events import REvent

logger = logging.getLogger("predictai.research")
RUNNING_STALE_S = 90
QUEUED_STALE_S = 30 * 60
HEARTBEAT_EVERY_S = 1.0


class Control:
    """What a handler gets: a cancellation check and a progress sink. Both hit the database lightly (at most one write per second)."""

    def __init__(self, repo: Any, clock: Any, job_id: str) -> None:
        self.repo, self.clock, self.job_id, self._last = repo, clock, job_id, 0.0
        self.summary: Dict[str, Any] = {}          # a handler may leave per-item results here; they are kept in the job's final progress record

    def cancelled(self) -> bool:
        state = self.repo.cancel_state(self.job_id)
        return state is None or state

    def progress(self, phase: str, fraction: float, message: str) -> None:
        now = time.monotonic()
        if now - self._last < HEARTBEAT_EVERY_S and fraction < 0.99:
            return
        self._last = now
        self.repo.set_job(self.job_id, progress=json.dumps({"phase": phase, "fraction": round(max(0.0, min(1.0, fraction)), 3), "message": message[:200]}), heartbeat_at=self.clock.iso())


def run_job(svc: Any, job_id: str, handlers: Dict[str, Callable[[Any, Control], Optional[str]]]) -> None:
    repo, clock, audit = svc.repo, svc.ctx.clock, svc.ctx.audit
    job = repo.job_internal(job_id)
    if job is None:
        return
    owner = job["owner_user_id"]

    def finish(status: str, started: float, **fields: Any) -> None:
        repo.finish_job(job_id, status=status, now=clock.iso(), duration_ms=int((time.monotonic() - started) * 1000), **fields)

    now0 = time.monotonic()
    if job["cancel_requested"] and job["status"] == "queued":
        finish("cancelled", now0, progress=json.dumps({"phase": "cancelled", "fraction": 0, "message": "Cancelled before it started"}))
        audit.record(REvent.RUN_CANCELLED, actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"]})
        return
    if not repo.claim_job(job_id, clock.iso()):
        return
    audit.record(REvent.RUN_STARTED, actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"]})
    started = time.monotonic()
    ctl = Control(repo, clock, job_id)
    try:
        handler = handlers.get(job["kind"])
        if handler is None:
            raise ResearchFailed("JOB_KIND_UNKNOWN", "This kind of job is not available.")
        result_id = handler(job, ctl)
        if ctl.cancelled():
            raise Cancelled()
        finish("completed", started, result_id=result_id, progress=json.dumps({"phase": "completed", "fraction": 1.0, "message": "Done", **ctl.summary}))
        audit.record(REvent.RUN_COMPLETED, actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"], "result_id": result_id, "duration_ms": int((time.monotonic() - started) * 1000)})
    except Cancelled:
        finish("cancelled", started, progress=json.dumps({"phase": "cancelled", "fraction": 0, "message": "Cancelled"}))
        audit.record(REvent.RUN_CANCELLED, actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"]})
    except (ResearchFailed, AppError) as e:
        code, message = getattr(e, "code", "RESEARCH_ERROR"), getattr(e, "message", None) or str(e)
        finish("failed", started, error_code=code, error_message=message[:300], progress=json.dumps({"phase": "failed", "fraction": 0, "message": message[:200], **{k: v for k, v in ctl.summary.items() if k != "message"}}))
        audit.record(REvent.RUN_FAILED, "failure", actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"], "code": code})
    except Exception as exc:  # noqa: BLE001  a bug must end the job visibly. Messages can quote document or page text, so only the type and place are logged.
        frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
        logger.error("Unexpected %s in research job at %s:%s", type(exc).__name__, frame.filename.rsplit("/", 1)[-1] if frame else "?", frame.lineno if frame else "?")
        finish("failed", started, error_code="RESEARCH_ERROR", error_message="The research could not be completed.", progress=json.dumps({"phase": "failed", "fraction": 0, "message": "Failed"}))
        audit.record(REvent.RUN_FAILED, "failure", actor=owner, target=job_id, metadata={"kind": job["kind"], "project_id": job["project_id"], "code": "RESEARCH_ERROR"})


def recover_stale(repo: Any, clock: Any, audit: Any, owns: Callable[[str], bool]) -> int:
    """Jobs whose thread is gone (server stopped) are marked failed with a plain reason, never left 'running'."""
    now = clock.now()
    n = 0
    for job in repo.stale_jobs(running_before=to_iso(now - timedelta(seconds=RUNNING_STALE_S)), queued_before=to_iso(now - timedelta(seconds=QUEUED_STALE_S))):
        if owns(job["id"]):
            continue
        if repo.finish_job(job["id"], status="failed", now=to_iso(now), error_code="JOB_INTERRUPTED", error_message="The server stopped while this was running. Nothing was saved; you can start it again.",
                           progress=json.dumps({"phase": "failed", "fraction": 0, "message": "Interrupted"})):
            n += 1
            if job["session_id"]:
                row = repo.conn.execute("SELECT status FROM research_sessions WHERE id = ?", (job["session_id"],)).fetchone()
                if row and row["status"] in ("queued", "running"):
                    repo.update_session(job["session_id"], status="failed", updated_at=to_iso(now))
            audit.record(REvent.RUN_FAILED, "failure", actor=job["owner_user_id"], target=job["id"], metadata={"kind": job["kind"], "project_id": job["project_id"], "code": "JOB_INTERRUPTED"})
    return n
