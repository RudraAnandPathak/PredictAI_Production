"""Magic-link sign-in: only a verified mailbox, one use, short life, nothing enumerable, nothing stored in the clear."""
import unittest

from app.core.errors import AppError
from app.modules.identity.constants import Event
from support import GOOD_PASSWORD, Harness
from test_identity_api import ApiBase


def verified(h, email="alice@example.com"):
    h.register(email)
    h.conn.execute("UPDATE users SET verified_at = ? WHERE email_normalized = ?", (h.clock.iso(), email))


class MagicLink(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def auth(self, ip="203.0.113.7"):
        return self.h.services(ip=ip).auth

    def code_of(self, fn, *a):
        with self.assertRaises(AppError) as c:
            fn(*a)
        return c.exception

    def events(self, name):
        return self.h.rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", (name,))

    def test_a_verified_account_signs_in_once_with_the_mailed_link(self):
        verified(self.h)
        self.auth().request_magic_link("Alice@Example.com")
        self.assertIn("/magic-link#token=", self.h.last_email().body)
        token = self.h.last_token("alice@example.com")
        result = self.auth().consume_magic_link(token, False)
        self.assertEqual(result.principal.email, "alice@example.com")
        self.assertTrue(self.h.principal(result.session.token).user_id)
        self.assertEqual(self.code_of(self.auth().consume_magic_link, token, False).code, "INVALID_TOKEN")      # second use
        self.assertEqual(len(self.events(Event.MAGIC_LINK_USED)), 1)

    def test_the_raw_token_is_never_stored(self):
        verified(self.h)
        self.auth().request_magic_link("alice@example.com")
        self.assertNotIn(self.h.last_token("alice@example.com"), self.h.dump())

    def test_unknown_unverified_and_blocked_accounts_get_the_same_answer_and_no_mail(self):
        self.h.register("pending@example.com")                                   # active but mailbox never verified
        verified(self.h, "blocked@example.com")
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE email_normalized = 'blocked@example.com'")
        before = len(self.h.outbox.messages)
        for email in ("ghost@example.com", "pending@example.com", "blocked@example.com", "not-an-email"):
            self.assertIsNone(self.auth().request_magic_link(email))
        self.assertEqual(len(self.h.outbox.messages), before)

    def test_a_link_expires_after_fifteen_minutes(self):
        verified(self.h)
        self.auth().request_magic_link("alice@example.com")
        token = self.h.last_token("alice@example.com")
        self.h.clock.advance(minutes=16)
        self.assertEqual(self.code_of(self.auth().consume_magic_link, token, False).code, "INVALID_TOKEN")

    def test_a_new_request_replaces_the_old_link(self):
        verified(self.h)
        self.auth().request_magic_link("alice@example.com")
        first = self.h.last_token("alice@example.com")
        self.auth().request_magic_link("alice@example.com")
        second = self.h.last_token("alice@example.com")
        self.assertNotEqual(first, second)
        self.assertEqual(self.code_of(self.auth().consume_magic_link, first, False).code, "INVALID_TOKEN")
        self.assertTrue(self.auth().consume_magic_link(second, False).principal.user_id)

    def test_an_account_blocked_after_the_link_was_sent_cannot_use_it(self):
        verified(self.h)
        self.auth().request_magic_link("alice@example.com")
        token = self.h.last_token("alice@example.com")
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE email_normalized = 'alice@example.com'")
        self.assertEqual(self.code_of(self.auth().consume_magic_link, token, False).code, "INVALID_TOKEN")

    def test_other_kinds_of_token_do_not_work_as_a_sign_in_link(self):
        verified(self.h)
        self.auth().request_recovery("alice@example.com")
        reset_token = self.h.last_token("alice@example.com")
        self.assertEqual(self.code_of(self.auth().consume_magic_link, reset_token, False).code, "INVALID_TOKEN")
        for bad in ("", "x" * 500, None, 7):
            self.assertEqual(self.code_of(self.auth().consume_magic_link, bad, False).code, "INVALID_TOKEN")
        self.assertGreaterEqual(len(self.events(Event.MAGIC_LINK_REFUSED)), 5)

    def test_requests_are_rate_limited_per_address(self):
        verified(self.h)
        for _ in range(3):
            self.auth().request_magic_link("alice@example.com")
        self.assertEqual(self.code_of(self.auth().request_magic_link, "alice@example.com").code, "RATE_LIMITED")


class MagicLinkHttp(ApiBase):
    def test_flow_over_http(self):
        self.api.register()
        self.api.h.conn.execute("UPDATE users SET verified_at = ? WHERE email_normalized = 'alice@example.com'", (self.api.h.clock.iso(),))
        self.assertEqual(self.api.call("POST", "/auth/magic-link/request", {"email": "alice@example.com"}).status_code, 202)
        self.assertEqual(self.api.call("POST", "/auth/magic-link/request", {"email": "ghost@example.com"}).json()["data"], {"sent": True})
        token = self.api.h.last_token("alice@example.com")
        self.assertEqual(self.error_code(self.api.call("POST", "/auth/magic-link/consume", {"token": "bogus"}), 400), "INVALID_TOKEN")
        ok = self.api.call("POST", "/auth/magic-link/consume", {"token": token})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("httponly", ok.headers.get("set-cookie", "").lower())             # a session cookie was issued; reuse of the link is covered at service level


if __name__ == "__main__":
    unittest.main()
