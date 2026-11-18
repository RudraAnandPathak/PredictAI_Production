"""Owner Command Center: platform-level counts, honest monitoring, maintenance mode and quota policy.

Privacy boundary: every query here returns counts, sizes, states and timestamps. Nothing selects a title, a name given to a user's
work, document text, report content, prompt or answer. Owners see account identity through the existing /owner/users routes only."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..identity.authentication import ensure_fresh_auth
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..p7 import E, Service7, choice, clean, invalid, maintenance_error, page

STALE_JOB_MINUTES = 15          # a running job whose heartbeat is older than this is reported as stale
OVERDUE_SCHEDULE_MINUTES = 30   # an active schedule this late means nobody is running run_schedules.py
QUOTA_KEYS = {"standard_max_documents": (1, 500), "standard_max_pages": (1, 2000), "standard_max_research_workspaces": (1, 100), "standard_max_research_sources": (1, 500)}


class OwnerOpsService(Service7):
    def __init__(self, ctx: Any, ws: Any) -> None:
        super().__init__(ctx, ws)

    # ------------------------------------------------------------------ helpers
    def _owner(self, principal: Principal, perm: str = Perm.OWNER_USERS_READ) -> None:
        require(principal, perm)   # owner permissions are held only by owners; anyone else gets the usual typed 403

    def _n(self, sql: str, *args: Any) -> int:
        return int(self.conn.execute(sql, args).fetchone()[0])

    def _event(self, principal: Principal, action: str, target: Optional[str], outcome: str, detail: Optional[str] = None) -> None:
        self.conn.execute("INSERT INTO own_events (actor_user_id, action, target, outcome, detail, created_at) VALUES (?,?,?,?,?,?)",
                          (principal.user_id, action, target, outcome, detail, self.now()))
        self.audit(E.OWNER_ACTION, principal, target, outcome if outcome in ("success", "failure", "denied") else "success", action=action)

    # ------------------------------------------------------------------ maintenance
    def maintenance(self) -> Dict[str, Any]:
        r = self.conn.execute("SELECT active, notice, changed_by, changed_at FROM own_maintenance WHERE id = 1").fetchone()
        return {"active": bool(r and r["active"]), "notice": r["notice"] if r else None, "changed_at": r["changed_at"] if r else None}

    def guard_write(self, principal: Optional[Principal]) -> None:
        """Called for every mutating request: while maintenance is on, only owners may change anything."""
        m = self.maintenance()
        if m["active"] and not (principal is not None and principal.is_owner):
            raise maintenance_error(m["notice"])

    def set_maintenance(self, principal: Principal, active: Any, notice: Any = None, confirm: Any = False) -> Dict[str, Any]:
        self._owner(principal, Perm.OWNER_ENTITLEMENTS_MANAGE)
        ensure_fresh_auth(self.ctx, principal)
        self.limit("p7_owner_change:user", principal)
        if not isinstance(active, bool):
            raise invalid("active", "Choose on or off.")
        if confirm is not True:
            raise invalid("confirm", "Confirm this change. Turning maintenance on pauses changes for every other account.")
        text = clean(notice, "notice", maximum=300, required=False)
        self.conn.execute("UPDATE own_maintenance SET active = ?, notice = ?, changed_by = ?, changed_at = ? WHERE id = 1",
                          (1 if active else 0, text if active else None, principal.user_id, self.now()))
        self._event(principal, "maintenance_on" if active else "maintenance_off", None, "success", text)
        return {**self.maintenance(), "owner_access": "preserved"}

    # ------------------------------------------------------------------ quota policy
    def quota_policy(self, principal: Principal) -> Dict[str, Any]:
        self._owner(principal)
        s = self.ctx.settings
        defaults = {"standard_max_documents": s.standard_max_documents, "standard_max_pages": s.standard_max_pages,
                    "standard_max_research_workspaces": s.standard_max_research_workspaces, "standard_max_research_sources": s.standard_max_research_sources}
        rows = {r["key"]: r for r in self.conn.execute("SELECT key, value, changed_at FROM own_quota_policy").fetchall()}
        return {"items": [{"key": k, "default": defaults[k], "override": rows[k]["value"] if k in rows else None, "effective": rows[k]["value"] if k in rows else defaults[k],
                           "min": QUOTA_KEYS[k][0], "max": QUOTA_KEYS[k][1], "changed_at": rows[k]["changed_at"] if k in rows else None} for k in QUOTA_KEYS],
                "note": "Applies to standard accounts from the next upload or new workspace. Existing documents and workspaces are never deleted when a limit is lowered."}

    def set_quota(self, principal: Principal, key: Any, value: Any) -> Dict[str, Any]:
        self._owner(principal, Perm.OWNER_ENTITLEMENTS_MANAGE)
        ensure_fresh_auth(self.ctx, principal)
        self.limit("p7_owner_change:user", principal)
        key = choice(key, "key", QUOTA_KEYS)
        lo, hi = QUOTA_KEYS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise invalid("value", f"Use a whole number from {lo} to {hi}.")
        self.conn.execute("INSERT INTO own_quota_policy (key, value, changed_by, changed_at) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, changed_by=excluded.changed_by, changed_at=excluded.changed_at",
                          (key, value, principal.user_id, self.now()))
        self._event(principal, "quota_set", key, "success", str(value))
        return self.quota_policy(principal)

    def reset_quota(self, principal: Principal, key: Any) -> Dict[str, Any]:
        self._owner(principal, Perm.OWNER_ENTITLEMENTS_MANAGE)
        ensure_fresh_auth(self.ctx, principal)
        self.limit("p7_owner_change:user", principal)
        key = choice(key, "key", QUOTA_KEYS)
        self.conn.execute("DELETE FROM own_quota_policy WHERE key = ?", (key,))
        self._event(principal, "quota_reset", key, "success")
        return self.quota_policy(principal)

    # ------------------------------------------------------------------ overview (counts only)
    def overview(self, principal: Principal) -> Dict[str, Any]:
        self._owner(principal)
        n = self._n
        by_status = {r["status"]: r["c"] for r in self.conn.execute("SELECT status, COUNT(*) c FROM users GROUP BY status").fetchall()}
        out = {"generated_at": self.now(), "maintenance": self.maintenance(),
               "accounts": {"total": sum(by_status.values()), "by_status": by_status,
                            "special_access_active": n("SELECT COUNT(DISTINCT user_id) FROM entitlements WHERE revoked_at IS NULL")},
               "content": {"projects": n("SELECT COUNT(*) FROM projects WHERE status = 'active'"),
                           "datasets": n("SELECT COUNT(*) FROM assets WHERE kind='dataset' AND status != 'deleting'"),
                           "documents": n("SELECT COUNT(*) FROM assets WHERE kind='document' AND status != 'deleting'"),
                           "stored_bytes": n("SELECT COALESCE(SUM(size_bytes),0) FROM assets WHERE status != 'deleting'"),
                           "reports": n("SELECT COUNT(*) FROM rep_reports WHERE status='active'"),
                           "report_exports": n("SELECT COUNT(*) FROM rep_exports"),
                           "registered_models": n("SELECT COUNT(*) FROM reg_models"),
                           "workspaces": n("SELECT COUNT(*) FROM ws_workspaces WHERE status='active'"),
                           "schedules_active": n("SELECT COUNT(*) FROM sch_schedules WHERE status='active'")},
               "work": {"training_jobs_running": n("SELECT COUNT(*) FROM ml_jobs WHERE status IN ('queued','running')"),
                        "research_jobs_running": n("SELECT COUNT(*) FROM research_jobs WHERE status IN ('queued','running')"),
                        "jobs_failed_last_7_days": n("SELECT COUNT(*) FROM ml_jobs WHERE status='failed' AND created_at > ?", (self.clock.now() - timedelta(days=7)).isoformat())},
               "boundary": "Counts, sizes and states only. Titles, document text, report content, questions and answers are not shown here."}
        return out

    def user_summary(self, principal: Principal, user_id: Any) -> Dict[str, Any]:
        """Metadata for one account: how much, never what."""
        self._owner(principal)
        if not isinstance(user_id, str) or not self.conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            from ..workspace.errors import not_found
            raise not_found("account")
        n = lambda sql: self._n(sql, user_id)  # noqa: E731
        self.audit(E.OWNER_ACTION, principal, user_id, action="view_user_summary")
        return {"user_id": user_id, "projects": n("SELECT COUNT(*) FROM projects WHERE owner_user_id = ? AND status='active'"),
                "datasets": n("SELECT COUNT(*) FROM assets WHERE owner_user_id = ? AND kind='dataset' AND status != 'deleting'"),
                "documents": n("SELECT COUNT(*) FROM assets WHERE owner_user_id = ? AND kind='document' AND status != 'deleting'"),
                "stored_bytes": n("SELECT COALESCE(SUM(size_bytes),0) FROM assets WHERE owner_user_id = ? AND status != 'deleting'"),
                "reports": n("SELECT COUNT(*) FROM rep_reports WHERE owner_user_id = ? AND status='active'"),
                "models": n("SELECT COUNT(*) FROM ml_models WHERE owner_user_id = ?"),
                "schedules": n("SELECT COUNT(*) FROM sch_schedules WHERE owner_user_id = ?"),
                "workspaces_member_of": n("SELECT COUNT(*) FROM ws_members WHERE user_id = ? AND status='active'"),
                "boundary": "Counts only. The owner role does not open a person's projects, documents or reports."}

    # ------------------------------------------------------------------ monitoring
    def monitoring(self, principal: Principal) -> Dict[str, Any]:
        self._owner(principal)
        now = self.clock.now()
        checks: List[Dict[str, Any]] = []

        def add(name: str, state: str, detail: str, **extra: Any) -> None:
            checks.append({"name": name, "state": state, "detail": detail, **extra})

        try:
            self.conn.execute("SELECT 1").fetchone()
            add("database", "ok", "The database answered.")
        except Exception:  # noqa: BLE001
            add("database", "unavailable", "The database did not answer.")
        try:
            root = getattr(self.ctx.settings, "storage_dir", None) or "."
            du = shutil.disk_usage(root if os.path.exists(str(root)) else ".")
            free = round(du.free / du.total * 100, 1)
            add("disk", "ok" if free > 15 else "degraded", f"{free}% free where files are stored.", free_percent=free)
        except Exception:  # noqa: BLE001
            add("disk", "unavailable", "Disk usage could not be read on this system.")
        try:
            add("load", "ok", "1-minute load average: %.2f." % os.getloadavg()[0], load_1m=round(os.getloadavg()[0], 2))
        except (OSError, AttributeError):
            add("load", "unavailable", "Load average is not available on this system.")
        add("cpu_memory", "unavailable", "CPU and memory use are not collected. PredictAI does not install a monitoring agent; use your operating system's tools.")

        cutoff = (now - timedelta(minutes=STALE_JOB_MINUTES)).isoformat()
        for label, table in (("training_jobs", "ml_jobs"), ("research_jobs", "research_jobs")):
            try:
                stale = self._n(f"SELECT COUNT(*) FROM {table} WHERE status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)", cutoff)
                running = self._n(f"SELECT COUNT(*) FROM {table} WHERE status = 'running'")
                add(label, "stale" if stale else "ok", f"{stale} of {running} running job(s) have no recent heartbeat." if running else "No jobs running.", running=running, stale=stale)
            except Exception:  # noqa: BLE001
                add(label, "unavailable", "Job state could not be read.")
        try:
            late = (now - timedelta(minutes=OVERDUE_SCHEDULE_MINUTES)).isoformat()
            overdue = self._n("SELECT COUNT(*) FROM sch_schedules WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at < ?", late)
            dead = self._n("SELECT COUNT(*) FROM sch_deliveries WHERE outcome='dead'")
            add("scheduler", "stale" if overdue else "ok", f"{overdue} schedule(s) are overdue. Check that scripts/run_schedules.py is running." if overdue else "No schedule is overdue.", overdue=overdue)
            add("deliveries", "degraded" if dead else "ok", f"{dead} delivery(ies) failed permanently." if dead else "No permanently failed deliveries.", dead_lettered=dead)
        except Exception:  # noqa: BLE001
            add("scheduler", "unavailable", "Schedule state could not be read.")
        order = {"ok": 0, "degraded": 1, "stale": 2, "unavailable": 3}
        worst = max((c["state"] for c in checks), key=lambda s: order[s])
        return {"generated_at": now.isoformat(), "overall": worst, "checks": checks,
                "note": "'unavailable' means PredictAI cannot measure it here, not that it is fine. 'stale' means a recent signal is missing."}

    def events(self, principal: Principal, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        self._owner(principal, Perm.OWNER_AUDIT_READ)
        lim, off = page(limit, offset)
        rows = self.conn.execute("SELECT id, actor_user_id, action, target, outcome, created_at FROM own_events ORDER BY id DESC LIMIT ? OFFSET ?", (lim + 1, off)).fetchall()
        return {"items": [dict(r) for r in rows[:lim]], "next_offset": off + lim if len(rows) > lim else None}
