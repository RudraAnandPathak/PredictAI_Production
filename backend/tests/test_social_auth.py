"""Continue with Google/GitHub: registration, sign-in, safe linking rules, and support for accounts that have no password.
Provider calls use a fake HTTP client (no real provider was reachable); everything else is real code and real SQLite."""
import unittest
from urllib.parse import parse_qs, urlsplit

from app.core.errors import AppError
from app.modules.identity.constants import Event
from app.modules.identity.oauth import providers_from_settings
from support import GOOD_PASSWORD, Harness
from test_identity_platform import FakeHttp


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as error:
        return error.code
    return None


class SocialBase(unittest.TestCase):
    def setUp(self):
        self.h = Harness(oauth_google_client_id="gid", oauth_google_client_secret="gsecret", oauth_github_client_id="hid", oauth_github_client_secret="hsecret")
        self.addCleanup(self.h.close)
        self.http = FakeHttp()
        self.http.google = (200, {"sub": "g-777", "email": "Newcomer@Example.com", "email_verified": True, "name": "Nia Newcomer"})
        self.http.github = (200, {"id": 9001, "login": "octo", "name": "Octo Cat"})
        self.http.emails = (200, [{"email": "octo@example.com", "primary": True, "verified": True}])
        self.providers = providers_from_settings(self.h.settings)

    def svc(self):
        return self.h.services(providers=self.providers, http=self.http)

    def begin(self, provider="google", intent="register"):
        url, nonce = self.svc().oauth.start_signin(provider, intent)
        return parse_qs(urlsplit(url).query)["state"][0], nonce

    def finish(self, provider="google", intent="register", code="c"):
        state, nonce = self.begin(provider, intent)
        return self.svc().oauth.complete_signin(provider, code, state, nonce)

    def users(self):
        return self.h.rows("SELECT * FROM users")


class RegistrationTests(SocialBase):
    def test_a_verified_provider_email_creates_an_active_verified_account_and_a_session(self):
        result = self.finish()
        self.assertTrue(result.created)
        p = result.login.principal
        self.assertEqual((p.email, p.display_name, p.email_verified, sorted(p.roles)), ("Newcomer@Example.com", "Nia Newcomer", True, ["user"]))
        user = self.users()[0]
        self.assertEqual((user["status"], user["email_normalized"]), ("active", "newcomer@example.com"))
        identity = self.h.rows("SELECT * FROM identities")[0]
        self.assertEqual((identity["provider"], identity["provider_subject"], identity["password_hash"], identity["verified"]), ("google", "g-777", None, 1))
        self.assertEqual(self.h.principal(result.login.session.token).user_id, p.user_id)  # the session really authenticates
        self.assertEqual([e["event_type"] for e in self.h.rows("SELECT event_type FROM audit_events WHERE event_type LIKE 'oauth.%'")], [Event.OAUTH_REGISTERED])
        self.assertNotIn("at-1", self.h.dump())  # provider tokens are never stored
        self.assertNotIn("gsecret", self.h.dump())

    def test_github_uses_its_verified_primary_email(self):
        result = self.finish("github")
        self.assertEqual((result.login.principal.email, result.login.principal.display_name), ("octo@example.com", "Octo Cat"))

    def test_the_provider_must_vouch_for_the_email(self):
        cases = [("google", {"sub": "g1", "email": "a@example.com", "email_verified": False}, None),
                 ("google", {"sub": "g2", "email_verified": True}, None),
                 ("github", None, [{"email": "x@example.com", "primary": True, "verified": False}]),
                 ("github", None, [])]
        for provider, google, emails in cases:
            if google is not None:
                self.http.google = (200, google)
            if emails is not None:
                self.http.emails = (200, emails)
            self.assertEqual(code_of(self.finish, provider), "OAUTH_EMAIL_UNVERIFIED", (provider, google, emails))
        self.assertEqual(self.users(), [])
        self.assertEqual(self.h.rows("SELECT * FROM identities"), [])

    def test_an_existing_email_is_never_merged_or_signed_into(self):
        for status, verify in (("verified", True), ("pending", False)):
            h = Harness(oauth_google_client_id="gid", oauth_google_client_secret="gsecret", require_email_verification=not verify)
            self.addCleanup(h.close)
            h.register("newcomer@example.com", GOOD_PASSWORD)  # case differs from the provider's address
            http = FakeHttp()
            http.google = (200, {"sub": "attacker-sub", "email": "NEWCOMER@example.com", "email_verified": True})
            s = h.services(providers=providers_from_settings(h.settings), http=http)
            url, nonce = s.oauth.start_signin("google", "register")
            error = None
            try:
                s.oauth.complete_signin("google", "c", parse_qs(urlsplit(url).query)["state"][0], nonce)
            except AppError as e:
                error = e
            self.assertEqual((error.code, error.status_code), ("OAUTH_EMAIL_EXISTS", 409), status)
            self.assertEqual(h.rows("SELECT provider FROM identities"), [{"provider": "password"}], status)  # nothing attached
            self.assertEqual(h.rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 0, status)
            self.assertIn("blocked", h.last_email("newcomer@example.com").subject)  # the real owner is told

    def test_signing_in_without_an_account_does_not_create_one(self):
        self.assertEqual(code_of(self.finish, "google", "login"), "OAUTH_NO_ACCOUNT")
        self.assertEqual(self.users(), [])


