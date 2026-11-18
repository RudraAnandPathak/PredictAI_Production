"""Scheduled reports: timing and time zones, snapshot vs refresh, idempotency, bounded retries and dead-letter, permission re-checks, revocation, and local delivery without any credentials."""
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError
from app.modules.schedule import timing as T
from app.modules.schedule.adapters import DeliveryError, EmailAdapter, SlackAdapter
from p7_support import P7Base, code_of


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class Timing(P7Base.__mro__[1]):                      # plain TestCase: no database needed
    def test_zones_dst_and_month_ends(self):
        n = T.next_after({"frequency": "daily", "timezone": "Asia/Kolkata", "local_time": "09:00"}, utc("2026-11-14T03:00:00"))
        self.assertEqual(n.isoformat(), "2026-11-14T03:30:00+00:00")
        gap = T.next_after({"frequency": "daily", "timezone": "America/New_York", "local_time": "02:30"}, utc("2026-03-07T12:00:00"))
        self.assertEqual(gap.isoformat(), "2026-03-08T07:30:00+00:00", "a time that does not exist runs at the first valid moment")
        self.assertEqual(T.next_after({"frequency": "monthly", "timezone": "UTC", "local_time": "10:00", "day_of_month": 31}, utc("2026-02-01T00:00:00")).day, 28)
        self.assertEqual(T.next_after({"frequency": "weekly", "timezone": "Europe/London", "local_time": "08:00", "weekday": 0}, utc("2026-11-14T00:00:00")).isoformat(), "2026-11-16T08:00:00+00:00")
        self.assertIsNone(T.next_after({"frequency": "once", "timezone": "UTC", "local_time": "10:00", "run_at": "2020-01-01T00:00:00Z"}, utc("2026-01-01T00:00:00")))
        for bad in ("Mars/Base", "", None, "x" * 80):
            with self.assertRaises(ValueError):
                T.zone(bad)


class FakeEmail:
    kind, needs = "email", "test"

    def __init__(self):
        self.configured, self.fail, self.sent = True, 0, []

    def send(self, notice):
        if self.fail:
            self.fail -= 1
            raise DeliveryError("mail server down")
        self.sent.append(notice)
        return "ok"


