"""Compute management: limits (defaults from settings, owner overrides per user or project), usage measured from finished jobs, queue state, and the honest capability list.
GPU is an interface only: nothing is allocated unless a provider is genuinely present, and none is."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

from ...core.errors import AppError
from ..identity.authentication import ensure_fresh_auth
from ..identity.constants import Perm
from .common import DevService, clean, dev_error, invalid, record_event

# key -> (Settings attribute, minimum, maximum an owner may set)
LIMIT_KEYS: Dict[str, tuple] = {
    "job_wall_seconds": ("dev_job_wall_seconds", 5, 3600), "job_cpu_seconds": ("dev_job_cpu_seconds", 5, 3600), "job_memory_mb": ("dev_job_memory_mb", 64, 16384),
    "job_output_kb": ("dev_job_output_kb", 16, 65536), "job_max_procs": ("dev_job_max_procs", 1, 512), "job_storage_mb": ("dev_job_storage_mb", 1, 10240),
    "user_concurrency": ("dev_user_concurrency", 1, 16), "project_concurrency": ("dev_project_concurrency", 1, 16), "queue_depth": ("dev_queue_depth", 1, 500),
    "project_storage_mb": ("dev_project_storage_mb", 1, 10240), "user_cpu_seconds_per_day": ("dev_user_cpu_seconds_per_day", 10, 86400), "max_sessions": ("dev_max_sessions", 1, 16),
}
WARN_FRACTION = 0.8


def effective_limit(conn: sqlite3.Connection, settings, user_id: Optional[str], project_id: Optional[str], key: str) -> int:
    default = int(getattr(settings, LIMIT_KEYS[key][0]))
    for scope, sid in (("project", project_id), ("user", user_id)):
        if sid:
            row = conn.execute("SELECT value FROM dv_quota_overrides WHERE scope = ? AND scope_id = ? AND key = ? AND cleared_at IS NULL", (scope, sid, key)).fetchone()
            if row is not None:
                return int(row["value"])
    return default


class GpuProvider:
    """Provider-independent allocation interface. This release has no provider: status() says so and allocate() refuses. A real provider would implement the same three methods."""

    def status(self) -> Dict[str, Any]:
        return {"available": False, "provider": None, "allocatable": 0, "reason": "No GPU capacity is configured on this installation. PredictAI does not rent or advertise managed GPUs."}

    def allocate(self, *_a: Any, **_k: Any) -> None:
        raise AppError("GPU_UNAVAILABLE", self.status()["reason"], 409)

    def release(self, *_a: Any, **_k: Any) -> None:
        return None


CAPABILITIES = {
    "cpu_limits": "enforced by the execution boundary (see /dev/isolation)", "memory_limits": "enforced (kernel limit where the OS supports it, otherwise monitored and killed)",
    "wall_clock": "enforced", "output_size": "enforced", "storage": "enforced per job and per project", "process_count": "enforced (monitored; the whole process group is killed)",
    "queue": "local database-backed queue with priority and per-user / per-project concurrency", "terminate": "available (cancel; the whole process group is killed)",
    "pause": "unavailable: the local process provider cannot checkpoint a job", "resize": "unavailable: limits are fixed per job when it starts",
    "gpu": "unavailable: no provider configured",
}


class ComputeService(DevService):
    gpu = GpuProvider()

    def limits(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        return {k: effective_limit(self.conn, self.ctx.settings, project["owner_user_id"], project["id"], k) for k in LIMIT_KEYS}

    def usage(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        pid, uid = project["id"], project["owner_user_id"]
        lim = {k: effective_limit(self.conn, self.ctx.settings, uid, pid, k) for k in LIMIT_KEYS}
        by_status = {r["status"]: r["c"] for r in self.conn.execute("SELECT status, COUNT(*) c FROM dv_jobs WHERE project_id = ? GROUP BY status", (pid,)).fetchall()}
        day = self.clock.now().strftime("%Y-%m-%dT00:00:00")
        agg = self.conn.execute("SELECT COALESCE(SUM(cpu_ms),0) cpu, COALESCE(SUM(wall_ms),0) wall, COALESCE(MAX(peak_rss_bytes),0) rss FROM dv_jobs WHERE project_id = ?", (pid,)).fetchone()
        user_cpu = int(self.conn.execute("SELECT COALESCE(SUM(cpu_ms),0) FROM dv_jobs WHERE owner_user_id = ? AND created_at >= ?", (uid, day)).fetchone()[0]) // 1000
        running_u = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE owner_user_id = ? AND status IN ('starting','running','cancelling')", (uid,)).fetchone()[0])
        running_p = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE project_id = ? AND status IN ('starting','running','cancelling')", (pid,)).fetchone()[0])
        queued = int(self.conn.execute("SELECT COUNT(*) FROM dv_jobs WHERE status = 'queued'").fetchone()[0])
        stored = int(self.conn.execute("SELECT COALESCE(SUM(bytes),0) FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (pid,)).fetchone()[0])
        art = int(self.conn.execute("SELECT COALESCE(SUM(bytes),0) FROM dv_artifacts WHERE project_id = ? AND deleted_at IS NULL", (pid,)).fetchone()[0])
        storage_limit = lim["project_storage_mb"] * 1024 * 1024
        warnings = []
        if user_cpu >= WARN_FRACTION * lim["user_cpu_seconds_per_day"]:
            warnings.append(f"You have used {user_cpu} of {lim['user_cpu_seconds_per_day']} CPU seconds today.")
        if stored + art >= WARN_FRACTION * storage_limit:
            warnings.append("Project storage is above 80% of its quota.")
        return {"jobs_by_status": by_status, "totals": {"cpu_seconds": round(agg["cpu"] / 1000, 2), "wall_seconds": round(agg["wall"] / 1000, 2), "peak_memory_bytes": int(agg["rss"])},
                "today": {"user_cpu_seconds": user_cpu, "user_cpu_seconds_limit": lim["user_cpu_seconds_per_day"], "user_cpu_seconds_remaining": max(0, lim["user_cpu_seconds_per_day"] - user_cpu)},
                "concurrency": {"user_running": running_u, "user_limit": lim["user_concurrency"], "project_running": running_p, "project_limit": lim["project_concurrency"]},
                "queue": {"waiting_total": queued, "capacity": lim["queue_depth"]},
                "storage": {"files_bytes": stored, "artifact_bytes": art, "limit_bytes": storage_limit, "remaining_bytes": max(0, storage_limit - stored - art)},
                "limits": lim, "warnings": warnings}

    def capabilities(self, principal) -> Dict[str, Any]:
        self.gate(principal)
        return {"capabilities": CAPABILITIES, "gpu": self.gpu.status(), "execution_mode": self.ctx.settings.dev_execution}

    # ------------------------------------------------------------------ owner overrides (authorised, reasoned, audited)
    def set_override(self, owner, *, scope: Any = None, scope_id: Any = None, key: Any = None, value: Any = None, reason: Any = None) -> Dict[str, Any]:
        if not owner.can(Perm.OWNER_ENTITLEMENTS_MANAGE):
            raise dev_error("FORBIDDEN", "Only the platform owner can override compute limits.")
        ensure_fresh_auth(self.ctx, owner)
        self.limit("p7_owner_change:user", owner)
        if scope not in ("user", "project") or not isinstance(scope_id, str) or not scope_id:
            raise invalid("scope", "Choose user or project and give its id.")
        if key not in LIMIT_KEYS:
            raise invalid("key", "Unknown limit. Choose one of: " + ", ".join(LIMIT_KEYS) + ".")
        _, lo, hi = LIMIT_KEYS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise invalid("value", f"Use a whole number from {lo} to {hi}.")
        why = clean(reason, "reason", minimum=5, maximum=300)
        target = self.conn.execute("SELECT 1 FROM " + ("users" if scope == "user" else "projects") + " WHERE id = ?", (scope_id,)).fetchone()
        if target is None:
            raise invalid("scope_id", "No such " + scope + ".")
        now = self.now()
        self.conn.execute("UPDATE dv_quota_overrides SET cleared_at = ? WHERE scope = ? AND scope_id = ? AND key = ? AND cleared_at IS NULL", (now, scope, scope_id, key))
        self.conn.execute("INSERT INTO dv_quota_overrides (scope, scope_id, key, value, set_by, reason, set_at) VALUES (?,?,?,?,?,?,?)", (scope, scope_id, key, value, owner.user_id, why, now))
        record_event(self.conn, now, scope_id if scope == "project" else "-", owner.user_id, "quota.override_set", scope, scope_id, key=key, value=value, reason=why)
        self.ctx.audit.record("dev.quota.override", actor=owner.user_id, metadata={"scope": scope, "scope_id": scope_id, "key": key, "value": value})
        return {"scope": scope, "scope_id": scope_id, "key": key, "value": value}

    def clear_override(self, owner, *, scope: Any = None, scope_id: Any = None, key: Any = None) -> Dict[str, Any]:
        if not owner.can(Perm.OWNER_ENTITLEMENTS_MANAGE):
            raise dev_error("FORBIDDEN", "Only the platform owner can override compute limits.")
        ensure_fresh_auth(self.ctx, owner)
        if key not in LIMIT_KEYS or scope not in ("user", "project"):
            raise invalid("key", "Unknown limit or scope.")
        now = self.now()
        n = self.conn.execute("UPDATE dv_quota_overrides SET cleared_at = ? WHERE scope = ? AND scope_id = ? AND key = ? AND cleared_at IS NULL", (now, scope, str(scope_id), key)).rowcount
        record_event(self.conn, now, str(scope_id) if scope == "project" else "-", owner.user_id, "quota.override_cleared", scope, str(scope_id), key=key, cleared=n)
        return {"cleared": n}

    def list_overrides(self, owner, scope: Any = None, scope_id: Any = None) -> Dict[str, Any]:
        if not owner.can(Perm.OWNER_ENTITLEMENTS_MANAGE):
            raise dev_error("FORBIDDEN", "Only the platform owner can view compute overrides.")
        rows = self.conn.execute("SELECT * FROM dv_quota_overrides WHERE cleared_at IS NULL ORDER BY set_at DESC LIMIT 200").fetchall()
        return {"items": [dict(r) for r in rows if (scope is None or r["scope"] == scope) and (scope_id is None or r["scope_id"] == scope_id)]}
