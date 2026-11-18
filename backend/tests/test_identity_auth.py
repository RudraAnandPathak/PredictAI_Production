import unittest
from dataclasses import replace

from app.core.errors import AppError
from app.modules.identity.constants import Event
from app.modules.identity.passwords import PasswordHasher
from support import GOOD_PASSWORD, OTHER_PASSWORD, Harness


class Base(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def code_of(self, fn, *args, **kwargs):
        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        return ctx.exception

    def events(self, event_type):
        return self.h.rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", (event_type,))


class LoginTests(Base):
    def test_successful_login_returns_a_session_and_principal(self):
        self.h.register("alice@example.com", GOOD_PASSWORD, "Alice")
        result = self.h.login("Alice@Example.com")
        self.assertEqual((result.principal.email, result.principal.display_name), ("alice@example.com", "Alice"))
        self.assertTrue(result.session.persistent)
        self.assertGreater(result.session.max_age_seconds, 0)
        self.assertEqual(self.events(Event.LOGIN_SUCCESS)[0]["actor_user_id"], result.principal.user_id)

    def test_wrong_password_and_unknown_email_give_the_same_error(self):
        self.h.register("alice@example.com")
        wrong = self.code_of(self.h.login, "alice@example.com", "not-the-password")
        unknown = self.code_of(self.h.login, "nobody@example.com", GOOD_PASSWORD)
        self.assertEqual((wrong.code, wrong.status_code, wrong.message), (unknown.code, unknown.status_code, unknown.message))
        self.assertEqual(wrong.code, "INVALID_CREDENTIALS")
        reasons = [e["reason"] for e in self.events(Event.LOGIN_FAILURE)]
        self.assertEqual(reasons, ["bad_password", "unknown_account"])  # distinguishable only in the audit log

    def test_malformed_input_never_crashes_and_gives_the_same_error(self):
        for email, password in ((None, GOOD_PASSWORD), ("alice@example.com", None), (42, 42), ("", ""), ("x" * 5000, "y")):
            with self.subTest(email=str(email)[:10]):
                self.assertEqual(self.code_of(self.h.login, email, password).code, "INVALID_CREDENTIALS")

    def test_blocked_and_pending_accounts_cannot_sign_in_even_with_the_right_password(self):
        user, _ = self.h.make_user("alice@example.com")
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (user.user_id,))
        self.assertEqual(self.code_of(self.h.login, "alice@example.com").code, "INVALID_CREDENTIALS")
        self.h.conn.execute("UPDATE users SET status = 'pending' WHERE id = ?", (user.user_id,))
        self.assertEqual(self.code_of(self.h.login, "alice@example.com").code, "INVALID_CREDENTIALS")
        self.assertEqual([e["reason"] for e in self.events(Event.LOGIN_FAILURE)], ["account_blocked", "account_pending"])

    def test_repeated_failures_lock_the_account_even_for_the_correct_password(self):
        self.h.register("alice@example.com")
        for _ in range(5):
            self.assertEqual(self.code_of(self.h.login, "alice@example.com", "wrong-password-1").code, "INVALID_CREDENTIALS")
        locked = self.code_of(self.h.login, "alice@example.com", GOOD_PASSWORD)
        self.assertEqual((locked.code, locked.status_code), ("RATE_LIMITED", 429))
        self.assertGreater(int(locked.headers["Retry-After"]), 0)
        self.assertEqual(len(self.events(Event.LOGIN_LOCKED)), 1)
        self.h.clock.advance(minutes=16)
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)

    def test_lockout_cannot_be_extended_by_hammering(self):
        self.h.register("alice@example.com")
        for _ in range(5):
            self.code_of(self.h.login, "alice@example.com", "wrong-password-1")
        for _ in range(10):
            self.assertEqual(self.code_of(self.h.login, "alice@example.com", "wrong-password-1").code, "RATE_LIMITED")
        self.h.clock.advance(minutes=15, seconds=5)  # blocked attempts were not counted, so the lock still ends on schedule
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)

    def test_unknown_emails_are_locked_too_so_lockout_reveals_nothing(self):
        for _ in range(5):
            self.code_of(self.h.login, "ghost@example.com", "wrong-password-1")
        self.assertEqual(self.code_of(self.h.login, "ghost@example.com", "wrong-password-1").code, "RATE_LIMITED")

    def test_lockout_is_per_account(self):
        self.h.register("alice@example.com")
        self.h.register("bob@example.com", OTHER_PASSWORD)
        for _ in range(5):
            self.code_of(self.h.login, "alice@example.com", "wrong-password-1")
        self.assertTrue(self.h.login("bob@example.com", OTHER_PASSWORD).principal.user_id)

    def test_ip_rate_limit_applies_across_accounts(self):
        for i in range(30):
            self.code_of(self.h.login, f"user{i}@example.com", "wrong-password-1", ip="198.51.100.5")
        self.assertEqual(self.code_of(self.h.login, "alice@example.com", GOOD_PASSWORD, ip="198.51.100.5").code, "RATE_LIMITED")

    def test_old_hash_parameters_are_upgraded_at_sign_in(self):
        self.h.register("alice@example.com")
        weak = PasswordHasher(2**9, 8, 1).hash(GOOD_PASSWORD)
        self.h.conn.execute("UPDATE identities SET password_hash = ?", (weak,))
        self.h.login("alice@example.com")
        stored = self.h.rows("SELECT password_hash FROM identities")[0]["password_hash"]
        self.assertTrue(stored.startswith(f"scrypt${self.h.settings.password_hash_params[0]}$"))
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)

    def test_every_login_issues_a_fresh_stored_hashed_token(self):
        self.h.register("alice@example.com")
        a, b = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        self.assertNotEqual(a.session.token, b.session.token)
        self.assertNotIn(a.session.token, self.h.dump())  # only a hash is stored
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 2)

    def test_remember_me_false_is_not_persistent(self):
        self.h.register("alice@example.com")
        self.assertFalse(self.h.login("alice@example.com", remember=False).session.persistent)


