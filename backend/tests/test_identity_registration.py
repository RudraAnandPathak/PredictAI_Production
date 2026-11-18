import unittest

from app.core.errors import AppError
from app.modules.identity.constants import Event, Role
from support import GOOD_PASSWORD, Harness


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def assertError(self, code, status, fn, *args, **kwargs):
        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), (code, status))
        return ctx.exception

    def test_successful_registration_creates_an_active_user_with_a_hashed_password(self):
        result = self.h.register("Alice@Example.com", GOOD_PASSWORD, "  Alice   Smith ")
        self.assertEqual(result, {"verification_required": False})
        user = self.h.rows("SELECT * FROM users")[0]
        self.assertEqual((user["email"], user["email_normalized"], user["display_name"], user["status"]),
                         ("Alice@Example.com", "alice@example.com", "Alice Smith", "active"))
        identity = self.h.rows("SELECT * FROM identities")[0]
        self.assertTrue(identity["password_hash"].startswith("scrypt$"))
        self.assertNotIn(GOOD_PASSWORD, self.h.dump(), "plaintext password must never be stored")
        self.assertEqual(self.h.rows("SELECT role FROM user_roles"), [{"role": Role.USER}])
        self.assertEqual(self.h.rows("SELECT event_type FROM audit_events")[0]["event_type"], Event.REGISTRATION_CREATED)

    def test_email_is_validated_and_unique_ignoring_case(self):
        for bad in ("", "nope", "a@b", "a b@example.com", "x@@example.com", "a..b@example.com", None, 42):
            with self.subTest(email=bad):
                exc = self.assertError("VALIDATION_ERROR", 422, self.h.register, bad, GOOD_PASSWORD)
                self.assertEqual(exc.details[0]["field"], "email")
        self.h.register("alice@example.com")
        self.h.register("ALICE@example.COM")
        self.assertEqual(len(self.h.rows("SELECT id FROM users")), 1)

    def test_duplicate_registration_looks_identical_and_notifies_the_owner_of_the_address(self):
        first = self.h.register("alice@example.com")
        second = self.h.register("alice@example.com", "a-completely-different-pass-9")
        self.assertEqual(first, second, "outward result must not reveal that the address exists")
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM identities")[0]["n"], 1)
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Someone tried to register with your email")
        audit = self.h.rows("SELECT event_type, outcome FROM audit_events WHERE event_type = ?", (Event.REGISTRATION_DUPLICATE,))
        self.assertEqual(audit, [{"event_type": Event.REGISTRATION_DUPLICATE, "outcome": "failure"}])

    def test_password_policy_is_enforced_with_specific_messages(self):
        cases = {"short": "too_short", "password123": "too_common", "aaaaaaaaaaaa": "too_common", "my-alice.smith-pass": "contains_email"}
        for password, issue in cases.items():
            with self.subTest(password=password):
                exc = self.assertError("VALIDATION_ERROR", 422, self.h.register, "alice.smith@example.com", password)
                self.assertIn(issue, [p["issue"] for p in exc.details])
        self.assertEqual(self.h.rows("SELECT id FROM users"), [])

    def test_display_name_rules(self):
        exc = self.assertError("VALIDATION_ERROR", 422, self.h.register, "a@example.com", GOOD_PASSWORD, "x" * 61)
        self.assertEqual(exc.details[0]["field"], "display_name")
        self.h.register("b@example.com", GOOD_PASSWORD, "   ")
        self.assertIsNone(self.h.rows("SELECT display_name FROM users WHERE email_normalized = 'b@example.com'")[0]["display_name"])

    def test_registration_is_rate_limited_per_ip(self):
        for i in range(10):
            self.h.register(f"user{i}@example.com", ip="198.51.100.9")
        self.assertError("RATE_LIMITED", 429, self.h.register, "user99@example.com", ip="198.51.100.9")
        self.h.register("other@example.com", ip="198.51.100.10")  # a different address is unaffected
        self.h.clock.advance(hours=1, seconds=5)
        self.h.register("later@example.com", ip="198.51.100.9")

    def test_rate_limit_error_carries_retry_after(self):
        for i in range(10):
            self.h.register(f"user{i}@example.com", ip="198.51.100.9")
        exc = self.assertError("RATE_LIMITED", 429, self.h.register, "x@example.com", ip="198.51.100.9")
        self.assertGreater(int(exc.headers["Retry-After"]), 0)


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(require_email_verification=True)
        self.addCleanup(self.h.close)

    def test_registration_creates_a_pending_account_and_sends_one_link(self):
        self.assertEqual(self.h.register("alice@example.com"), {"verification_required": True})
        self.assertEqual(self.h.rows("SELECT status, verified_at FROM users"), [{"status": "pending", "verified_at": None}])
        self.assertEqual(len(self.h.outbox.messages), 1)
        self.assertIn("/verify-email#token=", self.h.last_email().body)  # fragment: never sent to servers or logs

    def test_pending_accounts_cannot_sign_in_until_verified(self):
        self.h.register("alice@example.com")
        with self.assertRaises(AppError) as ctx:
            self.h.login("alice@example.com")
        self.assertEqual(ctx.exception.code, "INVALID_CREDENTIALS")
        token = self.h.last_token()
        self.h.services().auth.verify_email(token)
        row = self.h.rows("SELECT status, verified_at FROM users")[0]
        self.assertEqual(row["status"], "active")
        self.assertIsNotNone(row["verified_at"])
        self.assertTrue(self.h.login("alice@example.com").principal.email_verified)

    def test_token_is_single_use_and_expires(self):
        self.h.register("alice@example.com")
        token = self.h.last_token()
        self.h.services().auth.verify_email(token)
        with self.assertRaises(AppError) as reuse:
            self.h.services().auth.verify_email(token)
        self.assertEqual(reuse.exception.code, "INVALID_TOKEN")

        self.h.register("bob@example.com")
        token = self.h.last_token("bob@example.com")
        self.h.clock.advance(hours=25)
        with self.assertRaises(AppError) as expired:
            self.h.services().auth.verify_email(token)
        self.assertEqual(expired.exception.code, "INVALID_TOKEN")

    def test_garbage_tokens_are_rejected(self):
        for token in ("", "nope", None, 123):
            with self.subTest(token=token), self.assertRaises(AppError) as ctx:
                self.h.services().auth.verify_email(token)
            self.assertEqual(ctx.exception.code, "INVALID_TOKEN")

    def test_resend_replaces_the_old_link_and_reveals_nothing(self):
        self.h.register("alice@example.com")
        old = self.h.last_token()
        self.h.services().auth.resend_verification("alice@example.com")
        new = self.h.last_token()
        self.assertNotEqual(old, new)
        with self.assertRaises(AppError):
            self.h.services().auth.verify_email(old)
        self.h.services().auth.verify_email(new)
        before = len(self.h.outbox.messages)
        self.h.services().auth.resend_verification("nobody@example.com")   # unknown: silent
        self.h.services().auth.resend_verification("alice@example.com")    # already verified: silent
        self.assertEqual(len(self.h.outbox.messages), before)

    def test_resend_is_rate_limited_per_email(self):
        self.h.register("alice@example.com")
        for _ in range(3):
            self.h.services().auth.resend_verification("alice@example.com")
        with self.assertRaises(AppError) as ctx:
            self.h.services().auth.resend_verification("alice@example.com")
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")


if __name__ == "__main__":
    unittest.main()
