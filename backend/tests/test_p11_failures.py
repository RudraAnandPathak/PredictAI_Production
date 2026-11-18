"""Failure and recovery checks that did not already have a test: the database failing under a running app, and a write that is interrupted halfway.
(Other failure modes are covered by the tests named in docs/phase11/FAILURE_RECOVERY.md.)"""
import sqlite3
import unittest
from contextlib import contextmanager

from test_identity_api import ApiBase


class DatabaseFailure(ApiBase):
    @contextmanager
    def broken_database(self):
        db = self.api.app.state.db
        real = db.session

        @contextmanager
        def failing(*a, **k):
            raise sqlite3.OperationalError("database is locked: /private/path/predictai.db")
            yield  # pragma: no cover
        db.session = failing
        try:
            yield
        finally:
            db.session = real

    def test_readiness_goes_503_with_a_plain_message_while_liveness_stays_up_and_recovery_is_automatic(self):
        self.assertEqual(self.api.call("GET", "/health/ready").status_code, 200)
        with self.broken_database():
            ready = self.api.call("GET", "/health/ready")
            live = self.api.call("GET", "/health")
        self.assertEqual(self.error_code(ready, 503), "NOT_READY")
        self.assertNotIn("/private/path", ready.text)                 # no path or driver message leaks
        self.assertNotIn("OperationalError", ready.text)
        self.assertEqual(live.status_code, 200)                       # the process is up; only readiness says the database is not
        self.assertEqual(self.api.call("GET", "/health/ready").status_code, 200)    # no restart needed once the database answers again

    def test_a_request_that_needs_the_database_fails_safely_and_the_next_one_works(self):
        with self.broken_database():
            r = self.api.call("POST", "/auth/login", {"email": "alice@example.com", "password": "x"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertNotIn("/private/path", r.text)
        self.assertNotIn("Traceback", r.text)
        self.assertEqual(self.api.call("GET", "/health/ready").status_code, 200)


class MaintenanceMode(ApiBase):
    """Runbook 14 over HTTP: owner turns it on; other accounts can read but not change anything; owner keeps working; turning it off restores writes."""

    def test_maintenance_blocks_other_accounts_writes_but_not_reads_or_the_owner_and_is_reversible(self):
        api = self.api
        api.register("alice@example.com")
        api.owner_signed_in()
        on = api.call("PUT", "/owner/maintenance", {"active": True, "notice": "Back at 10:00", "confirm": True})
        self.assertEqual(on.status_code, 200, on.text)
        self.assertEqual(api.call("POST", "/projects", {"name": "owner may still write"}).status_code, 201)
        api.call("POST", "/auth/logout")
        self.assertEqual(api.login("alice@example.com").status_code, 200)
        blocked = api.call("POST", "/projects", {"name": "blocked"})
        self.assertEqual(self.error_code(blocked, 503), "MAINTENANCE")
        self.assertIn("Back at 10:00", blocked.text)
        self.assertEqual(api.call("GET", "/projects").status_code, 200)                       # reads keep working
        self.assertEqual(api.call("POST", "/auth/logout").status_code in (200, 204), True)    # signing out is always allowed
        api.login("owner@example.com")
        off = api.call("PUT", "/owner/maintenance", {"active": False, "confirm": True})
        self.assertEqual(off.status_code, 200, off.text)
        api.call("POST", "/auth/logout")
        api.login("alice@example.com")
        self.assertEqual(api.call("POST", "/projects", {"name": "works again"}).status_code, 201)

    def test_turning_it_on_needs_explicit_confirmation(self):
        self.api.owner_signed_in()
        r = self.api.call("PUT", "/owner/maintenance", {"active": True, "notice": "x"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertFalse(self.api.call("GET", "/owner/maintenance").json()["data"]["active"])


class InterruptedWrite(ApiBase):
    """Sessions are autocommit by design (app/db/database.py); multi-statement changes must go through transaction()."""

    def setUp(self):
        super().setUp()
        self.db = self.api.app.state.db
        with self.db.session() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS p11_scratch (n INTEGER)")

    def count(self):
        with self.db.session() as conn:
            return conn.execute("SELECT COUNT(*) FROM p11_scratch").fetchone()[0]

    def test_a_transaction_that_fails_halfway_leaves_no_partial_rows(self):
        from app.db.database import transaction
        with self.assertRaises(RuntimeError):
            with self.db.session() as conn, transaction(conn):
                conn.execute("INSERT INTO p11_scratch VALUES (1)")
                conn.execute("INSERT INTO p11_scratch VALUES (2)")
                raise RuntimeError("interrupted")
        self.assertEqual(self.count(), 0)

    def test_a_committed_transaction_is_kept(self):
        from app.db.database import transaction
        with self.db.session() as conn, transaction(conn):
            conn.execute("INSERT INTO p11_scratch VALUES (1)")
        self.assertEqual(self.count(), 1)

    def test_plain_sessions_are_autocommit_so_multi_statement_changes_need_transaction(self):
        """Documents the contract rather than hiding it: without transaction() each statement is its own commit."""
        with self.assertRaises(RuntimeError):
            with self.db.session() as conn:
                conn.execute("INSERT INTO p11_scratch VALUES (1)")
                raise RuntimeError("interrupted")
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