class SessionTests(Base):
    def test_valid_token_authenticates_and_bad_tokens_do_not(self):
        principal, token = self.h.make_user("alice@example.com")
        self.assertEqual(self.h.principal(token).user_id, principal.user_id)
        self.assertEqual(self.code_of(self.h.principal, "").code, "UNAUTHENTICATED")
        self.assertEqual(self.code_of(self.h.principal, "made-up-token").code, "SESSION_EXPIRED")

    def test_idle_timeout_and_sliding_activity(self):
        _, token = self.h.make_user("alice@example.com")
        self.h.clock.advance(hours=20)
        self.h.principal(token)                       # activity moves the idle deadline
        self.h.clock.advance(hours=20)
        self.h.principal(token)
        self.h.clock.advance(hours=25)                # 25 h without activity: idle limit is 24 h
        self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")

    def test_absolute_lifetime_applies_even_with_constant_activity(self):
        _, token = self.h.make_user("alice@example.com")
        for _ in range(7):
            self.h.clock.advance(hours=23)
            self.h.principal(token)
        self.h.clock.advance(hours=23)                # 8 days after sign-in, absolute limit is 7 days
        self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")

    def test_owner_sessions_use_the_stricter_policy(self):
        _, owner_token = self.h.make_owner()
        _, user_token = self.h.make_user("alice@example.com")
        self.h.clock.advance(minutes=61)
        self.assertEqual(self.code_of(self.h.principal, owner_token).code, "SESSION_EXPIRED")
        self.assertTrue(self.h.principal(user_token))

    def test_logout_ends_only_that_session(self):
        self.h.register("alice@example.com")
        first, second = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        self.h.services().auth.logout(first.session.token)
        self.assertEqual(self.code_of(self.h.principal, first.session.token).code, "SESSION_EXPIRED")
        self.assertTrue(self.h.principal(second.session.token))
        self.assertEqual(len(self.events(Event.LOGOUT)), 1)

    def test_logout_everywhere_and_logout_with_unknown_token(self):
        self.h.register("alice@example.com")
        first, second = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        self.h.services().auth.logout(first.session.token, everywhere=True)
        for token in (first.session.token, second.session.token):
            self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")
        self.h.services().auth.logout("unknown")      # must not raise
        self.h.services().auth.logout(None)

    def test_blocking_a_user_invalidates_the_existing_session_immediately(self):
        user, token = self.h.make_user("alice@example.com")
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (user.user_id,))
        self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")
        self.assertEqual(self.events(Event.SESSION_REJECTED)[0]["reason"], "account_not_active")

    def test_session_listing_marks_current_and_hides_other_users(self):
        self.h.register("alice@example.com")
        mine, other_device = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        self.h.make_user("bob@example.com", OTHER_PASSWORD)
        principal = self.h.principal(mine.session.token)
        sessions = self.h.services().auth.list_sessions(principal)
        self.assertEqual(len(sessions), 2)
        self.assertEqual([s["current"] for s in sessions].count(True), 1)
        self.assertEqual(sessions[0]["device"], "Test browser")
        self.assertNotIn("token", repr(sessions).lower())

    def test_revoking_another_session_works_and_foreign_sessions_look_missing(self):
        self.h.register("alice@example.com")
        mine, other = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        principal = self.h.principal(mine.session.token)
        self.h.services().auth.revoke_session(principal, other.session.session_id)
        self.assertEqual(self.code_of(self.h.principal, other.session.token).code, "SESSION_EXPIRED")
        bob, _ = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        error = self.code_of(self.h.services().auth.revoke_session, self.h.principal(self.h.login("bob@example.com", OTHER_PASSWORD).session.token), mine.session.session_id)
        self.assertEqual((error.code, error.status_code), ("NOT_FOUND", 404))