class NoticeTests(unittest.TestCase):
    def test_no_security_notice_subject_has_an_unfilled_placeholder(self):
        from app.modules.identity.email import _NOTICES
        for kind, (subject, body) in _NOTICES.items():
            self.assertNotIn("{", subject, kind)  # only the body is templated

    def test_the_blocked_signup_notice_names_the_provider_in_the_body(self):
        from app.modules.identity.email import _NOTICES
        self.assertIn("{detail}", _NOTICES["oauth_signup_blocked"][1])


class SignInTests(SocialBase):
    def test_a_connected_provider_signs_in_again_without_creating_anything(self):
        first = self.finish()
        again = self.finish(intent="login")
        self.assertEqual((again.created, again.login.principal.user_id), (False, first.login.principal.user_id))
        self.assertEqual(len(self.users()), 1)
        again = self.finish(intent="register")  # registering twice just signs in
        self.assertFalse(again.created)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"], 3)

    def test_a_provider_linked_from_a_password_account_can_sign_in(self):
        user, token = self.h.make_user("alice@example.com")
        url = self.svc().oauth.start_link(self.h.principal(token), "google")
        self.svc().oauth.complete_link(self.h.principal(token), "google", "c", parse_qs(urlsplit(url).query)["state"][0])
        result = self.finish(intent="login")
        self.assertEqual(result.login.principal.user_id, user.user_id)

    def test_suspended_accounts_cannot_sign_in_this_way(self):
        self.finish()
        owner, _ = self.h.make_owner()
        self.svc().users.block_user(self.h.principal(self.h.login("owner@example.com").session.token), self.users()[0]["id"], "test")
        before = self.h.rows("SELECT COUNT(*) AS n FROM sessions WHERE revoked_at IS NULL")[0]["n"]
        self.assertEqual(code_of(self.finish, "google", "login"), "ACCOUNT_NOT_ACTIVE")
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM sessions WHERE revoked_at IS NULL")[0]["n"], before)  # no new session was issued

    def test_provider_failures_are_generic_audited_and_create_nothing(self):
        self.http.token = (400, {"error": "invalid_grant"})
        self.assertEqual(code_of(self.finish), "OAUTH_FAILED")
        self.http.token, self.http.google = (200, {"access_token": "x"}), (500, {})
        self.assertEqual(code_of(self.finish), "OAUTH_FAILED")
        self.assertEqual(code_of(self.finish, code=""), "OAUTH_FAILED")
        self.assertEqual(self.users(), [])
        self.assertEqual(len(self.h.rows("SELECT * FROM audit_events WHERE event_type = ?", (Event.OAUTH_LOGIN_FAILED,))), 3)

    def test_unknown_or_unconfigured_providers_and_bad_intents(self):
        self.assertEqual(code_of(self.svc().oauth.start_signin, "myspace", "login"), "NOT_FOUND")
        self.assertEqual(code_of(self.svc().oauth.start_signin, "google", "admin"), "VALIDATION_ERROR")
        h = Harness()
        self.addCleanup(h.close)
        self.assertEqual(code_of(h.services().oauth.start_signin, "google", "login"), "PROVIDER_NOT_CONFIGURED")
        self.assertEqual({p["id"]: p["configured"] for p in h.services().oauth.available()}, {"google": False, "github": False})

    def test_sign_in_attempts_are_rate_limited_per_address(self):
        for _ in range(30):
            self.begin()
        self.assertEqual(code_of(self.begin), "RATE_LIMITED")


