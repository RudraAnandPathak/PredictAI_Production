"""Phase 7: user dashboard and Owner Command Center (authorisation, maintenance, quota policy, metadata-only boundary, honest monitoring)."""
import json
import unittest

from p7_support import P7Base, code_of
from app.core.errors import AppError


class DashboardOwnerTests(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner, _ = cls.h.make_owner("boss@example.com")

    def setUp(self):
        self.p7.owner.set_maintenance(self.owner, False, None, True)

    # ---------------------------------------------------------------- dashboard
    def test_dashboard_is_user_scoped_and_real(self):
        a = self.p7.dashboard.summary(self.alice)
        b = self.p7.dashboard.summary(self.bob)
        self.assertTrue(a["complete"])
        self.assertGreaterEqual(a["sections"]["work"]["datasets"], 1)
        self.assertEqual(b["sections"]["work"]["datasets"], 0)
        self.assertEqual(b["sections"]["work"]["projects"], 0)
        self.assertIn("documents_remaining", a["sections"]["quotas"])
        self.assertEqual(a["user"]["tier"], "standard")

    def test_one_broken_section_does_not_break_the_page(self):
        original = self.p7.dashboard.access.shared_with
        self.p7.dashboard.access.shared_with = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            d = self.p7.dashboard.summary(self.alice)
        finally:
            self.p7.dashboard.access.shared_with = original
        self.assertEqual(d["sections"]["collaboration"]["state"], "unavailable")
        self.assertIn("recovery", d["sections"]["collaboration"])
        self.assertEqual(d["sections"]["work"]["state"], "ok")
        self.assertEqual(d["degraded"], ["collaboration"])
        self.assertFalse(d["complete"])
        self.assertNotIn("boom", json.dumps(d))

    # ---------------------------------------------------------------- owner authorisation
    def test_non_owner_is_denied_everywhere(self):
        for fn in (lambda: self.p7.owner.overview(self.alice), lambda: self.p7.owner.monitoring(self.alice),
                   lambda: self.p7.owner.quota_policy(self.alice), lambda: self.p7.owner.events(self.alice),
                   lambda: self.p7.owner.user_summary(self.alice, self.bob.user_id),
                   lambda: self.p7.owner.set_maintenance(self.alice, True, "x", True),
                   lambda: self.p7.owner.set_quota(self.alice, "standard_max_documents", 99)):
            self.assertEqual(code_of(fn)[1], 403)
        self.assertFalse(self.p7.owner.maintenance()["active"])

    # ---------------------------------------------------------------- maintenance
    def test_maintenance_needs_confirmation_and_blocks_only_non_owner_changes(self):
        self.assertEqual(code_of(lambda: self.p7.owner.set_maintenance(self.owner, True, "back soon", False))[1], 422)
        self.assertEqual(code_of(lambda: self.p7.owner.set_maintenance(self.owner, "yes", "x", True))[1], 422)
        self.assertFalse(self.p7.owner.maintenance()["active"])
        state = self.p7.owner.set_maintenance(self.owner, True, "Upgrading storage", True)
        self.assertTrue(state["active"])
        self.assertEqual(state["owner_access"], "preserved")
        code, status = code_of(self.p7.owner.guard_write, self.alice)
        self.assertEqual((code, status), ("MAINTENANCE", 503))
        self.p7.owner.guard_write(self.owner)                      # owner keeps working
        self.assertEqual(self.p7.dashboard.summary(self.alice)["sections"]["work"]["state"], "ok")  # reads keep working
        self.assertEqual(self.p7.schedule.tick()["skipped"], "maintenance")
        ev = self.p7.owner.events(self.owner)["items"]
        self.assertEqual(ev[0]["action"], "maintenance_on")
        self.p7.owner.set_maintenance(self.owner, False, None, True)
        self.p7.owner.guard_write(self.alice)
        self.assertNotIn("skipped", self.p7.schedule.tick())

    # ---------------------------------------------------------------- quota policy
    def test_quota_policy_bounds_and_effect(self):
        self.assertEqual(code_of(lambda: self.p7.owner.set_quota(self.owner, "nonsense", 3))[1], 422)
        for bad in (0, -1, 10_000, "5", True, 2.5):
            self.assertEqual(code_of(lambda v=bad: self.p7.owner.set_quota(self.owner, "standard_max_documents", v))[1], 422)
        self.p7.owner.set_quota(self.owner, "standard_max_documents", 7)
        try:
            self.assertEqual(self.ws.quota.summary(self.alice)["max_documents"], 7)
            item = [i for i in self.p7.owner.quota_policy(self.owner)["items"] if i["key"] == "standard_max_documents"][0]
            self.assertEqual((item["override"], item["effective"]), (7, 7))
        finally:
            self.p7.owner.reset_quota(self.owner, "standard_max_documents")
        self.assertEqual(self.ws.quota.summary(self.alice)["max_documents"], 50)   # harness default restored
        self.assertTrue(self.h.rows("SELECT 1 FROM own_events WHERE action = 'quota_set'"))

    # ---------------------------------------------------------------- privacy boundary
    def test_owner_views_never_contain_user_titles_or_content(self):
        pid = self.ws.projects.create(self.bob, "SECRET TITLE quarterly layoffs")["id"]
        self.assertTrue(pid)
        blob = json.dumps([self.p7.owner.overview(self.owner), self.p7.owner.monitoring(self.owner),
                           self.p7.owner.user_summary(self.owner, self.bob.user_id), self.p7.owner.events(self.owner),
                           self.p7.owner.quota_policy(self.owner)])
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("customers.csv", blob)
        self.assertGreaterEqual(self.p7.owner.user_summary(self.owner, self.bob.user_id)["projects"], 1)
        self.assertEqual(code_of(lambda: self.p7.owner.user_summary(self.owner, "nope"))[1], 404)

    # ---------------------------------------------------------------- monitoring honesty
    def test_monitoring_says_unavailable_when_it_cannot_measure(self):
        m = self.p7.owner.monitoring(self.owner)
        by = {c["name"]: c for c in m["checks"]}
        self.assertEqual(by["cpu_memory"]["state"], "unavailable")
        self.assertEqual(by["database"]["state"], "ok")
        self.assertEqual(m["overall"], "unavailable")   # the worst state wins; it is never rounded up to "ok"

    def test_stale_job_heartbeat_is_reported(self):
        self.train()                                        # a real job row to age
        j = self.h.rows("SELECT id FROM ml_jobs LIMIT 1")
        self.h.conn.execute("UPDATE ml_jobs SET status='running', heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id = ?", (j[0]["id"],))
        try:
            by = {c["name"]: c for c in self.p7.owner.monitoring(self.owner)["checks"]}
            self.assertEqual(by["training_jobs"]["state"], "stale")
        finally:
            self.h.conn.execute("UPDATE ml_jobs SET status='completed' WHERE id = ?", (j[0]["id"],))


if __name__ == "__main__":
    unittest.main()
