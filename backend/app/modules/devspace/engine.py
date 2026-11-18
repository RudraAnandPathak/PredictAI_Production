"""Job admission, the job state machine, and the execution worker.

The API process never runs user code: JobService only validates, admits and records; Worker (in scripts/run_dev_worker.py, or driven by tick() in tests) starts the
process through an isolation provider and measures it. Limits are applied by the kernel where the OS allows it and otherwise enforced by the worker killing the whole process group."""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.errors import AppError
from ..analytics import sandbox as osbox
from ..identity.authorization import build_principal
from ..identity.repository import Repository as IdentityRepository
from . import isolation, secrets as secretlib
from .common import (DevService, clean, contains_secret_pattern, dev_error, dev_root, env_python_path, has_dev_access, invalid, kind_of, new_id, not_found, project_dir, put_object_at,
                     quota, record_event, redact_text, safe_path, sha256_bytes)
from .compute import effective_limit

STATES = ("queued", "starting", "running", "cancelling", "succeeded", "failed", "timed_out", "cancelled", "blocked")
TRANSITIONS: Dict[str, set] = {
    "queued": {"starting", "cancelled", "blocked"}, "starting": {"running", "failed", "cancelling", "cancelled", "blocked"},
    "running": {"succeeded", "failed", "timed_out", "cancelling"}, "cancelling": {"cancelled", "failed", "timed_out"},
    "blocked": {"queued"}, "succeeded": set(), "failed": set(), "timed_out": set(), "cancelled": set(),
}
TERMINAL = ("succeeded", "failed", "timed_out", "cancelled", "blocked")
ACTIVE = ("starting", "running", "cancelling")
KINDS = ("script", "notebook", "command")
STALE_SECONDS = 60
MAX_ARTIFACTS_PER_JOB = 100
MAX_MANUAL_RETRIES = 3
BOOT = Path(__file__).with_name("_devboot.py")
EXPLAIN = {
    "memory_limit": "The job used more memory than its limit, so it was stopped. Use less data or ask the owner for a higher limit.",
    "cpu_limit": "The job used more CPU time than allowed and was stopped.", "output_limit": "The job printed more than the output limit and was stopped.",
    "storage_limit": "The job wrote more files than its storage limit and was stopped.", "process_limit": "The job started too many processes and was stopped.",
    "dependency_missing": "A package the code imports is not in the selected environment. Add it to the environment's requirements and rebuild.",
    "syntax_error": "The code has a syntax error. Fix it and run again.", "runtime_error": "The code raised an error. The error text is in the logs.",
    "worker_lost": "The worker handling this job stopped unexpectedly. Your files are untouched; it is safe to run it again.", "isolation_error": "The isolation layer could not start the job.",
    "wall_clock": "The job ran longer than its time limit and was stopped.", "killed": "The job was killed by the system.", "entitlement_revoked": "Special access ended, so the job was stopped.",
    "execution_disabled": "Code execution is switched off on this installation.", "network_isolation_unavailable": "This host cannot enforce the no-network rule, so the job was refused instead of run unsafely.",
    "environment_not_ready": "The selected environment is not ready.", "cancelled": "You cancelled this job.",
}
RETRY_SAFE = {"worker_lost", "isolation_error", "killed", "environment_not_ready", "wall_clock", "memory_limit", "cpu_limit", "output_limit", "storage_limit", "process_limit", "cancelled",
              "runtime_error", "dependency_missing", "syntax_error", "execution_disabled", "network_isolation_unavailable", "entitlement_revoked"}


def entitled(conn: sqlite3.Connection, user_id: str) -> bool:
    """Is this person active and holding Special access RIGHT NOW (recomputed from the database, never cached)?"""
    repo = IdentityRepository(conn)
    user = repo.user_by_id(user_id)
    if user is None or user["status"] != "active" or user["deleted_at"] is not None:
        return False
    return has_dev_access(build_principal(repo, user))