class Schedules(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reg = cls.p7.registry.register(cls.alice, cls.train(), name="Sched model")
        cls.s = cls.p7.schedule

    def setUp(self):
        self.rep = self.p7.reports.create(self.alice, self.project, source_kind="prediction", source={"version_ids": [self.reg["versions"][0]["id"]]})
        self.rid = self.rep["id"]
        self.email = FakeEmail()
        self.s.adapters["email"] = self.email
        self.addCleanup(lambda: self.ws.ctx.conn.execute("UPDATE sch_schedules SET status = 'ended', next_run_at = NULL").connection.commit())

    def make(self, **kw):
        spec = {"frequency": "daily", "timezone": "Asia/Kolkata", "local_time": "09:00", "export_format": "md", "mode": "refresh", **kw}
        return self.s.create(self.alice, self.rid, spec)

    def go(self, **delta):
        self.h.clock.advance(**delta)
        return self.s.tick()

    def test_local_mode_works_with_no_credentials_and_creates_a_downloadable_record(self):
        self.s.adapters["email"] = EmailAdapter(self.ws.ctx.settings, self.ws.ctx.mailer.sender)
        self.assertFalse(self.s.adapters["email"].configured and self.ws.ctx.settings.email_backend != "smtp")
        sc = self.make()
        self.assertEqual(sc["recipients"], [{"kind": "local", "target": "me"}])
        self.assertEqual(self.go(hours=0)["ran"], 0, "nothing is due yet")
        self.assertEqual(self.go(days=1)["ran"], 1)
        run = self.s.runs(self.alice, sc["id"])["items"][0]
        self.assertEqual(run["outcome"], "ok")
        self.assertEqual([d["outcome"] for d in run["deliveries"]], ["sent"])
        name, mime, data = self.s.download(self.alice, run["id"])
        self.assertIn(b"Version", data)

    def test_email_and_slack_are_refused_until_credentials_exist(self):
        self.s.adapters["email"] = EmailAdapter(self.ws.ctx.settings, self.ws.ctx.mailer.sender)
        self.s.adapters["slack"] = SlackAdapter(url="")
        for kind, target in (("email", "x@example.com"), ("slack", "c")):
            c = code_of(self.s.create, self.alice, self.rid, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00", "recipients": [{"kind": kind, "target": target}]})
            self.assertEqual(c[0], "ADAPTER_NOT_CONFIGURED", kind)
        self.assertFalse(SlackAdapter(url="http://hooks.slack.com/services/x").configured, "https only")
        self.assertFalse(SlackAdapter(url="https://evil.example/services/x").configured, "only hooks.slack.com")
        with self.assertRaises(DeliveryError):
            SlackAdapter(url="").send({})
        self.assertTrue(SlackAdapter(url="https://hooks.slack.com/services/T/B/x").configured)

    def test_a_run_is_idempotent_and_a_missed_period_is_not_replayed(self):
        sc = self.make()
        self.assertEqual(self.go(days=1)["ran"], 1)
        self.assertEqual(self.s.tick()["ran"], 0, "same time, second tick: nothing more")
        self.assertEqual(self.go(days=5)["ran"], 1, "five missed days produce one run, not five")
        self.assertEqual(self.s.runs(self.alice, sc["id"])["total"], 2)
        a = self.s.run_now(self.alice, sc["id"], idempotency_key="k1")
        b = self.s.run_now(self.alice, sc["id"], idempotency_key="k1")
        self.assertEqual(a["id"], b["id"])
        self.assertNotEqual(self.s.run_now(self.alice, sc["id"], idempotency_key="k2")["id"], a["id"])

    def test_snapshot_sends_the_pinned_version_and_refresh_the_latest(self):
        snap = self.make(mode="snapshot")
        fresh = self.make(mode="refresh")
        self.assertEqual(snap["pinned_version"], 1)
        self.p7.reports.edit(self.alice, self.rid, 1, [{"op": "set_title", "text": "Updated title"}])
        self.go(days=1)
        v = {r["id"]: self.s.runs(self.alice, r["id"])["items"][0]["report_version"] for r in (snap, fresh)}
        self.assertEqual(v[snap["id"]], 1)
        self.assertEqual(v[fresh["id"]], 2)
        self.assertEqual(code_of(self.s.create, self.alice, self.rid, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00", "mode": "snapshot", "pinned_version": 9})[0], "VALIDATION_ERROR")

    def test_failed_deliveries_retry_a_bounded_number_of_times_then_dead_letter_and_can_be_retried_by_hand(self):
        sc = self.make(recipients=[{"kind": "email", "target": "bob@example.com"}])
        self.c = self.p7.collab
        w = self.c.create_workspace(self.alice, name="Delivery team")["id"]
        inv = self.c.invite(self.alice, w, email="bob@example.com", role="viewer")
        self.c.respond(self.bob, inv["invite_link"].split("token=")[1], True)
        self.c.share(self.alice, w, resource_type="report", resource_id=self.rid)
        self.email.fail = 10
        self.go(days=1)
        d = self.s.runs(self.alice, sc["id"])["items"][0]["deliveries"][0]
        self.assertEqual((d["outcome"], d["attempts"]), ("failed", 1))
        self.go(minutes=2)
        self.go(minutes=10)
        d = self.s.runs(self.alice, sc["id"])["items"][0]["deliveries"][0]
        self.assertEqual((d["outcome"], d["attempts"], d["dead_letter"]), ("dead", 3, True), "bounded: three attempts, then dead-letter")
        self.go(hours=5)
        self.assertEqual(self.s.runs(self.alice, sc["id"])["items"][0]["deliveries"][0]["attempts"], 3, "no further automatic attempts")
        self.email.fail = 0
        out = self.s.retry_delivery(self.alice, d["id"])
        self.assertEqual(out["deliveries"][0]["outcome"], "sent")
        self.assertEqual(self.email.sent[-1]["recipient"], "bob@example.com")
        self.assertNotIn("customers", str(self.email.sent[-1]), "a notice carries no report content")
        self.assertEqual(code_of(self.s.retry_delivery, self.alice, d["id"])[0], "ALREADY_SENT")
        self.assertEqual(code_of(self.s.retry_delivery, self.bob, d["id"])[1], 404)

    def test_a_recipient_without_access_is_never_notified(self):
        sc = self.make(recipients=[{"kind": "email", "target": "carol@example.com"}])
        self.go(days=1)
        d = self.s.runs(self.alice, sc["id"])["items"][0]["deliveries"][0]
        self.assertEqual(d["outcome"], "dead")
        self.assertIn("access", d["last_error"])
        self.assertEqual(self.email.sent, [])

    def test_revoking_access_blocks_the_schedule_and_cancels_pending_deliveries(self):
        c = self.p7.collab
        w = c.create_workspace(self.alice, name="Revocation team")["id"]
        inv = c.invite(self.alice, w, email="bob@example.com", role="viewer")
        c.respond(self.bob, inv["invite_link"].split("token=")[1], True)
        g = c.share(self.alice, w, resource_type="report", resource_id=self.rid)
        sc = self.s.create(self.bob, self.rid, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00", "export_format": "md"})
        self.assertEqual(self.s.get(self.bob, sc["id"])["status"], "active")
        c.unshare(self.alice, g["grant_id"])
        self.assertEqual(self.s.get(self.bob, sc["id"])["status"], "blocked", "revocation propagates to the schedule at once")
        self.go(days=2)
        self.assertEqual(self.s.runs(self.bob, sc["id"])["total"], 0, "a blocked schedule does not run")
        self.assertEqual(code_of(self.s.set_status, self.bob, sc["id"], "resume")[0], "ACCESS_REVOKED")
        self.assertEqual(code_of(self.s.create, self.bob, self.rid, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00"})[1], 404)

    def test_permission_is_rechecked_at_run_time_even_if_nothing_told_the_scheduler(self):
        sc = self.make()
        self.ws.ctx.conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (self.alice.user_id,))
        try:
            self.go(days=1)
            run = self.s.runs(self.alice, sc["id"])["items"][0] if False else self.ws.ctx.conn.execute("SELECT outcome, reason FROM sch_runs WHERE schedule_id = ?", (sc["id"],)).fetchone()
            self.assertEqual(run["outcome"], "blocked")
        finally:
            self.ws.ctx.conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (self.alice.user_id,))

    def test_one_off_runs_once_and_ends_and_validation(self):
        at = (self.h.clock.now() + timedelta(hours=3)).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
        sc = self.s.create(self.alice, self.rid, {"frequency": "once", "timezone": "Asia/Kolkata", "run_at": at, "export_format": "md"})
        self.assertEqual(self.go(hours=1)["ran"], 0)
        self.assertEqual(self.go(hours=3)["ran"], 1)
        self.assertEqual(self.s.get(self.alice, sc["id"])["status"], "ended")
        self.assertEqual(self.go(days=3)["ran"], 0)
        for bad in ({"frequency": "hourly"}, {"frequency": "daily", "timezone": "Nowhere/City", "local_time": "09:00"}, {"frequency": "daily", "timezone": "UTC", "local_time": "25:00"},
                    {"frequency": "weekly", "timezone": "UTC", "local_time": "09:00"}, {"frequency": "monthly", "timezone": "UTC", "local_time": "09:00", "day_of_month": 40},
                    {"frequency": "once", "timezone": "UTC", "run_at": "2020-01-01T00:00:00Z"}, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00", "export_format": "exe"}):
            self.assertEqual(code_of(self.s.create, self.alice, self.rid, bad)[0], "VALIDATION_ERROR", str(bad))

    def test_schedules_belong_to_their_owner(self):
        sc = self.make()
        for fn, args in ((self.s.get, (sc["id"],)), (self.s.runs, (sc["id"],)), (self.s.run_now, (sc["id"],)), (self.s.set_status, (sc["id"], "pause"))):
            self.assertEqual(code_of(fn, self.bob, *args)[1], 404)
        self.assertEqual(code_of(self.s.create, self.bob, self.rid, {"frequency": "daily", "timezone": "UTC", "local_time": "09:00"})[1], 404)
        self.assertNotIn(sc["id"], [x["id"] for x in self.s.list(self.bob)["items"]])
        paused = self.s.set_status(self.alice, sc["id"], "pause")
        self.assertEqual(paused["status"], "paused")
        self.go(days=2)
        self.assertEqual(self.s.runs(self.alice, sc["id"])["total"], 0)
        self.assertEqual(self.s.set_status(self.alice, sc["id"], "resume")["status"], "active")
