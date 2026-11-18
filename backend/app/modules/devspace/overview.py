"""Landing page data, consolidated project history and the revocation helpers. Everything here is read from real records; an empty workspace shows zeros, never invented activity."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..identity.constants import Perm
from . import isolation, sessions
from .common import DevService, dev_error, has_dev_access, invalid
from .compute import CAPABILITIES, ComputeService
from .engine import ACTIVE, TERMINAL


def access_state(principal) -> Dict[str, Any]:
    ok = has_dev_access(principal)
    return {"allowed": ok, "reason": None if ok else "The Development Workspace is available to people with Special access. Ask the platform owner for access."}


class OverviewService(DevService):
    def landing(self, principal) -> Dict[str, Any]:
        """Never raises for a person without access: the page needs to explain why it is locked. Detail is only returned when allowed."""
        state = access_state(principal)
        if not state["allowed"]:
            return {"access": state}
        s, uid = self.ctx.settings, principal.user_id
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        projects = self.conn.execute("SELECT p.id, p.name FROM projects p WHERE p.owner_user_id = ? AND p.status = 'active' ORDER BY p.updated_at DESC LIMIT 50", (uid,)).fetchall()
        jobs = {r["status"]: r["c"] for r in self.conn.execute("SELECT status, COUNT(*) c FROM dv_jobs WHERE owner_user_id = ? GROUP BY status", (uid,)).fetchall()}
        recent = self.conn.execute("SELECT j.id, j.project_id, j.kind, j.status, j.entry, j.created_at, j.error_class FROM dv_jobs j WHERE j.owner_user_id = ? ORDER BY j.created_at DESC LIMIT 8", (uid,)).fetchall()
        events = self.conn.execute("SELECT e.id, e.project_id, e.action, e.outcome, e.created_at FROM dv_events e JOIN projects p ON p.id = e.project_id WHERE p.owner_user_id = ? ORDER BY e.id DESC LIMIT 10", (uid,)).fetchall()
        prov = isolation.describe(s)
        return {"access": state, "projects": [{"id": p["id"], "name": p["name"], "has_workspace": bool(q("SELECT COUNT(*) FROM dv_workspaces WHERE project_id = ?", p["id"]))} for p in projects],
                "counts": {"workspaces": q("SELECT COUNT(*) FROM dv_workspaces WHERE owner_user_id = ? AND status = 'active'", uid), "suspended_workspaces": q("SELECT COUNT(*) FROM dv_workspaces WHERE owner_user_id = ? AND status = 'suspended'", uid),
                           "files": q("SELECT COUNT(*) FROM dv_files WHERE owner_user_id = ? AND deleted_at IS NULL", uid), "environments": q("SELECT COUNT(*) FROM dv_envs WHERE owner_user_id = ? AND deleted_at IS NULL", uid),
                           "experiments": q("SELECT COUNT(*) FROM dv_experiments WHERE owner_user_id = ?", uid), "artifacts": q("SELECT COUNT(*) FROM dv_artifacts WHERE owner_user_id = ? AND deleted_at IS NULL", uid),
                           "pipelines": q("SELECT COUNT(*) FROM dv_pipelines WHERE owner_user_id = ? AND deleted_at IS NULL", uid), "sessions_running": q("SELECT COUNT(*) FROM dv_sessions WHERE owner_user_id = ? AND status IN ('starting','running')", uid)},
                "jobs": {"by_status": jobs, "active": sum(jobs.get(k, 0) for k in ACTIVE) , "queued": jobs.get("queued", 0), "recent": [dict(r) for r in recent]},
                "history": [dict(r) for r in events],
                "execution": {"mode": s.dev_execution, "enabled": s.dev_execution != "off", "isolation": prov, "pipelines_enabled": s.dev_pipelines_enabled, "deploy_enabled": False,
                              "sessions": {k: sessions.availability(s, k) for k in sessions.KINDS}},
                "capabilities": CAPABILITIES}

    def history(self, principal, project_id: Any, *, action: Any = None, before: Any = None, limit: Any = 50) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        sql, args = "SELECT * FROM dv_events WHERE project_id = ?", [project["id"]]
        if isinstance(action, str) and action:
            sql, args = sql + " AND action LIKE ?", args + [action.replace("%", "").replace("_", "\\_") + "%"]
            sql = sql.replace("action LIKE ?", "action LIKE ? ESCAPE '\\'")
        if before is not None:
            if isinstance(before, bool) or not isinstance(before, int):
                raise invalid("before", "Use an event id.")
            sql, args = sql + " AND id < ?", args + [before]
        n = max(1, min(int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else 50, 200))
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [n]).fetchall()
        return {"items": [{"id": r["id"], "action": r["action"], "target_type": r["target_type"], "target_id": r["target_id"], "outcome": r["outcome"], "actor": "you" if r["actor_user_id"] == principal.user_id else ("system" if r["actor_user_id"] is None else "another user"),
                           "detail": json.loads(r["detail"]), "created_at": r["created_at"]} for r in rows], "next_before": rows[-1]["id"] if len(rows) == n else None}

    def timeline(self, principal, project_id: Any) -> Dict[str, Any]:
        """History grouped for the project page: the last of each kind of milestone."""
        project = self.open_project(principal, project_id)
        out = {}
        for key, like in (("commit", "git.committed"), ("environment", "env.build_"), ("experiment", "experiment."), ("pipeline", "pipeline.run_"), ("job", "job.")):
            r = self.conn.execute("SELECT action, outcome, created_at FROM dv_events WHERE project_id = ? AND action LIKE ? ORDER BY id DESC LIMIT 1", (project["id"], like + "%")).fetchone()
            out[key] = dict(r) if r else None
        return {"latest": out}