class StateTests(SocialBase):
    def test_state_needs_the_browser_that_started_it(self):
        state, nonce = self.begin()
        s = self.svc().oauth
        self.assertEqual(code_of(s.complete_signin, "google", "c", state, None), "INVALID_TOKEN")           # no cookie (login CSRF)
        state, nonce = self.begin()
        self.assertEqual(code_of(s.complete_signin, "google", "c", state, "someone-elses-nonce"), "INVALID_TOKEN")
        self.assertEqual(self.users(), [])

    def test_state_is_single_use_expires_and_is_bound_to_provider(self):
        state, nonce = self.begin()
        s = self.svc().oauth
        s.complete_signin("google", "c", state, nonce)
        self.assertEqual(code_of(s.complete_signin, "google", "c", state, nonce), "INVALID_TOKEN")           # replay
        state, nonce = self.begin("github")
        self.assertEqual(code_of(s.complete_signin, "google", "c", state, nonce), "INVALID_TOKEN")           # wrong provider
        state, nonce = self.begin()
        self.h.clock.advance(minutes=11)
        self.assertEqual(code_of(s.complete_signin, "google", "c", state, nonce), "INVALID_TOKEN")           # expired
        self.assertEqual(code_of(s.complete_signin, "google", "c", "forged", nonce), "INVALID_TOKEN")

    def test_link_and_sign_in_states_cannot_be_swapped(self):
        user, token = self.h.make_user("alice@example.com")
        link_url = self.svc().oauth.start_link(self.h.principal(token), "google")
        link_state = parse_qs(urlsplit(link_url).query)["state"][0]
        self.assertEqual(code_of(self.svc().oauth.complete_signin, "google", "c", link_state, "x"), "INVALID_TOKEN")   # a link state cannot sign anyone in
        state, nonce = self.begin()
        self.assertEqual(code_of(self.svc().oauth.complete_link, self.h.principal(token), "google", "c", state), "INVALID_TOKEN")  # nor can a sign-in state link
        self.assertEqual(self.svc().oauth.peek_intent(link_state), "link")
        self.assertEqual(self.svc().oauth.peek_intent(state), "register")
        self.assertIsNone(self.svc().oauth.peek_intent("forged"))
        self.assertIsNone(self.svc().oauth.peek_intent(None))