class PasswordChangeTests(Base):
    def test_change_rotates_the_session_and_signs_out_other_devices(self):
        self.h.register("alice@example.com")
        mine, other = self.h.login("alice@example.com"), self.h.login("alice@example.com")
        principal = self.h.principal(mine.session.token)
        issued = self.h.services().auth.change_password(principal, GOOD_PASSWORD, OTHER_PASSWORD)
        self.assertNotEqual(issued.token, mine.session.token)
        self.assertEqual(self.code_of(self.h.principal, mine.session.token).code, "SESSION_EXPIRED")   # old identifier is dead
        self.assertEqual(self.code_of(self.h.principal, other.session.token).code, "SESSION_EXPIRED")  # other devices signed out
        self.assertTrue(self.h.principal(issued.token))
        self.assertEqual(self.code_of(self.h.login, "alice@example.com", GOOD_PASSWORD).code, "INVALID_CREDENTIALS")
        self.assertTrue(self.h.login("alice@example.com", OTHER_PASSWORD).principal.user_id)
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Your PredictAI password was changed")
        self.assertEqual(len(self.events(Event.PASSWORD_CHANGED)), 1)

    def test_wrong_current_password_is_rejected_and_rate_limited(self):
        principal, _ = self.h.make_user("alice@example.com")
        for _ in range(5):
            self.assertEqual(self.code_of(self.h.services().auth.change_password, principal, "wrong-wrong-1", OTHER_PASSWORD).code, "INVALID_PASSWORD")
        self.assertEqual(self.code_of(self.h.services().auth.change_password, principal, GOOD_PASSWORD, OTHER_PASSWORD).code, "RATE_LIMITED")

    def test_new_password_must_meet_the_policy_and_differ(self):
        principal, _ = self.h.make_user("alice@example.com")
        weak = self.code_of(self.h.services().auth.change_password, principal, GOOD_PASSWORD, "short")
        self.assertEqual(weak.details[0]["field"], "new_password")
        same = self.code_of(self.h.services().auth.change_password, principal, GOOD_PASSWORD, GOOD_PASSWORD)
        self.assertEqual(same.details[0]["issue"], "unchanged")
        self.assertTrue(self.h.login("alice@example.com", GOOD_PASSWORD).principal.user_id)  # nothing changed

    def test_passwords_never_appear_in_audit_or_storage(self):
        principal, _ = self.h.make_user("alice@example.com")
        self.h.services().auth.change_password(principal, GOOD_PASSWORD, OTHER_PASSWORD)
        for secret in (GOOD_PASSWORD, OTHER_PASSWORD):
            self.assertNotIn(secret, self.h.dump())