def transition(conn: sqlite3.Connection, job_id: str, to: str, *, frm: Optional[str] = None, **fields: Any) -> bool:
    """Validated, atomic state change. False when the job was no longer in an allowed 'from' state (somebody else moved it first)."""
    row = conn.execute("SELECT status FROM dv_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return False
    cur = row["status"]
    if (frm is not None and cur != frm) or to not in TRANSITIONS.get(cur, set()):
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    n = conn.execute(f"UPDATE dv_jobs SET status = ?{', ' + sets if sets else ''} WHERE id = ? AND status = ?", (to, *fields.values(), job_id, cur)).rowcount
    return n == 1


def present(job, *, detail: bool = False) -> Dict[str, Any]:
    d = {k: job[k] for k in ("id", "project_id", "kind", "status", "priority", "entry", "env_id", "env_version", "attempts", "max_attempts", "retry_of", "dead_letter", "cancel_requested",
                             "exit_code", "error_class", "blocked_reason", "commit_sha", "tree_sha256", "pipeline_run_id", "experiment_id", "isolation", "wall_ms", "cpu_ms", "peak_rss_bytes",
                             "output_bytes", "created_at", "started_at", "finished_at", "network")}
    d["limits"] = json.loads(job["limits"])
    if detail:
        d["argv"] = json.loads(job["argv"])
        d["secret_refs"] = json.loads(job["secret_refs"])
    if job["status"] in TERMINAL and job["status"] != "succeeded":
        cls = job["error_class"] or job["blocked_reason"] or "runtime_error"
        d["recovery"] = {"what_failed": EXPLAIN.get(cls, "The job did not finish."), "when": job["finished_at"], "data_preserved": True,
                         "retry_safe": cls in RETRY_SAFE or job["status"] == "cancelled", "actions": ["view_logs", "retry"] + (["edit_environment"] if cls == "dependency_missing" else [])}
    return d


class JobService(DevService):
    """Everything the API does with jobs. Nothing here executes code."""

    def _limits(self, project, requested: Optional[Dict[str, Any]], kind: str) -> Dict[str, Any]:
        e = lambda k: effective_limit(self.conn, self.ctx.settings, project["owner_user_id"], project["id"], k)  # noqa: E731
        wall, mem, cpu, out, procs, stor = e("job_wall_seconds"), e("job_memory_mb"), e("job_cpu_seconds"), e("job_output_kb"), e("job_max_procs"), e("job_storage_mb")
        req = requested or {}
        for key, cap in (("wall_seconds", wall), ("memory_mb", mem), ("cpu_seconds", cpu)):
            if key in req:
                v = req[key]
                if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                    raise invalid(key, "Use a positive whole number.")
        wall = min(wall, int(req.get("wall_seconds", wall))); mem = min(mem, int(req.get("memory_mb", mem))); cpu = min(cpu, int(req.get("cpu_seconds", cpu)))
        return {"wall_seconds": wall, "cpu_seconds": min(cpu, wall * 4), "memory_mb": mem, "output_kb": out, "max_procs": procs, "storage_mb": stor}

    def create_job(self, project, owner_id: str, *, kind: str, entry: Optional[str] = None, argv: Optional[List[str]] = None, env_id: Optional[str] = None, priority: int = 100,
                   secret_refs: Optional[List[str]] = None, max_attempts: int = 1, limits: Optional[Dict[str, Any]] = None, experiment_id: Optional[str] = None,
                   pipeline_run_id: Optional[str] = None, retry_of: Optional[str] = None, attempts: int = 0, network: str = "none", actor: Optional[str] = None) -> str:
        env_version = None
        if env_id:
            env = self.conn.execute("SELECT * FROM dv_envs WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (env_id, project["id"])).fetchone()
            if env is None:
                raise not_found("environment")
            env_version = env["current_version"]
        jid, now = new_id(), self.now()
        self.conn.execute("INSERT INTO dv_jobs (id, project_id, owner_user_id, kind, status, priority, entry, argv, env_id, env_version, limits, network, secret_refs, attempts, max_attempts, retry_of, "
                          "experiment_id, pipeline_run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (jid, project["id"], owner_id, kind, "queued", priority, entry, json.dumps(argv or []), env_id, env_version, json.dumps(limits or self._limits(project, None, kind)), network,
                           json.dumps(secret_refs or []), attempts, max_attempts, retry_of, experiment_id, pipeline_run_id, now))
        record_event(self.conn, now, project["id"], actor or owner_id, "job.submitted", "job", jid, kind=kind, entry=entry, env_id=env_id, retry_of=retry_of)
        return jid

    def submit(self, principal, project_id: Any, *, kind: Any = None, entry: Any = None, argv: Any = None, env_id: Any = None, priority: Any = 100, secret_refs: Any = None,
               max_attempts: Any = 1, limits: Any = None, experiment_id: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_submit:user", principal)
        s = self.ctx.settings
        if s.dev_execution == "off":
            raise dev_error("EXECUTION_DISABLED", EXPLAIN["execution_disabled"], 403)
        if kind not in KINDS:
            raise invalid("kind", "Choose script, notebook or command.")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 1000:
            raise invalid("priority", "Use a whole number from 1 (first) to 1000.")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
            raise invalid("max_attempts", "Use 1 to 3.")
        if kind in ("script", "notebook"):
            rel = safe_path(entry)
            want = ".py" if kind == "script" else ".ipynb"
            if not rel.endswith(want) or self.conn.execute("SELECT 1 FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (project["id"], rel)).fetchone() is None:
                raise invalid("entry", f"Choose an existing {want} file in this project.")
            run_argv = [str(a) for a in (argv or [])]
        else:
            entry = None
            run_argv = self._command_argv(argv)
        if len(run_argv) > 20 or any(len(a) > 500 or "\x00" in a for a in run_argv):
            raise invalid("argv", "Use at most 20 arguments of up to 500 characters.")
        if env_id is not None:
            e = self.conn.execute("SELECT * FROM dv_envs WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (env_id if isinstance(env_id, str) else "", project["id"])).fetchone()
            if e is None:
                raise not_found("environment")
            v = self.conn.execute("SELECT status FROM dv_env_versions WHERE env_id = ? AND version = ?", (e["id"], e["current_version"])).fetchone()
            if v is None or v["status"] != "ready":
                raise dev_error("ENVIRONMENT_NOT_READY", "That environment is not built yet or failed to build. Check its status first.", 409)
        names = [n for n in (secret_refs or [])]
        if not isinstance(secret_refs or [], list) or len(names) > 10:
            raise invalid("secret_refs", "Give up to 10 secret names.")
        for n in names:
            if self.conn.execute("SELECT 1 FROM dv_secrets WHERE project_id = ? AND name = ? AND purpose = 'job' AND revoked_at IS NULL", (project["id"], n if isinstance(n, str) else "")).fetchone() is None:
                raise not_found("secret")
        # admission control
        depth = effective_limit(self.conn, s, project["owner_user_id"], project["id"], "queue_depth")
        waiting = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE status = 'queued'").fetchone()[0])
        mine = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE owner_user_id = ? AND status IN ('queued','starting','running','cancelling')", (principal.user_id,)).fetchone()[0])
        if waiting >= depth or mine >= max(5, 3 * effective_limit(self.conn, s, principal.user_id, project["id"], "user_concurrency")):
            self.event(project["id"], principal, "job.rejected", "job", None, "denied", reason="queue_full")
            raise quota("QUEUE_FULL", "The job queue is full. Wait for a job to finish or cancel one.")
        lim = self._limits(project, limits, kind)
        budget = effective_limit(self.conn, s, principal.user_id, project["id"], "user_cpu_seconds_per_day")
        day = self.clock.now().strftime("%Y-%m-%dT00:00:00")
        used = int(self.conn.execute("SELECT COALESCE(SUM(cpu_ms),0) FROM dv_jobs WHERE owner_user_id = ? AND created_at >= ?", (principal.user_id, day)).fetchone()[0]) // 1000
        if used >= budget:
            self.event(project["id"], principal, "job.rejected", "job", None, "denied", reason="cpu_budget")
            raise quota("COMPUTE_BUDGET_EXHAUSTED", f"You have used today's compute allowance ({used} of {budget} CPU seconds). It resets at midnight UTC.")
        jid = self.create_job(project, principal.user_id, kind=kind, entry=entry, argv=run_argv, env_id=env_id, priority=priority, secret_refs=names, max_attempts=max_attempts, limits=lim,
                              experiment_id=experiment_id if isinstance(experiment_id, str) else None)
        return present(self._row(project["id"], jid), detail=True)

    @staticmethod
    def _command_argv(argv: Any) -> List[str]:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or argv[0] != "python":
            raise invalid("argv", "A command must be a list starting with \"python\", for example [\"python\", \"-c\", \"print(1)\"]. Shells are not available.")
        rest = argv[1:]
        if not rest or (rest[0] in ("-c", "-m") and len(rest) < 2):
            raise invalid("argv", "Give python a script path, -c CODE or -m MODULE.")
        if rest[0] not in ("-c", "-m"):
            safe_path(rest[0])
        return rest

    # ------------------------------------------------------------------ reading
    def _row(self, project_id: str, job_id: Any):
        row = self.conn.execute("SELECT * FROM dv_jobs WHERE id = ? AND project_id = ?", (job_id if isinstance(job_id, str) else "", project_id)).fetchone()
        if row is None:
            raise not_found("job")
        return row

    def get(self, principal, project_id: Any, job_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        return present(self._row(project["id"], job_id), detail=True)

    def list(self, principal, project_id: Any, status: Any = None, limit: int = 50) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        sql, args = "SELECT * FROM dv_jobs WHERE project_id = ?", [project["id"]]
        if status is not None:
            if status not in STATES:
                raise invalid("status", "Unknown status.")
            sql, args = sql + " AND status = ?", args + [status]
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id LIMIT ?", args + [max(1, min(int(limit), 200))]).fetchall()
        return {"items": [present(r) for r in rows]}

    def logs(self, principal, project_id: Any, job_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        job = self._row(project["id"], job_id)
        out = {r["stream"]: {"text": r["text"], "truncated": bool(r["truncated"])} for r in self.conn.execute("SELECT * FROM dv_job_output WHERE job_id = ?", (job["id"],)).fetchall()}
        return {"job_id": job["id"], "status": job["status"], "stdout": out.get("stdout", {"text": "", "truncated": False}), "stderr": out.get("stderr", {"text": "", "truncated": False}),
                "exit_code": job["exit_code"], "note": "Secret values and credentials are removed from logs before they are stored."}

    # ------------------------------------------------------------------ control
    def cancel(self, principal, project_id: Any, job_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)          # cancelling is allowed even while the workspace is suspended
        job = self._row(project["id"], job_id)
        if job["owner_user_id"] != principal.user_id and not principal.is_owner:
            raise not_found("job")
        now = self.now()
        if job["status"] == "queued":
            transition(self.conn, job["id"], "cancelled", frm="queued", finished_at=now, error_class="cancelled", cancel_requested=1)
        elif job["status"] in ("starting", "running"):
            transition(self.conn, job["id"], "cancelling", cancel_requested=1)
        elif job["status"] == "cancelling":
            pass
        else:
            raise dev_error("JOB_NOT_CANCELLABLE", "This job already finished.", 409)
        self.event(project["id"], principal, "job.cancel_requested", "job", job["id"])
        return present(self._row(project["id"], job["id"]), detail=True)

    def retry(self, principal, project_id: Any, job_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        job = self._row(project["id"], job_id)
        if job["status"] not in TERMINAL or job["status"] == "succeeded":
            raise dev_error("JOB_NOT_RETRYABLE", "Only failed, timed-out, cancelled or blocked jobs can be run again.", 409)
        chain, cur = 0, job
        while cur is not None and cur["retry_of"] and chain < 10:
            chain, cur = chain + 1, self.conn.execute("SELECT * FROM dv_jobs WHERE id = ?", (cur["retry_of"],)).fetchone()
        if chain >= MAX_MANUAL_RETRIES:
            raise dev_error("RETRY_LIMIT", f"This job has already been retried {chain} times. Change the code or environment first.", 409)
        if job["kind"] not in KINDS:
            raise dev_error("JOB_NOT_RETRYABLE", "Run this from where it was started (environment build or pipeline).", 409)
        new = self.submit(principal, project["id"], kind=job["kind"], entry=job["entry"], argv=(["python"] + json.loads(job["argv"])) if job["kind"] == "command" else json.loads(job["argv"]),
                          env_id=job["env_id"], priority=job["priority"], secret_refs=json.loads(job["secret_refs"]), max_attempts=job["max_attempts"], experiment_id=job["experiment_id"])
        self.conn.execute("UPDATE dv_jobs SET retry_of = ? WHERE id = ?", (job["id"], new["id"]))
        new["retry_of"] = job["id"]
        return new

    def artifacts(self, principal, project_id: Any, job_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        job = self._row(project["id"], job_id)
        rows = self.conn.execute("SELECT * FROM dv_artifacts WHERE job_id = ? AND deleted_at IS NULL ORDER BY name", (job["id"],)).fetchall()
        return {"items": [{"id": r["id"], "name": r["name"], "bucket": r["bucket"], "version": r["version"], "kind": r["kind"], "sha256": r["sha256"], "bytes": r["bytes"]} for r in rows]}


# ====================================================================================================================== worker
class Worker:
    """Takes queued jobs and runs them. One instance per worker process; it owns its own database connection."""

    def __init__(self, db, settings, clock, *, poll: float = 0.2) -> None:
        self.db, self.settings, self.clock, self.poll = db, settings, clock, poll
        self.conn = db.connect()
        self.identity = IdentityRepository(self.conn)
        self.provider = isolation.provider_for(settings)
        self.pid = os.getpid()

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return self.clock.iso()

    # ------------------------------------------------------------------ housekeeping
    def entitled(self, user_id: str) -> bool:
        return entitled(self.conn, user_id)

    def reconcile(self) -> Dict[str, int]:
        """Revocation convergence: whoever lost Special access has queued jobs blocked, running jobs cancelled and sessions stopped. Idempotent; safe to call on every tick."""
        from . import sessions
        counts = {"blocked": 0, "cancelling": 0, "sessions": 0, "suspended": 0}
        now = self.now()
        owners = {r[0] for r in self.conn.execute("SELECT DISTINCT owner_user_id FROM dv_jobs WHERE status IN ('queued','starting','running') UNION SELECT DISTINCT owner_user_id FROM dv_sessions "
                                                   "WHERE status IN ('starting','running') UNION SELECT DISTINCT owner_user_id FROM dv_workspaces WHERE status = 'active'").fetchall()}
        for uid in owners:
            if self.entitled(uid):
                continue
            for j in self.conn.execute("SELECT id, project_id, status FROM dv_jobs WHERE owner_user_id = ? AND status IN ('queued','starting','running')", (uid,)).fetchall():
                if j["status"] == "queued":
                    if transition(self.conn, j["id"], "blocked", frm="queued", blocked_reason="entitlement_revoked", finished_at=now):
                        counts["blocked"] += 1
                elif transition(self.conn, j["id"], "cancelling", cancel_requested=1, blocked_reason="entitlement_revoked"):
                    counts["cancelling"] += 1
                record_event(self.conn, now, j["project_id"], None, "job.stopped_entitlement_revoked", "job", j["id"], "denied")
            counts["sessions"] += sessions.stop_all_for_user(self.conn, now, uid, reason="special access revoked")
            n = self.conn.execute("UPDATE dv_workspaces SET status = 'suspended', suspended_reason = 'special access revoked', updated_at = ? WHERE owner_user_id = ? AND status = 'active'", (now, uid)).rowcount
            counts["suspended"] += n
            if n:
                for w in self.conn.execute("SELECT project_id FROM dv_workspaces WHERE owner_user_id = ?", (uid,)).fetchall():
                    record_event(self.conn, now, w["project_id"], None, "workspace.suspended", "workspace", w["project_id"], "denied", reason="special access revoked")
        return counts

    def recover_stale(self) -> int:
        now_dt = self.clock.now()
        n = 0
        for j in self.conn.execute("SELECT * FROM dv_jobs WHERE status IN ('starting','running','cancelling')").fetchall():
            beat = j["heartbeat_at"] or j["started_at"] or j["created_at"]
            from ...core.clock import from_iso
            if (now_dt - from_iso(beat)).total_seconds() < STALE_SECONDS:
                continue
            if j["worker_pid"] and j["worker_pid"] != self.pid:
                try:
                    os.killpg(j["worker_pid"], signal.SIGKILL)       # an orphan left by a dead worker must not keep consuming resources
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            now = self.now()
            to = "cancelled" if j["status"] == "cancelling" else "failed"
            if transition(self.conn, j["id"], to, error_class="cancelled" if to == "cancelled" else "worker_lost", finished_at=now):
                n += 1
                record_event(self.conn, now, j["project_id"], None, "job.recovered", "job", j["id"], "failure", status=to)
                if to == "failed" and j["attempts"] < j["max_attempts"] and j["kind"] in KINDS:
                    nid = new_id()
                    self.conn.execute("INSERT INTO dv_jobs (id, project_id, owner_user_id, kind, status, priority, entry, argv, env_id, env_version, limits, network, secret_refs, attempts, max_attempts, "
                                      "retry_of, experiment_id, pipeline_run_id, created_at) SELECT ?, project_id, owner_user_id, kind, 'queued', priority, entry, argv, env_id, env_version, limits, "
                                      "network, secret_refs, attempts, max_attempts, id, experiment_id, pipeline_run_id, ? FROM dv_jobs WHERE id = ?", (nid, now, j["id"]))
                elif to == "failed" and j["max_attempts"] > 1 and j["attempts"] >= j["max_attempts"]:
                    self.conn.execute("UPDATE dv_jobs SET dead_letter = 1 WHERE id = ?", (j["id"],))
        return n

    # ------------------------------------------------------------------ queue
    def _pick(self):
        s = self.settings
        best, best_key = None, None
        for j in self.conn.execute("SELECT * FROM dv_jobs WHERE status = 'queued' ORDER BY priority, created_at, id LIMIT 100").fetchall():
            ru = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE owner_user_id = ? AND status IN ('starting','running','cancelling')", (j["owner_user_id"],)).fetchone()[0])
            rp = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE project_id = ? AND status IN ('starting','running','cancelling')", (j["project_id"],)).fetchone()[0])
            if ru >= effective_limit(self.conn, s, j["owner_user_id"], j["project_id"], "user_concurrency") or rp >= effective_limit(self.conn, s, j["owner_user_id"], j["project_id"], "project_concurrency"):
                continue
            key = (j["priority"], ru, j["created_at"])      # priority first, then whoever has the fewest jobs running (fairness), then age
            if best_key is None or key < best_key:
                best, best_key = j, key
        return best

    def tick(self) -> Optional[str]:
        """One scheduling step. Returns the id of the job that was run, or None. Runs the job to completion in this thread (the worker process exists for that)."""
        self.reconcile()
        self.recover_stale()
        try:
            from . import sessions
            sessions.sweep(self.conn, self.clock)
            from . import artifacts as _art
            _art.purge_expired(self.conn, self.clock)
            from . import pipelines
            pipelines.advance_all(self.conn, self.settings, self.clock)
        except Exception:  # noqa: BLE001 - a pipeline bug must never stop the queue
            pass
        job = self._pick()
        if job is None:
            return None
        if not transition(self.conn, job["id"], "starting", frm="queued", started_at=self.now(), worker_pid=None, heartbeat_at=self.now(), attempts=job["attempts"] + 1):
            return None
        self._execute(self.conn.execute("SELECT * FROM dv_jobs WHERE id = ?", (job["id"],)).fetchone())
        return job["id"]

    def run_forever(self, stop: Callable[[], bool]) -> None:
        while not stop():
            if self.tick() is None:
                time.sleep(self.poll)

    # ------------------------------------------------------------------ one job
    def _block(self, job, reason: str, detail: str = "") -> None:
        now = self.now()
        transition(self.conn, job["id"], "blocked", blocked_reason=reason, error_class=reason, finished_at=now)
        record_event(self.conn, now, job["project_id"], None, "job.blocked", "job", job["id"], "denied", reason=reason, detail=detail)
        self.conn.execute("INSERT OR REPLACE INTO dv_job_output (job_id, stream, text, truncated) VALUES (?, 'stderr', ?, 0)", (job["id"], EXPLAIN.get(reason, reason)))

    def _execute(self, job) -> None:
        s, conn = self.settings, self.conn
        jid, pid = job["id"], job["project_id"]
        if job["kind"] == "env_build":
            from . import envs
            envs.run_build(self, job)
            return
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        ws = conn.execute("SELECT status FROM dv_workspaces WHERE project_id = ?", (pid,)).fetchone()
        if not self.entitled(job["owner_user_id"]) or project is None or project["status"] != "active":
            return self._block(job, "entitlement_revoked" if not self.entitled(job["owner_user_id"]) else "project_unavailable")
        if self.provider is None or s.dev_execution == "off":
            return self._block(job, "execution_disabled")
        if self.provider.network_isolation() is None:
            return self._block(job, "network_isolation_unavailable")
        limits = json.loads(job["limits"])
        base = project_dir(s, pid) / "jobs" / jid
        work, out_dir, data_dir, tmp, home, boot = base / "work", base / "outputs", base / "data", base / "tmp", base / "home", base / "boot"
        try:
            for d in (work, out_dir, tmp, home, boot):
                d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BOOT, boot / "_devboot.py")
            from .files import FileService
            tree_dir = project_dir(s, pid) / "tree"
            rows = conn.execute("SELECT path FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (pid,)).fetchall()
            tree_hash = _tree_hash(conn, pid)
            for r in rows:
                src = (tree_dir / r["path"])
                if src.is_file() and not src.is_symlink():
                    dst = work / r["path"]
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            has_data = self._materialise_data(conn, project, data_dir, limits)
            commit = _head_commit(tree_dir)
            conn.execute("UPDATE dv_jobs SET tree_sha256 = ?, commit_sha = ?, isolation = ? WHERE id = ?", (tree_hash, commit, self.provider.name + ": " + (self.provider.network_isolation() or ""), jid))
        except OSError as e:
            transition(conn, jid, "failed", error_class="isolation_error", finished_at=self.now())
            conn.execute("INSERT OR REPLACE INTO dv_job_output (job_id, stream, text, truncated) VALUES (?, 'stderr', ?, 0)", (jid, "Could not prepare the job folder: " + type(e).__name__))
            shutil.rmtree(base, ignore_errors=True)
            return
        # interpreter
        docker = self.provider.name == "docker"
        py = "python"
        if not docker:
            py = sys.executable
            if job["env_id"]:
                p = env_python_path(s, pid, job["env_id"], job["env_version"])
                ver = conn.execute("SELECT status FROM dv_env_versions WHERE env_id = ? AND version = ?", (job["env_id"], job["env_version"])).fetchone()
                if not p.exists() or ver is None or ver["status"] != "ready":
                    shutil.rmtree(base, ignore_errors=True)
                    return self._block(job, "environment_not_ready")
                py = str(p)
        # secrets (scoped to this job's own project and names; values live only in the child's environment)
        secret_values: Dict[str, str] = {}
        names = json.loads(job["secret_refs"])
        try:
            secret_values = secretlib.resolve_values(conn, s, self.now(), pid, names) if names else {}
        except AppError:
            shutil.rmtree(base, ignore_errors=True)
            return self._block(job, "secret_unavailable")
        for n in names:
            record_event(conn, self.now(), pid, job["owner_user_id"], "secret.used", "secret", n, "success", job_id=jid)
        out_path, in_work = ("/outputs" if docker else str(out_dir)), ("/work" if docker else str(work))
        boot_path = "/boot/_devboot.py" if docker else str(boot / "_devboot.py")
        argv_user = json.loads(job["argv"])
        kas = "1" if osbox.hard_memory_limit_supported() else "0"
        head = [py, boot_path]
        if job["kind"] == "script":
            cmd = head + ["script", str(limits["cpu_seconds"]), str(limits["memory_mb"]), kas, str(limits["output_kb"] // 1024 + 64), job["entry"], *argv_user]
        elif job["kind"] == "notebook":
            cmd = head + ["notebook", str(limits["cpu_seconds"]), str(limits["memory_mb"]), kas, str(limits["output_kb"] // 1024 + 64), job["entry"], out_path + "/executed.ipynb"]
        else:
            cmd = head + ["command", str(limits["cpu_seconds"]), str(limits["memory_mb"]), kas, str(limits["output_kb"] // 1024 + 64), *argv_user]
        env = {"PATH": (str(Path(py).parent) + ":" if not docker else "") + "/usr/local/bin:/usr/bin:/bin", "HOME": str(home) if not docker else "/tmp", "TMPDIR": str(tmp) if not docker else "/tmp",
               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8", "PREDICTAI_OUTPUT_DIR": out_path, "PREDICTAI_DATA_DIR": ("/data" if docker else str(data_dir)),
               "PREDICTAI_JOB_ID": jid, **secret_values}
        if docker:    # the docker CLIENT needs the real PATH/HOME and its own DOCKER_* settings to find the daemon; none of them are forwarded into the container (see DockerProvider.wrap)
            env.update({k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT") if k in os.environ})
        if job["experiment_id"]:
            ex = conn.execute("SELECT params, seed FROM dv_experiments WHERE id = ? AND project_id = ?", (job["experiment_id"], pid)).fetchone()
            if ex is not None:
                env["PREDICTAI_EXPERIMENT_PARAMS"] = ex["params"]
                if ex["seed"] is not None:
                    env["PREDICTAI_SEED"] = str(ex["seed"])
        try:
            argv = self.provider.wrap(cmd, workdir=str(work), env=env, limits={"max_procs": limits["max_procs"], "memory_mb": limits["memory_mb"]}, data_dir=str(data_dir) if has_data else None,
                                      out_dir=str(out_dir), boot_dir=str(boot))
        except RuntimeError:
            shutil.rmtree(base, ignore_errors=True)
            return self._block(job, "network_isolation_unavailable")
        result = self._run_process(job, argv, env, str(work), base, out_dir, limits)
        self._finish(job, result, base, out_dir, work, secret_values, tree_hash)

    def _materialise_data(self, conn, project, data_dir: Path, limits: Dict[str, Any]) -> bool:
        """Read-only copies of the project's own Data Workspace files (originals, and derived versions under derived/). Another project's files are never reachable from here."""
        from ..storage import LocalStorage
        storage = LocalStorage(self.settings.storage_dir)
        budget = int(limits["storage_mb"]) * 1024 * 1024 // 2
        made = False
        for a in conn.execute("SELECT id, name FROM assets WHERE project_id = ? AND owner_user_id = ? AND status = 'ready'", (project["id"], project["owner_user_id"])).fetchall():
            for v in conn.execute("SELECT version_number, kind, storage_key, size_bytes FROM asset_versions WHERE asset_id = ? ORDER BY version_number", (a["id"],)).fetchall():
                if (v["size_bytes"] or 0) > budget:
                    continue
                name = os.path.basename(a["name"]).replace("/", "_") or "file"
                dst = data_dir / (name if v["kind"] == "original" else f"derived/{name}.v{v['version_number']}")
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with storage.open(v["storage_key"]) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(dst, 0o444)
                    budget -= v["size_bytes"] or 0
                    made = True
                except Exception:  # noqa: BLE001 - a missing stored object is simply not offered
                    continue
        return made

    def _run_process(self, job, argv, env, cwd: str, base: Path, out_dir: Path, limits) -> Dict[str, Any]:
        conn, jid = self.conn, job["id"]
        so, se = base / "stdout.txt", base / "stderr.txt"
        max_out = int(limits["output_kb"]) * 1024
        mem_bytes = int(limits["memory_mb"]) * 1024 * 1024
        storage_cap = int(limits["storage_mb"]) * 1024 * 1024
        started = time.monotonic()
        killed: Optional[str] = None
        status_code, rusage = None, None
        with open(so, "wb") as fo, open(se, "wb") as fe:
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, close_fds=True, start_new_session=True)
            except OSError as e:
                return {"killed": None, "returncode": None, "error": f"could not start: {type(e).__name__}", "wall": 0.0, "cpu": 0.0, "rss": 0}
            transition(conn, jid, "running", frm="starting", worker_pid=proc.pid, heartbeat_at=self.now())
            if conn.execute("SELECT cancel_requested FROM dv_jobs WHERE id = ?", (jid,)).fetchone()[0]:
                killed = "cancel"
            peak, last_beat, last_revoke = 0, time.monotonic(), time.monotonic()
            while True:
                try:
                    wpid, st, ru = os.wait4(proc.pid, os.WNOHANG)
                except ChildProcessError:
                    wpid, st, ru = proc.pid, 0, None
                if wpid != 0:
                    status_code, rusage = st, ru
                    break
                now_m = time.monotonic()
                rss, nproc = _group_stats(proc.pid)
                peak = max(peak, rss)
                if killed is None:
                    if now_m - started > limits["wall_seconds"]:
                        killed = "time"
                    elif nproc > limits["max_procs"]:
                        killed = "procs"
                    elif rss > mem_bytes:
                        killed = "memory"
                    elif not so.exists() or not se.exists():
                        killed = "cancel"                      # the job folder was deleted under us (project/account deletion)
                    elif so.stat().st_size + se.stat().st_size > max_out:
                        killed = "output"
                    elif _dir_bytes(base) > storage_cap:
                        killed = "storage"
                    else:
                        row = conn.execute("SELECT cancel_requested, status, blocked_reason FROM dv_jobs WHERE id = ?", (jid,)).fetchone()
                        if row is None:
                            killed = "cancel"                      # the job (its project was deleted) no longer exists: stop and clean up
                        elif row["cancel_requested"] or row["status"] == "cancelling":
                            killed = "revoked" if row["blocked_reason"] == "entitlement_revoked" else "cancel"
                        elif now_m - last_revoke > 1.0:
                            last_revoke = now_m
                            if not self.entitled(job["owner_user_id"]):
                                killed = "revoked"
                if killed:
                    _kill_group(proc.pid)
                    try:
                        _, status_code, rusage = os.wait4(proc.pid, 0)
                    except ChildProcessError:
                        pass
                    break
                if now_m - last_beat > 2.0:
                    last_beat = now_m
                    conn.execute("UPDATE dv_jobs SET heartbeat_at = ? WHERE id = ?", (self.now(), jid))
                time.sleep(0.05)
            _kill_group(proc.pid)                              # nothing may outlive the job (daemons, grandchildren)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and _group_stats(proc.pid)[1] > 0:
                _kill_group(proc.pid)
                time.sleep(0.05)
        code = None if status_code is None else os.waitstatus_to_exitcode(status_code)
        proc.returncode = code if code is not None else 0          # we reaped it ourselves; tell subprocess so it does not warn about a "still running" child
        cpu = (rusage.ru_utime + rusage.ru_stime) if rusage is not None else 0.0
        # ru_maxrss is NOT used: on Linux a forked child inherits the parent's high-water mark, so it would report the worker's own size. The polled group total is the honest figure (0 = job too short to sample).
        return {"killed": killed, "returncode": code, "wall": time.monotonic() - started, "cpu": cpu, "rss": peak}

    def _finish(self, job, r: Dict[str, Any], base: Path, out_dir: Path, work: Path, secrets: Dict[str, str], tree_hash: str) -> None:
        conn, jid, pid = self.conn, job["id"], job["project_id"]
        fresh = conn.execute("SELECT * FROM dv_jobs WHERE id = ?", (jid,)).fetchone()
        if fresh is None:
            shutil.rmtree(base, ignore_errors=True)               # the project was deleted while the job ran: nothing to record
            return
        job = fresh                                               # the row as it is now (snapshot hash, commit and isolation were written after the worker first read it)
        limits = json.loads(job["limits"])
        values = list(secrets.values())
        max_out = int(limits["output_kb"]) * 1024
        texts, truncated, total = {}, {}, 0
        for stream in ("stdout", "stderr"):
            data = (base / f"{stream}.txt").read_bytes() if (base / f"{stream}.txt").exists() else b""
            total += len(data)
            truncated[stream] = len(data) > max_out
            texts[stream] = redact_text(data[:max_out].decode("utf-8", "replace"), values)
        if r.get("error"):
            texts["stderr"] += r["error"]
        killed, code = r["killed"], r["returncode"]
        status, cls = "failed", None
        if killed == "time":
            status, cls = "timed_out", "wall_clock"
        elif killed == "cancel":
            status, cls = "cancelled", "cancelled"
        elif killed == "revoked":
            status, cls = "cancelled", "entitlement_revoked"
        elif killed == "memory":
            cls = "memory_limit"
        elif killed == "procs":
            cls = "process_limit"
        elif killed == "output":
            cls = "output_limit"
        elif killed == "storage":
            cls = "storage_limit"
        elif code == 0:
            status = "succeeded"
        else:
            err = texts["stderr"]
            if code is not None and code < 0 and -code == signal.SIGXCPU:
                cls = "cpu_limit"
            elif code is not None and code < 0 and -code == signal.SIGXFSZ:
                cls = "output_limit"
            elif code is not None and code < 0 and -code == signal.SIGKILL:
                cls = "memory_limit" if "MemoryError" in err else "killed"
            elif r["cpu"] >= limits["cpu_seconds"] * 0.95:
                cls = "cpu_limit"
            elif "MemoryError" in err:
                cls = "memory_limit"
            elif "ModuleNotFoundError" in err or "ImportError" in err:
                cls = "dependency_missing"
            elif "SyntaxError" in err:
                cls = "syntax_error"
            elif err.startswith("unshare:") or r.get("error"):
                cls = "isolation_error"
            else:
                cls = "runtime_error"
        now = self.now()
        for stream in ("stdout", "stderr"):
            conn.execute("INSERT OR REPLACE INTO dv_job_output (job_id, stream, text, truncated) VALUES (?,?,?,?)", (jid, stream, texts[stream], int(truncated[stream])))
        arts = self._collect_artifacts(job, out_dir, work, tree_hash, limits, values) if status in ("succeeded", "failed", "timed_out") else 0
        if job["kind"] == "notebook":
            self._write_back_notebook(job, out_dir)
        fields = dict(finished_at=now, exit_code=code, error_class=cls, wall_ms=int(r["wall"] * 1000), cpu_ms=int(r["cpu"] * 1000), peak_rss_bytes=int(r["rss"]), output_bytes=total)
        if killed == "revoked":
            fields["blocked_reason"] = "entitlement_revoked"
        cur = conn.execute("SELECT status FROM dv_jobs WHERE id = ?", (jid,)).fetchone()[0]
        if status == "cancelled" and cur == "running":
            transition(conn, jid, "cancelling")                      # running -> cancelling -> cancelled, never a skipped state
        elif status == "succeeded" and cur == "cancelling":
            status = "cancelled"                                     # a cancel that arrived before the exit wins
        transition(conn, jid, status, **fields)
        if status == "succeeded" and job["experiment_id"]:
            from . import experiments
            experiments.on_job_done(conn, now, job, out_dir)
        elif job["experiment_id"]:
            from . import experiments
            experiments.on_job_failed(conn, now, job, cls)
        record_event(conn, now, pid, None, "job." + status, "job", jid, "success" if status == "succeeded" else "failure", error_class=cls, exit_code=code, wall_ms=fields["wall_ms"], artifacts=arts)
        retry_ok = status == "failed" and cls in ("worker_lost", "isolation_error") and job["attempts"] < job["max_attempts"]
        if retry_ok:
            conn.execute("INSERT INTO dv_jobs (id, project_id, owner_user_id, kind, status, priority, entry, argv, env_id, env_version, limits, network, secret_refs, attempts, max_attempts, retry_of, "
                         "experiment_id, pipeline_run_id, created_at) SELECT ?, project_id, owner_user_id, kind, 'queued', priority, entry, argv, env_id, env_version, limits, network, secret_refs, "
                         "attempts, max_attempts, id, experiment_id, pipeline_run_id, ? FROM dv_jobs WHERE id = ?", (new_id(), now, jid))
        elif status == "failed" and job["max_attempts"] > 1 and job["attempts"] >= job["max_attempts"]:
            conn.execute("UPDATE dv_jobs SET dead_letter = 1 WHERE id = ?", (jid,))
        shutil.rmtree(base, ignore_errors=True)                    # temporary resources are released; diagnostics are in the database

    def _collect_artifacts(self, job, out_dir: Path, work: Path, tree_hash: str, limits, secret_values: List[str]) -> int:
        conn, pid = self.conn, job["project_id"]
        project = conn.execute("SELECT owner_user_id FROM projects WHERE id = ?", (pid,)).fetchone()
        cap = effective_limit(conn, self.settings, job["owner_user_id"], pid, "project_storage_mb") * 1024 * 1024
        used = int(conn.execute("SELECT COALESCE(SUM(bytes),0) FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (pid,)).fetchone()[0]) + \
            int(conn.execute("SELECT COALESCE(SUM(bytes),0) FROM dv_artifacts WHERE project_id = ? AND deleted_at IS NULL", (pid,)).fetchone()[0])
        n = 0
        for dirpath, dirnames, filenames in os.walk(out_dir):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if full.is_symlink() or n >= MAX_ARTIFACTS_PER_JOB:
                    continue
                rel = full.relative_to(out_dir).as_posix()
                try:
                    safe_path(rel, allow_blocked=False)
                    data = full.read_bytes()
                except Exception:  # noqa: BLE001
                    continue
                if used + len(data) > cap or len(data) > int(limits["storage_mb"]) * 1024 * 1024:
                    record_event(conn, self.now(), pid, None, "artifact.quota_denied", "artifact", rel, "denied", bytes=len(data))
                    continue
                if name.endswith((".json", ".txt", ".csv", ".md", ".ipynb", ".py", ".log")):
                    text = data.decode("utf-8", "replace")
                    clean = redact_text(text, secret_values)
                    if clean != text:
                        data = clean.encode()
                sha = put_object_at(self.settings, pid, data)
                last = conn.execute("SELECT MAX(version) FROM dv_artifacts WHERE project_id = ? AND bucket = 'outputs' AND name = ?", (pid, rel)).fetchone()[0] or 0
                prov = {"job_id": job["id"], "commit_sha": job["commit_sha"], "tree_sha256": tree_hash, "env_id": job["env_id"], "env_version": job["env_version"], "experiment_id": job["experiment_id"]}
                conn.execute("INSERT INTO dv_artifacts (id, project_id, owner_user_id, bucket, name, version, kind, sha256, bytes, job_id, experiment_id, provenance, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (new_id(), pid, project["owner_user_id"], "outputs", rel, last + 1, kind_of(rel), sha, len(data), job["id"], job["experiment_id"], json.dumps(prov), self.now()))
                used += len(data)
                n += 1
        return n

    def _write_back_notebook(self, job, out_dir: Path) -> None:
        """The executed notebook (with outputs) replaces the project's notebook as a new version, so a person sees the results when they reopen it."""
        done = out_dir / "executed.ipynb"
        if not done.is_file() or not job["entry"]:
            return
        try:
            data = done.read_bytes()
            if len(data) > 1024 * 1024:
                return
            sha = put_object_at(self.settings, job["project_id"], data)
            row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (job["project_id"], job["entry"])).fetchone()
            if row is None or row["sha256"] == sha:
                return
            now = self.now()
            v = row["version"] + 1
            self.conn.execute("UPDATE dv_files SET version = ?, sha256 = ?, bytes = ?, updated_at = ? WHERE id = ?", (v, sha, len(data), now, row["id"]))
            self.conn.execute("INSERT INTO dv_file_versions (file_id, version, sha256, bytes, author_user_id, created_at) VALUES (?,?,?,?,?,?)", (row["id"], v, sha, len(data), job["owner_user_id"], now))
            target = project_dir(self.settings, job["project_id"]) / "tree" / job["entry"]
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            record_event(self.conn, now, job["project_id"], None, "notebook.executed", "file", job["entry"], "success", job_id=job["id"], version=v)
        except OSError:
            return


# ---------------------------------------------------------------------------------------------------------------------- helpers
def _tree_hash(conn, project_id: str) -> str:
    import hashlib
    h = hashlib.sha256()
    for r in conn.execute("SELECT path, sha256 FROM dv_files WHERE project_id = ? AND deleted_at IS NULL ORDER BY path", (project_id,)).fetchall():
        h.update(f"{r['path']}\0{r['sha256']}\n".encode())
    return h.hexdigest()


def _head_commit(tree: Path) -> Optional[str]:
    if not (tree / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"})
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _group_stats(pgid: int):
    """(resident bytes, process count) of a process group, measured with `ps` so it works on Linux and macOS."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,pgid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    rss, n = 0, 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == str(pgid):
            n += 1
            try:
                rss += int(parts[2]) * 1024
            except ValueError:
                pass
    return rss, n


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _dir_bytes(path: Path) -> int:
    total = 0
    for dp, _dn, fns in os.walk(path):
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total