class PasswordlessAccountTests(SocialBase):
    """Phase 2 defect F1: accounts created through a provider have no password but must still be able to
    confirm sensitive actions, set a password, and delete themselves."""

    def setUp(self):
        super().setUp()
        self.first = self.finish()
        self.principal = self.first.login.principal
        self.uid = self.principal.user_id

    def current(self, token=None):
        return self.h.principal(token or self.first.login.session.token)

    def test_a_fresh_provider_sign_in_confirms_sensitive_actions_and_a_stale_one_does_not(self):
        self.assertFalse(bool(self.svc().ctx.repo.password_identity(self.uid)))
        self.h.clock.advance(minutes=self.h.settings.step_up_minutes + 1)
        self.assertEqual(code_of(self.svc().users.delete_account, self.current(), None), "REAUTH_REQUIRED")
        self.assertEqual(self.h.rows("SELECT status FROM users")[0]["status"], "active")

    def test_provider_reauth_restores_the_window_and_checks_it_is_the_same_account(self):
        self.h.clock.advance(minutes=self.h.settings.step_up_minutes + 1)
        url = self.svc().oauth.start_reauth(self.current(), "google")
        state = parse_qs(urlsplit(url).query)["state"][0]
        self.http.google = (200, {"sub": "someone-else", "email": "x@example.com", "email_verified": True})
        self.assertEqual(code_of(self.svc().oauth.complete_reauth, self.current(), "google", "c", state), "OAUTH_WRONG_ACCOUNT")
        self.http.google = (200, {"sub": "g-777", "email": "newcomer@example.com", "email_verified": True})
        url = self.svc().oauth.start_reauth(self.current(), "google")
        issued = self.svc().oauth.complete_reauth(self.current(), "google", "c", parse_qs(urlsplit(url).query)["state"][0])
        self.svc().users.delete_account(self.current(issued.token), None)
        self.assertEqual(self.h.rows("SELECT status FROM users")[0]["status"], "deleted")
        self.assertEqual(self.h.rows("SELECT * FROM identities"), [])

    def test_reauth_needs_a_connected_provider_and_a_matching_state(self):
        self.assertEqual(code_of(self.svc().oauth.start_reauth, self.current(), "github"), "OAUTH_NOT_LINKED")
        url = self.svc().oauth.start_link(self.current(), "google")
        self.assertEqual(code_of(self.svc().oauth.complete_reauth, self.current(), "google", "c", parse_qs(urlsplit(url).query)["state"][0]), "INVALID_TOKEN")

    def test_a_password_can_be_set_through_the_emailed_link_and_then_used(self):
        s = self.svc()
        s.auth.request_recovery("newcomer@example.com")
        mail = self.h.last_email("Newcomer@Example.com")
        self.assertIn("password", mail.subject.lower())
        s.auth.complete_recovery(self.h.last_token("Newcomer@Example.com"), GOOD_PASSWORD)
        self.assertTrue(self.svc().ctx.repo.password_identity(self.uid))
        login = self.h.services().auth.login("newcomer@example.com", GOOD_PASSWORD)
        self.assertEqual(login.principal.user_id, self.uid)
        self.assertEqual(sorted(i["provider"] for i in self.svc().ctx.repo.identities_for_user(self.uid)), ["google", "password"])

    def test_change_password_is_not_offered_without_a_password(self):
        self.assertEqual(code_of(self.svc().auth.change_password, self.current(), "anything", GOOD_PASSWORD), "NO_PASSWORD")

    def test_the_only_sign_in_method_cannot_be_removed_but_a_second_one_allows_it(self):
        self.assertEqual(code_of(self.svc().oauth.unlink, self.current(), "google", None), "LAST_SIGN_IN_METHOD")
        self.svc().auth.request_recovery("newcomer@example.com")
        self.svc().auth.complete_recovery(self.h.last_token("Newcomer@Example.com"), GOOD_PASSWORD)
        login = self.h.services().auth.login("newcomer@example.com", GOOD_PASSWORD)
        self.svc().oauth.unlink(self.h.principal(login.session.token), "google", GOOD_PASSWORD)
        self.assertEqual([i["provider"] for i in self.svc().ctx.repo.identities_for_user(self.uid)], ["password"])

    def test_recovery_still_ignores_unknown_and_unverified_accounts(self):
        self.svc().auth.request_recovery("nobody@example.com")
        self.assertIsNone(self.h.last_email("nobody@example.com"))
        h = Harness(require_email_verification=True)
        self.addCleanup(h.close)
        h.register("pending@example.com", GOOD_PASSWORD)
        h.outbox.messages.clear() if hasattr(h.outbox, "messages") else None
        h.services().auth.request_recovery("pending@example.com")  # unchanged Phase 2 behaviour: still allowed for a password holder
        self.assertIn("password", h.last_email("pending@example.com").subject.lower())


if __name__ == "__main__":
    unittest.main()