class RecoveryTests(Base):
    def test_full_recovery_flow(self):
        self.h.register("alice@example.com")
        old = self.h.login("alice@example.com")
        self.h.services().auth.request_recovery("alice@example.com")
        token = self.h.last_token("alice@example.com")
        self.assertIn("/reset-password#token=", self.h.last_email().body)
        self.h.services().auth.complete_recovery(token, OTHER_PASSWORD)
        self.assertEqual(self.code_of(self.h.principal, old.session.token).code, "SESSION_EXPIRED")  # all sessions ended
        self.assertEqual(self.code_of(self.h.login, "alice@example.com", GOOD_PASSWORD).code, "INVALID_CREDENTIALS")
        self.assertTrue(self.h.login("alice@example.com", OTHER_PASSWORD).principal.user_id)
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Your PredictAI password was reset")

    def test_unknown_email_gets_the_same_outward_result_and_no_mail(self):
        self.h.register("alice@example.com")
        before = len(self.h.outbox.messages)
        self.assertIsNone(self.h.services().auth.request_recovery("nobody@example.com"))
        self.assertIsNone(self.h.services().auth.request_recovery("not-an-email"))
        self.assertEqual(len(self.h.outbox.messages), before)

    def test_token_is_single_use_expires_and_newer_requests_replace_older(self):
        self.h.register("alice@example.com")
        self.h.services().auth.request_recovery("alice@example.com")
        first = self.h.last_token()
        self.h.services().auth.request_recovery("alice@example.com")
        second = self.h.last_token()
        self.assertEqual(self.code_of(self.h.services().auth.complete_recovery, first, OTHER_PASSWORD).code, "INVALID_TOKEN")
        self.h.services().auth.complete_recovery(second, OTHER_PASSWORD)
        self.assertEqual(self.code_of(self.h.services().auth.complete_recovery, second, GOOD_PASSWORD).code, "INVALID_TOKEN")

        self.h.services().auth.request_recovery("alice@example.com")
        token = self.h.last_token()
        self.h.clock.advance(minutes=31)
        self.assertEqual(self.code_of(self.h.services().auth.complete_recovery, token, GOOD_PASSWORD).code, "INVALID_TOKEN")

    def test_new_password_must_meet_the_policy_and_the_token_survives_a_rejected_attempt(self):
        self.h.register("alice@example.com")
        self.h.services().auth.request_recovery("alice@example.com")
        token = self.h.last_token()
        self.assertEqual(self.code_of(self.h.services().auth.complete_recovery, token, "short").code, "VALIDATION_ERROR")
        self.h.services().auth.complete_recovery(token, OTHER_PASSWORD)

    def test_blocked_accounts_cannot_use_recovery_to_get_back_in(self):
        user, _ = self.h.make_user("alice@example.com")
        self.h.services().auth.request_recovery("alice@example.com")
        token = self.h.last_token()
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (user.user_id,))
        self.assertEqual(self.code_of(self.h.services().auth.complete_recovery, token, OTHER_PASSWORD).code, "INVALID_TOKEN")

    def test_recovery_verifies_a_pending_account_because_the_link_proves_mailbox_control(self):
        h = Harness(require_email_verification=True)
        self.addCleanup(h.close)
        h.register("alice@example.com")
        h.services().auth.request_recovery("alice@example.com")
        h.services().auth.complete_recovery(h.last_token(), OTHER_PASSWORD)
        self.assertEqual(h.rows("SELECT status FROM users")[0]["status"], "active")
        self.assertTrue(h.login("alice@example.com", OTHER_PASSWORD).principal.email_verified)

    def test_recovery_requests_are_rate_limited_per_email(self):
        self.h.register("alice@example.com")
        for _ in range(3):
            self.h.services().auth.request_recovery("alice@example.com")
        self.assertEqual(self.code_of(self.h.services().auth.request_recovery, "alice@example.com").code, "RATE_LIMITED")

    def test_reset_tokens_are_stored_hashed(self):
        self.h.register("alice@example.com")
        self.h.services().auth.request_recovery("alice@example.com")
        self.assertNotIn(self.h.last_token(), self.h.dump())


class ReauthTests(Base):
    def test_reauth_rotates_the_session_and_refreshes_freshness(self):
        _, token = self.h.make_user("alice@example.com")
        self.h.clock.advance(minutes=30)
        principal = self.h.principal(token)
        from app.modules.identity.authentication import ensure_fresh_auth

        self.assertEqual(self.code_of(ensure_fresh_auth, self.h.services().ctx, principal).code, "REAUTH_REQUIRED")
        issued = self.h.services().auth.reauthenticate(principal, GOOD_PASSWORD)
        self.assertNotEqual(issued.token, token)
        self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")
        ensure_fresh_auth(self.h.services().ctx, self.h.principal(issued.token))  # now fresh: no error

    def test_wrong_password_does_not_elevate(self):
        principal, _ = self.h.make_user("alice@example.com")
        self.assertEqual(self.code_of(self.h.services().auth.reauthenticate, principal, "wrong-wrong-1").code, "INVALID_PASSWORD")


if __name__ == "__main__":
    unittest.main()
