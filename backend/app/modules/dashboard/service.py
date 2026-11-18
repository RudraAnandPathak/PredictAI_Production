"""The signed-in person's dashboard, assembled from their own records only. Each section is read independently: if one cannot be read it says so ("unavailable" with a reason and what to do) and the
rest still render. Nothing is estimated or invented: a count is a count of rows, and an empty section says it is empty."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import Service7, jload

DEAD_AFTER = "dead"


class DashboardService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver) -> None:
        super().__init__(ctx, ws)
        self.access = access

    def _q(self, sql: str, *args: Any) -> Any:
        return self.conn.execute(sql, args).fetchone()[0]

    def _section(self, name: str, fn: Callable[[], Dict[str, Any]], recovery: str) -> Dict[str, Any]:
        try:
            return {"state": "ok", "as_of": self.now(), **fn()}
        except Exception:  # noqa: BLE001 - one broken section must not take the page down
            return {"state": "unavailable", "as_of": self.now(), "reason": f"The {name} section could not be read.", "recovery": recovery}

    def summary(self, principal: Principal) -> Dict[str, Any]:
        u = principal.user_id
        sections: Dict[str, Dict[str, Any]] = {}

        def work() -> Dict[str, Any]:
            return {"projects": self._q("SELECT COUNT(*) FROM projects WHERE owner_user_id = ? AND status = 'active'", u),
                    "datasets": self._q("SELECT COUNT(*) FROM assets WHERE owner_user_id = ? AND kind = 'dataset' AND status != 'deleting'", u),
                    "documents": self._q("SELECT COUNT(*) FROM assets WHERE owner_user_id = ? AND kind = 'document' AND status != 'deleting'", u),
                    "models_trained": self._q("SELECT COUNT(*) FROM ml_models WHERE owner_user_id = ?", u),
                    "registered_models": self._q("SELECT COUNT(*) FROM reg_models WHERE owner_user_id = ?", u),
                    "research_results": self._q("SELECT COUNT(*) FROM evidence_bundles WHERE owner_user_id = ?", u),
                    "reports": self._q("SELECT COUNT(*) FROM rep_reports WHERE owner_user_id = ? AND status = 'active'", u),
                    "exam_subjects": self._q("SELECT COUNT(*) FROM exam_subjects WHERE owner_user_id = ?", u),
                    "intelligence_runs": self._q("SELECT COUNT(*) FROM adv_runs WHERE owner_user_id = ?", u)}
        sections["work"] = self._section("work", work, "Reload the page. If it keeps failing, tell the PredictAI owner.")

        def quotas() -> Dict[str, Any]:
            q = self.ws.quota.summary(principal)
            used, cap = q["documents_used"], q["max_documents"]
            return {**q, "documents_remaining": None if cap is None else max(0, cap - used), "ml_jobs_running": self._q("SELECT COUNT(*) FROM ml_jobs WHERE owner_user_id = ? AND status IN ('queued','running')", u),
                    "note": "Unlimited uploads come with Special access." if cap is not None else "Special access: uploads are not capped by document count."}
        sections["quotas"] = self._section("quota", quotas, "Open Settings to see your plan.")

        def reports() -> Dict[str, Any]:
            rows = self.conn.execute("SELECT id, title, report_type, current_version, updated_at FROM rep_reports WHERE owner_user_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 5", (u,)).fetchall()
            return {"recent": [dict(r) for r in rows], "exports": self._q("SELECT COUNT(*) FROM rep_exports e JOIN rep_reports r ON r.id = e.report_id WHERE r.owner_user_id = ?", u)}
        sections["reports"] = self._section("reports", reports, "Open Reports to see your list.")

        def models() -> Dict[str, Any]:
            by = self.conn.execute("SELECT v.status, COUNT(*) n FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE m.owner_user_id = ? GROUP BY v.status", (u,)).fetchall()
            bad = self._q("SELECT COUNT(*) FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE m.owner_user_id = ? AND v.last_verified_ok = 0", u)
            return {"versions_by_state": {r["status"]: r["n"] for r in by}, "failed_integrity": bad,
                    "note": "A lifecycle state is a record inside PredictAI. It does not show that anything is deployed or serving."}
        sections["models"] = self._section("model", models, "Open Models to see your registry.")

        def schedules() -> Dict[str, Any]:
            rows = self.conn.execute("SELECT id, report_id, frequency, status, next_run_at FROM sch_schedules WHERE owner_user_id = ? AND status IN ('active','paused','blocked') ORDER BY next_run_at LIMIT 10", (u,)).fetchall()
            dead = self._q("SELECT COUNT(*) FROM sch_deliveries d JOIN sch_runs r ON r.id = d.run_id JOIN sch_schedules s ON s.id = r.schedule_id WHERE s.owner_user_id = ? AND d.outcome = 'dead'", u)
            return {"items": [dict(r) for r in rows], "dead_lettered_deliveries": dead}
        sections["schedules"] = self._section("schedule", schedules, "Open Schedules to see your list.")

        def collaboration() -> Dict[str, Any]:
            ws_rows = self.conn.execute("SELECT w.id, w.name, m.role FROM ws_members m JOIN ws_workspaces w ON w.id = m.workspace_id WHERE m.user_id = ? AND m.status = 'active' AND w.status = 'active'", (u,)).fetchall()
            shared = self.access.shared_with(principal)
            inv = self.conn.execute("SELECT COUNT(*) FROM ws_invitations WHERE email = ? AND status = 'pending' AND expires_at > ?", ((principal.email or "").lower(), self.now())).fetchone()[0]
            return {"workspaces": [dict(r) for r in ws_rows], "shared_with_me": len(shared), "shared_items": shared[:10], "pending_invitations": inv}
        sections["collaboration"] = self._section("collaboration", collaboration, "Open Workspaces to see your list.")

        def activity() -> Dict[str, Any]:
            ev: List[Dict[str, Any]] = []
            for sql, kind in (("SELECT id, title AS label, updated_at AS at FROM rep_reports WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT 5", "report"),
                              ("SELECT id, title AS label, created_at AS at FROM adv_runs WHERE owner_user_id = ? ORDER BY created_at DESC LIMIT 5", "analysis"),
                              ("SELECT id, name AS label, updated_at AS at FROM reg_models WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT 5", "model")):
                ev += [{"kind": kind, **dict(r)} for r in self.conn.execute(sql, (u,)).fetchall()]
            return {"items": sorted(ev, key=lambda e: e["at"], reverse=True)[:10]}
        sections["activity"] = self._section("activity", activity, "Reload the page.")

        def attention() -> Dict[str, Any]:
            items: List[Dict[str, Any]] = []
            add = lambda sev, text, where: items.append({"severity": sev, "text": text, "where": where})  # noqa: E731
            for r in self.conn.execute("SELECT s.id FROM sch_schedules s WHERE s.owner_user_id = ? AND s.status = 'blocked'", (u,)).fetchall():
                add("warning", "A schedule is blocked because access to its report ended.", f"/schedules/{r['id']}")
            n = sections["schedules"].get("dead_lettered_deliveries", 0) if sections["schedules"]["state"] == "ok" else 0
            if n:
                add("warning", f"{n} scheduled delivery(ies) failed after several attempts. You can retry them.", "/schedules")
            bad = sections["models"].get("failed_integrity", 0) if sections["models"]["state"] == "ok" else 0
            if bad:
                add("critical", f"{bad} registered model version(s) failed their integrity check and cannot be used.", "/models")
            for r in self.conn.execute("SELECT id FROM exam_assessments WHERE owner_user_id = ? AND status = 'draft'", (u,)).fetchall():
                add("info", "A mock exam is waiting for your review and approval.", f"/exam/assessments/{r['id']}")
            failed = self._q("SELECT COUNT(*) FROM ml_jobs WHERE owner_user_id = ? AND status = 'failed'", u)
            if failed:
                add("info", f"{failed} training job(s) failed. Open the dataset to see why.", "/predictions")
            if sections["quotas"]["state"] == "ok" and sections["quotas"].get("documents_remaining") == 0:
                add("warning", "You have used all your document slots. Delete a document to upload another.", "/documents")
            return {"items": items}
        sections["attention"] = self._section("attention", attention, "Reload the page.")
        degraded = [k for k, v in sections.items() if v["state"] != "ok"]
        return {"generated_at": self.now(), "user": {"name": principal.display_name or principal.email, "tier": "special" if self.ws.quota.unlimited(principal) else "standard"}, "sections": sections,
                "degraded": degraded, "complete": not degraded}
