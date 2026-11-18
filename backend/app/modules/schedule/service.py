"""Scheduled report delivery. Nothing runs by itself inside the web process: `tick()` is called by `scripts/run_schedules.py` (cron, launchd, a systemd timer) or by the owner. A schedule is
a promise to export an existing report; it never re-runs research or calculations.

Safety: the schedule owner's permission to the report is re-checked at every run and every delivery attempt, so revoking a share, removing a member, closing a workspace or blocking an account
stops later deliveries. Every run and every delivery has an idempotency key; retries are bounded; a delivery that keeps failing ends in the dead-letter state until a person retries it."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...core.errors import AppError
from ..collab.access import AccessResolver
from ..identity.authorization import Principal, build_principal
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page
from . import timing as T
from .adapters import DeliveryError, build_adapters

MAX_ATTEMPTS = 3
BACKOFF_MINUTES = (1, 5, 30)
MAX_RECIPIENTS = 10
MAX_SCHEDULES = 20
FREQUENCIES = ("once", "daily", "weekly", "monthly")
FORMATS = ("pdf", "docx", "pptx", "md", "csv", "xlsx", "png")


class ScheduleService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver, reports: Any, adapters: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(ctx, ws)
        self.access, self.reports = access, reports
        self.adapters = adapters if adapters is not None else build_adapters(ctx.settings, ctx.mailer.sender)

    # ------------------------------------------------------------------ helpers
    def _now(self) -> datetime:
        return T.parse_utc(self.now())

    def _principal_for(self, user_id: str) -> Optional[Principal]:
        row = self.conn.execute("SELECT * FROM users WHERE id = ? AND status = 'active'", (user_id,)).fetchone()
        return build_principal(self.ctx.repo, row) if row else None

    def _view(self, s) -> Dict[str, Any]:
        last = self.conn.execute("SELECT outcome, reason, scheduled_for FROM sch_runs WHERE schedule_id = ? ORDER BY scheduled_for DESC, rowid DESC LIMIT 1", (s["id"],)).fetchone()
        return {"id": s["id"], "report_id": s["report_id"], "mode": s["mode"], "pinned_version": s["pinned_version"], "frequency": s["frequency"], "timezone": s["timezone"], "local_time": s["local_time"],
                "weekday": s["weekday"], "day_of_month": s["day_of_month"], "export_format": s["export_format"], "recipients": jload(s["recipients"], []), "status": s["status"],
                "next_run_at": s["next_run_at"], "created_at": s["created_at"], "last_run": dict(last) if last else None,
                "mode_note": "Snapshot always sends the pinned version." if s["mode"] == "snapshot" else "Refresh sends the report's latest saved version each time. It does not re-run research or calculations."}

    def _own(self, principal: Principal, schedule_id: Any):
        row = self.conn.execute("SELECT * FROM sch_schedules WHERE id = ? AND owner_user_id = ?", (schedule_id, principal.user_id)).fetchone() if isinstance(schedule_id, str) and len(schedule_id) <= 64 else None
        if row is None:
            raise not_found("schedule")
        return row

    # ------------------------------------------------------------------ create / manage
    def adapters_status(self, principal: Principal) -> Dict[str, Any]:
        return {"items": [{"kind": k, "configured": bool(a.configured), "needs": a.needs} for k, a in self.adapters.items()],
                "scheduler": "Schedules run when the scheduler script is run (for example every few minutes from cron). Nothing runs inside the web server by itself."}

    def create(self, principal: Principal, report_id: str, spec: Any) -> Dict[str, Any]:
        self.limit("p7_schedule:user", principal)
        spec = spec if isinstance(spec, dict) else {}
        self.access.resolve(principal, "report", report_id, "view")
        if self.conn.execute("SELECT COUNT(*) FROM sch_schedules WHERE owner_user_id = ? AND status IN ('active','paused','blocked')", (principal.user_id,)).fetchone()[0] >= MAX_SCHEDULES:
            raise AppError("SCHEDULE_LIMIT", f"You can keep up to {MAX_SCHEDULES} schedules.", 409)
        mode = choice(spec.get("mode", "refresh"), "mode", ("snapshot", "refresh"))
        freq = choice(spec.get("frequency"), "frequency", FREQUENCIES)
        fmt = choice(spec.get("export_format", "pdf"), "export_format", FORMATS)
        try:
            T.zone(spec.get("timezone"))
            if freq == "once":
                run_at = spec.get("run_at")
                if not isinstance(run_at, str):
                    raise ValueError("run_at")
                at = T.parse_utc(run_at)
                lt = at.astimezone(T.zone(spec["timezone"])).strftime("%H:%M")
            else:
                run_at, lt = None, spec.get("local_time")
                T.hhmm(lt)
        except ValueError as exc:
            field = str(exc) if str(exc) in ("timezone", "local_time", "run_at") else "run_at"
            raise invalid(field, {"timezone": "Use a timezone name such as Asia/Kolkata.", "local_time": "Use 24-hour HH:MM.", "run_at": "Give the date and time as 2026-12-31T09:00:00+05:30."}[field]) from None
        weekday, dom = spec.get("weekday"), spec.get("day_of_month")
        if freq == "weekly" and (isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6):
            raise invalid("weekday", "Use 0 (Monday) to 6 (Sunday).")
        if freq == "monthly" and (isinstance(dom, bool) or not isinstance(dom, int) or not 1 <= dom <= 31):
            raise invalid("day_of_month", "Use a day from 1 to 31 (short months use their last day).")
        recipients = self._recipients(principal, spec.get("recipients") or [{"kind": "local", "target": "me"}])
        r = self.reports.get(principal, report_id)
        pinned = None
        if mode == "snapshot":
            pinned = spec.get("pinned_version", r["current_version"])
            if isinstance(pinned, bool) or not isinstance(pinned, int) or self.reports.repo.version(report_id, pinned) is None:
                raise invalid("pinned_version", "Choose an existing version of the report.")
        sch = {"frequency": freq, "timezone": spec["timezone"], "local_time": lt, "weekday": weekday, "day_of_month": dom, "run_at": run_at}
        nxt = T.next_after(sch, self._now())
        if nxt is None:
            raise invalid("run_at", "Choose a time in the future.")
        sid, now = new_id(), self.now()
        with self.conn:
            self.conn.execute("INSERT INTO sch_schedules (id, owner_user_id, project_id, report_id, pinned_version, mode, frequency, timezone, local_time, weekday, day_of_month, export_format, recipients, status, next_run_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (sid, principal.user_id, r["project_id"], report_id, pinned, mode, freq, spec["timezone"], lt, weekday, dom, fmt, jdump(recipients), "active", nxt.isoformat(), now, now))
        self.audit(E.SCHEDULE_CHANGED, principal, sid, action="created", frequency=freq, mode=mode)
        row = self.conn.execute("SELECT * FROM sch_schedules WHERE id = ?", (sid,)).fetchone()
        return self._view(row)

    def _recipients(self, principal: Principal, raw: Any) -> List[Dict[str, str]]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_RECIPIENTS:
            raise invalid("recipients", f"Give one to {MAX_RECIPIENTS} recipients.")
        out, seen = [], set()
        for r in raw:
            if not isinstance(r, dict):
                raise invalid("recipients", "Each recipient is {kind, target}.")
            kind = choice(r.get("kind"), "recipients.kind", ("local", "email", "slack"))
            ad = self.adapters[kind]
            if not ad.configured:
                raise AppError("ADAPTER_NOT_CONFIGURED", f"{kind.title()} delivery is not set up on this server, so it cannot be used. It needs: {ad.needs}", 409)
            target = "me" if kind == "local" else clean(r.get("target"), "recipients.target", maximum=254).lower() if kind == "email" else "channel"
            if kind == "email" and "@" not in target:
                raise invalid("recipients.target", "Enter an email address.")
            if (kind, target) in seen:
                continue
            seen.add((kind, target))
            out.append({"kind": kind, "target": target})
        return out

    def list(self, principal: Principal) -> Dict[str, Any]:
        rows = self.conn.execute("SELECT * FROM sch_schedules WHERE owner_user_id = ? ORDER BY created_at DESC", (principal.user_id,)).fetchall()
        return {"items": [self._view(r) for r in rows]}

    def get(self, principal: Principal, schedule_id: str) -> Dict[str, Any]:
        return self._view(self._own(principal, schedule_id))

    def set_status(self, principal: Principal, schedule_id: str, action: Any) -> Dict[str, Any]:
        s = self._own(principal, schedule_id)
        action = choice(action, "action", ("pause", "resume", "end"))
        if action == "pause" and s["status"] == "active":
            new, nxt = "paused", s["next_run_at"]
        elif action == "end":
            new, nxt = "ended", None
        elif action == "resume" and s["status"] in ("paused", "blocked"):
            try:
                self.access.resolve(principal, "report", s["report_id"], "view")
            except AppError:
                raise AppError("ACCESS_REVOKED", "You no longer have access to this report, so its schedule cannot resume.", 409) from None
            sch = {"frequency": s["frequency"], "timezone": s["timezone"], "local_time": s["local_time"], "weekday": s["weekday"], "day_of_month": s["day_of_month"], "run_at": s["next_run_at"] if s["frequency"] == "once" else None}
            n = T.next_after(sch, self._now())
            if n is None:
                raise conflict("SCHEDULE_ENDED", "This one-off time has passed. Create a new schedule.")
            new, nxt = "active", n.isoformat()
        else:
            raise conflict("INVALID_ACTION", f"A {s['status']} schedule cannot be {action}d.")
        with self.conn:
            self.conn.execute("UPDATE sch_schedules SET status = ?, next_run_at = ?, updated_at = ? WHERE id = ?", (new, nxt, self.now(), schedule_id))
        self.audit(E.SCHEDULE_CHANGED, principal, schedule_id, action=action)
        return self.get(principal, schedule_id)

    # ------------------------------------------------------------------ running
    def tick(self, limit: int = 25) -> Dict[str, Any]:
        """Run what is due and retry what failed. Safe to call from several processes: runs and deliveries have unique idempotency keys."""
        m = self.conn.execute("SELECT active FROM own_maintenance WHERE id = 1").fetchone()
        if m is not None and m["active"]:
            return {"ran": 0, "retried": 0, "skipped": "maintenance"}
        now = self._now()
        ran, retried = 0, 0
        due = self.conn.execute("SELECT * FROM sch_schedules WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY next_run_at LIMIT ?", (now.isoformat(), limit)).fetchall()
        for s in due:
            scheduled_for = s["next_run_at"]
            self._advance(s, now)                                                  # the next time is saved first: a crash during the run can never make it run twice
            self._run(s, scheduled_for, key=f"{s['id']}:{scheduled_for}", initiated_by=None)
            ran += 1
        for d in self.conn.execute("SELECT id FROM sch_deliveries WHERE outcome = 'failed' AND next_attempt_at IS NOT NULL AND next_attempt_at <= ? ORDER BY next_attempt_at LIMIT ?", (now.isoformat(), limit)).fetchall():
            self._attempt(d["id"])
            retried += 1
        return {"ran": ran, "retried": retried, "at": now.isoformat()}

    def _advance(self, s, now: datetime) -> None:
        sch = {"frequency": s["frequency"], "timezone": s["timezone"], "local_time": s["local_time"], "weekday": s["weekday"], "day_of_month": s["day_of_month"]}
        nxt = None if s["frequency"] == "once" else T.next_after(sch, now)
        with self.conn:
            self.conn.execute("UPDATE sch_schedules SET next_run_at = ?, status = ?, updated_at = ? WHERE id = ?", (nxt.isoformat() if nxt else None, "active" if nxt else "ended", self.now(), s["id"]))

    def run_now(self, principal: Principal, schedule_id: str, *, idempotency_key: Any = None) -> Dict[str, Any]:
        s = self._own(principal, schedule_id)
        key = clean(idempotency_key, "idempotency_key", maximum=80, required=False) or uuid.uuid4().hex
        run = self._run(s, self.now(), key=f"{schedule_id}:manual:{key}", initiated_by=principal.user_id)
        return run

    def _run(self, s, scheduled_for: str, *, key: str, initiated_by: Optional[str]) -> Dict[str, Any]:
        existing = self.conn.execute("SELECT id FROM sch_runs WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            return self.get_run_internal(existing["id"])
        rid, now = new_id(), self.now()
        with self.conn:
            self.conn.execute("INSERT INTO sch_runs (id, schedule_id, scheduled_for, started_at, outcome, initiated_by, idempotency_key) VALUES (?,?,?,?,?,?,?)", (rid, s["id"], scheduled_for, now, "pending", initiated_by, key))
        owner = self._principal_for(s["owner_user_id"])
        reason = None
        if owner is None:
            reason = "The schedule owner's account is not active."
        else:
            try:
                self.access.resolve(owner, "report", s["report_id"], "view")                 # permission re-checked at run time, never trusted from when the schedule was made
            except AppError:
                reason = "The schedule owner no longer has access to this report."
        if reason:
            return self._finish(rid, s, "blocked", reason, block=True)
        try:
            version = s["pinned_version"] if s["mode"] == "snapshot" else None
            ex = self.reports.export(owner, s["report_id"], s["export_format"], version=version, origin="scheduled")
        except AppError as exc:
            return self._finish(rid, s, "failed", f"{exc.code}: {exc.message}")
        recipients = jload(s["recipients"], [])
        with self.conn:
            self.conn.execute("UPDATE sch_runs SET report_version = ?, export_id = ? WHERE id = ?", (ex["version"], ex["export_id"], rid))
            for r in recipients:
                self.conn.execute("INSERT OR IGNORE INTO sch_deliveries (id, run_id, provider, recipient, outcome, attempts, idempotency_key, next_attempt_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (new_id(), rid, r["kind"], r["target"], "pending", 0, f"{rid}:{r['kind']}:{r['target']}", now, now))
        for d in self.conn.execute("SELECT id FROM sch_deliveries WHERE run_id = ?", (rid,)).fetchall():
            self._attempt(d["id"])
        outcomes = [d["outcome"] for d in self.conn.execute("SELECT outcome FROM sch_deliveries WHERE run_id = ?", (rid,))]
        outcome = "ok" if all(o == "sent" for o in outcomes) else ("partial" if any(o == "sent" for o in outcomes) else "failed")
        return self._finish(rid, s, outcome, None if outcome == "ok" else "Some deliveries have not been sent yet; see the delivery history.")

    def _finish(self, rid: str, s, outcome: str, reason: Optional[str], block: bool = False) -> Dict[str, Any]:
        with self.conn:
            self.conn.execute("UPDATE sch_runs SET outcome = ?, reason = ?, finished_at = ? WHERE id = ?", (outcome, reason, self.now(), rid))
            if block:
                self.conn.execute("UPDATE sch_schedules SET status = 'blocked', updated_at = ? WHERE id = ?", (self.now(), s["id"]))
                self.conn.execute("UPDATE sch_deliveries SET outcome = 'dead', last_error = ?, next_attempt_at = NULL WHERE run_id = ? AND outcome IN ('pending','failed')", (reason, rid))
        self.audit(E.DELIVERY, None, rid, schedule=s["id"], run_outcome=outcome)
        return self.get_run_internal(rid)

    def _attempt(self, delivery_id: str, *, manual: bool = False) -> None:
        d = self.conn.execute("SELECT * FROM sch_deliveries WHERE id = ?", (delivery_id,)).fetchone()
        if d is None or d["outcome"] == "sent":
            return
        run = self.conn.execute("SELECT * FROM sch_runs WHERE id = ?", (d["run_id"],)).fetchone()
        s = self.conn.execute("SELECT * FROM sch_schedules WHERE id = ?", (run["schedule_id"],)).fetchone()
        now = self.now()
        owner = self._principal_for(s["owner_user_id"])
        err, retryable, detail = None, True, None
        try:
            if owner is None:
                raise DeliveryError("The schedule owner's account is not active.", retryable=False)
            self.access.resolve(owner, "report", s["report_id"], "view")
            if d["provider"] == "email":                                          # the person who receives the notice must themselves be allowed to open the report
                u = self.conn.execute("SELECT * FROM users WHERE lower(email) = ? AND status = 'active'", (d["recipient"],)).fetchone()
                rp = build_principal(self.ctx.repo, u) if u else None
                if rp is None or not self.access.can(rp, "report", s["report_id"], "view"):
                    raise DeliveryError("The recipient does not have access to this report.", retryable=False)
            ad = self.adapters[d["provider"]]
            if not ad.configured:
                raise DeliveryError(f"{d['provider'].title()} is not configured.", retryable=False)
            rep = self.reports.repo.report(s["report_id"])
            v = self.reports.repo.version(s["report_id"], run["report_version"])
            detail = ad.send({"recipient": d["recipient"], "title": rep["title"], "version": run["report_version"], "readiness": (jload(v["readiness"], {}) or {}).get("status", "unknown"),
                              "link": f"{self.ctx.settings.frontend_base_url}/reports/{s['report_id']}"})
        except DeliveryError as exc:
            err, retryable = str(exc), exc.retryable
        except AppError:
            err, retryable = "The schedule owner no longer has access to this report.", False
        attempts = d["attempts"] + 1
        with self.conn:
            if err is None:
                self.conn.execute("UPDATE sch_deliveries SET outcome = 'sent', attempts = ?, last_error = NULL, next_attempt_at = NULL, last_attempt_at = ? WHERE id = ?", (attempts, now, delivery_id))
            elif not retryable or attempts >= MAX_ATTEMPTS:
                self.conn.execute("UPDATE sch_deliveries SET outcome = 'dead', attempts = ?, last_error = ?, next_attempt_at = NULL, last_attempt_at = ? WHERE id = ?", (attempts, err, now, delivery_id))
            else:
                nxt = (T.parse_utc(now) + timedelta(minutes=BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)])).isoformat()
                self.conn.execute("UPDATE sch_deliveries SET outcome = 'failed', attempts = ?, last_error = ?, next_attempt_at = ?, last_attempt_at = ? WHERE id = ?", (attempts, err, nxt, now, delivery_id))
        self.audit(E.DELIVERY, None, delivery_id, provider=d["provider"], ok=err is None, attempts=attempts)

    # ------------------------------------------------------------------ history and retry
    def get_run_internal(self, run_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM sch_runs WHERE id = ?", (run_id,)).fetchone()
        ds = self.conn.execute("SELECT * FROM sch_deliveries WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)).fetchall()
        return {"id": r["id"], "schedule_id": r["schedule_id"], "scheduled_for": r["scheduled_for"], "started_at": r["started_at"], "finished_at": r["finished_at"], "report_version": r["report_version"],
                "export_id": r["export_id"], "outcome": r["outcome"], "reason": r["reason"], "manual": r["initiated_by"] is not None,
                "deliveries": [{"id": d["id"], "provider": d["provider"], "recipient": d["recipient"], "outcome": d["outcome"], "attempts": d["attempts"], "max_attempts": MAX_ATTEMPTS, "last_error": d["last_error"],
                                "next_attempt_at": d["next_attempt_at"], "dead_letter": d["outcome"] == "dead"} for d in ds]}

    def runs(self, principal: Principal, schedule_id: str, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        self._own(principal, schedule_id)
        lim, off = page(limit, offset, maximum=100, default=25)
        total = self.conn.execute("SELECT COUNT(*) FROM sch_runs WHERE schedule_id = ?", (schedule_id,)).fetchone()[0]
        ids = self.conn.execute("SELECT id FROM sch_runs WHERE schedule_id = ? ORDER BY scheduled_for DESC, rowid DESC LIMIT ? OFFSET ?", (schedule_id, lim, off)).fetchall()
        return {"items": [self.get_run_internal(i["id"]) for i in ids], "total": total, "limit": lim, "offset": off}

    def retry_delivery(self, principal: Principal, delivery_id: str) -> Dict[str, Any]:
        d = self.conn.execute("SELECT d.*, r.schedule_id FROM sch_deliveries d JOIN sch_runs r ON r.id = d.run_id JOIN sch_schedules s ON s.id = r.schedule_id WHERE d.id = ? AND s.owner_user_id = ?", (delivery_id, principal.user_id)).fetchone() if isinstance(delivery_id, str) and len(delivery_id) <= 64 else None
        if d is None:
            raise not_found("delivery")
        if d["outcome"] == "sent":
            raise conflict("ALREADY_SENT", "This delivery was already sent.")
        with self.conn:
            self.conn.execute("UPDATE sch_deliveries SET outcome = 'failed', attempts = MIN(attempts, ?) WHERE id = ?", (MAX_ATTEMPTS - 1, delivery_id))      # a manual retry always gets one real attempt
        self._attempt(delivery_id, manual=True)
        return self.get_run_internal(d["run_id"])

    def download(self, principal: Principal, run_id: str):
        r = self.conn.execute("SELECT r.export_id FROM sch_runs r JOIN sch_schedules s ON s.id = r.schedule_id WHERE r.id = ? AND s.owner_user_id = ?", (run_id, principal.user_id)).fetchone() if isinstance(run_id, str) else None
        if r is None or not r["export_id"]:
            raise not_found("delivery")
        return self.reports.download(principal, r["export_id"])

    # ------------------------------------------------------------------ revocation
    def on_access_change(self, kind: str, info: Dict[str, Any]) -> None:
        """Called by collaboration when a grant, a membership or a workspace stops giving access: schedules that relied on it are blocked and their waiting deliveries are cancelled."""
        for s in self.conn.execute("SELECT * FROM sch_schedules WHERE status IN ('active','paused')").fetchall():
            owner = self._principal_for(s["owner_user_id"])
            ok = owner is not None and self.access.can(owner, "report", s["report_id"], "view")
            if ok:
                continue
            with self.conn:
                self.conn.execute("UPDATE sch_schedules SET status = 'blocked', updated_at = ? WHERE id = ?", (self.now(), s["id"]))
                self.conn.execute("UPDATE sch_deliveries SET outcome = 'dead', last_error = 'Access was revoked.', next_attempt_at = NULL WHERE outcome IN ('pending','failed') AND run_id IN (SELECT id FROM sch_runs WHERE schedule_id = ?)", (s["id"],))
            self.audit(E.SCHEDULE_CHANGED, None, s["id"], action="blocked_by_revocation", cause=kind)
