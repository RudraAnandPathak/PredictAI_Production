"""Database-backed job queue (no extra infrastructure, ₹0). A worker claims a job with a lease; a job whose lease expires is picked up again (crash recovery) until max_attempts, after
which it fails with a stored reason. Retries back off exponentially. Cancellation is cooperative: a running job notices `cancelling` at its next stage boundary."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from ....core.clock import to_iso
from ...identity.authorization import Principal
from ...identity.context import Context
from ...identity.tokens import new_id
from ...workspace.errors import conflict, not_found
from ....db import transaction
from ..core.common import EV_JOB

KINDS = ("ingest", "embed", "reindex", "fetch_url", "resolve_identifier", "analysis")
TERMINAL = ("succeeded", "failed", "cancelled")


class Cancelled(Exception):
    pass


class JobService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    def enqueue(self, owner_id: str, workspace_id: str, kind: str, source_id: Optional[str], payload: Optional[Dict[str, Any]] = None) -> str:
        assert kind in KINDS
        jid, now = new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_jobs (id, workspace_id, owner_user_id, source_id, kind, status, payload, max_attempts, run_after, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (jid, workspace_id, owner_id, source_id, kind, "queued", json.dumps(payload or {}), self.ctx.settings.ri_job_max_attempts, now, now))
        return jid

    # ------------------------------------------------------------------ worker side
    def claim(self, worker_id: str) -> Optional[Dict[str, Any]]:
        now_dt = self.ctx.clock.now()
        now, until = to_iso(now_dt), to_iso(now_dt + timedelta(seconds=self.ctx.settings.ri_worker_lease_s))
        with transaction(self.conn):
            # a job that was running when its worker died: lease expired
            for r in self.conn.execute("SELECT id, attempts, max_attempts, status FROM ri_jobs WHERE status IN ('running','cancelling') AND locked_until < ?", (now,)).fetchall():
                if r["status"] == "cancelling":
                    self.conn.execute("UPDATE ri_jobs SET status='cancelled', finished_at=?, locked_by=NULL, locked_until=NULL WHERE id=?", (now, r["id"]))
                elif r["attempts"] >= r["max_attempts"]:
                    self.conn.execute("UPDATE ri_jobs SET status='failed', error_code='WORKER_LOST', error_message='The worker stopped while processing this job and no attempts remain.', finished_at=?, locked_by=NULL, locked_until=NULL WHERE id=?", (now, r["id"]))
                else:
                    self.conn.execute("UPDATE ri_jobs SET status='queued', locked_by=NULL, locked_until=NULL, stage='recovered after worker stopped', run_after=? WHERE id=?", (now, r["id"]))
            row = self.conn.execute("SELECT * FROM ri_jobs WHERE status = 'queued' AND run_after <= ? ORDER BY run_after, created_at LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            self.conn.execute("UPDATE ri_jobs SET status='running', attempts=attempts+1, locked_by=?, locked_until=?, started_at=COALESCE(started_at, ?), stage='starting' WHERE id=?", (worker_id, until, now, row["id"]))
        return self.get_raw(row["id"])

    def get_raw(self, job_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM ri_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(r) if r else None

    def heartbeat(self, job_id: str, progress: Optional[int] = None, stage: Optional[str] = None) -> None:
        """Extend the lease, record progress, and stop the job if it was cancelled."""
        until = to_iso(self.ctx.clock.now() + timedelta(seconds=self.ctx.settings.ri_worker_lease_s))
        row = self.conn.execute("SELECT status FROM ri_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["status"] == "cancelling":
            raise Cancelled()
        self.conn.execute("UPDATE ri_jobs SET locked_until = ?, progress = COALESCE(?, progress), stage = COALESCE(?, stage) WHERE id = ?", (until, progress, stage, job_id))

    def succeed(self, job_id: str) -> None:
        self.conn.execute("UPDATE ri_jobs SET status='succeeded', progress=100, stage='done', finished_at=?, locked_by=NULL, locked_until=NULL WHERE id=?", (self.ctx.clock.iso(), job_id))

    def cancelled(self, job_id: str) -> None:
        self.conn.execute("UPDATE ri_jobs SET status='cancelled', stage='cancelled', finished_at=?, locked_by=NULL, locked_until=NULL WHERE id=?", (self.ctx.clock.iso(), job_id))

    def fail(self, job: Dict[str, Any], code: str, message: str, *, retry: bool) -> str:
        """Returns 'retry' or 'failed'."""
        now_dt = self.ctx.clock.now()
        if retry and job["attempts"] < job["max_attempts"]:
            delay = min(600, 20 * (2 ** (job["attempts"] - 1)))
            self.conn.execute("UPDATE ri_jobs SET status='queued', error_code=?, error_message=?, stage='waiting to retry', run_after=?, locked_by=NULL, locked_until=NULL WHERE id=?",
                              (code, message[:500], to_iso(now_dt + timedelta(seconds=delay)), job["id"]))
            return "retry"
        self.conn.execute("UPDATE ri_jobs SET status='failed', error_code=?, error_message=?, finished_at=?, locked_by=NULL, locked_until=NULL WHERE id=?", (code, message[:500], to_iso(now_dt), job["id"]))
        return "failed"

    # ------------------------------------------------------------------ person side
    def _present(self, r) -> Dict[str, Any]:
        return {"id": r["id"], "workspace_id": r["workspace_id"], "source_id": r["source_id"], "kind": r["kind"], "status": r["status"], "progress": r["progress"], "stage": r["stage"],
                "attempts": r["attempts"], "max_attempts": r["max_attempts"], "error_code": r["error_code"], "error_message": r["error_message"], "created_at": r["created_at"],
                "started_at": r["started_at"], "finished_at": r["finished_at"]}

    def list(self, principal: Principal, workspace_id: str, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM ri_jobs WHERE workspace_id = ? AND owner_user_id = ?", [workspace_id, principal.user_id]
        if status:
            sql, args = sql + " AND status = ?", args + [status]
        return [self._present(r) for r in self.conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", args + [min(limit, 200)]).fetchall()]

    def get(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM ri_jobs WHERE id = ? AND owner_user_id = ?", (job_id if isinstance(job_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("job")
        return self._present(r)

    def cancel(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        self.ctx.limiter.hit("ri_job:user", principal.user_id)
        job = self.get(principal, job_id)
        if job["status"] in TERMINAL:
            raise conflict("JOB_FINISHED", "This job has already finished.")
        new = "cancelled" if job["status"] == "queued" else "cancelling"
        self.conn.execute("UPDATE ri_jobs SET status = ?, finished_at = CASE WHEN ? = 'cancelled' THEN ? ELSE finished_at END WHERE id = ? AND owner_user_id = ?", (new, new, self.ctx.clock.iso(), job_id, principal.user_id))
        if new == "cancelled" and job["source_id"]:
            self.conn.execute("UPDATE ri_sources SET status='cancelled', stage='cancelled', updated_at=? WHERE id=? AND owner_user_id=? AND status IN ('queued','processing')", (self.ctx.clock.iso(), job["source_id"], principal.user_id))
        self.ctx.audit.record(EV_JOB, "success", actor=principal.user_id, metadata={"job_id": job_id, "action": "cancel"})
        return self.get(principal, job_id)

    def retry(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        self.ctx.limiter.hit("ri_job:user", principal.user_id)
        job = self.get(principal, job_id)
        if job["status"] not in ("failed", "cancelled"):
            raise conflict("JOB_NOT_RETRYABLE", "Only a failed or cancelled job can be retried.")
        now = self.ctx.clock.iso()
        self.conn.execute("UPDATE ri_jobs SET status='queued', attempts=0, progress=0, stage='queued', error_code=NULL, error_message=NULL, run_after=?, finished_at=NULL WHERE id=? AND owner_user_id=?", (now, job_id, principal.user_id))
        if job["source_id"]:
            self.conn.execute("UPDATE ri_sources SET status='queued', stage='queued', error_code=NULL, error_message=NULL, updated_at=? WHERE id=? AND owner_user_id=?", (now, job["source_id"], principal.user_id))
        self.ctx.audit.record(EV_JOB, "success", actor=principal.user_id, metadata={"job_id": job_id, "action": "retry"})
        return self.get(principal, job_id)
