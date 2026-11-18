import base64
import hashlib
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from app.core.clock import ManualClock
from app.core.config import ConfigError, load_settings
from app.core.errors import AppError
from app.db import MigrationError, migrate, pending_versions
from app.db.migrator import MIGRATIONS_DIR, discover
from app.modules.identity.constants import Event
from app.modules.identity.container import run_housekeeping
from app.modules.identity.email import DisabledSender, EmailMessage, FileOutbox, MemoryOutbox, Notifier, SmtpSender
from app.modules.identity.http_security import client_ip, csrf_problem, device_label, truncate_ip
from app.modules.identity.oauth import providers_from_settings
from support import GOOD_PASSWORD, OTHER_PASSWORD, Harness

PROD = {"PREDICTAI_ENV": "production", "PREDICTAI_CORS_ALLOWED_ORIGINS": "https://app.example.com",
        "PREDICTAI_SECRET_KEY": "k" * 8 + "Zq7vN2xW9pLm4RtY6uHb3cEa5sDfG1jK", "PREDICTAI_FRONTEND_BASE_URL": "https://app.example.com",
        "PREDICTAI_SMTP_HOST": "smtp.example.com", "PREDICTAI_EMAIL_FROM": "PredictAI <hello@example.com>"}


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.token = (200, {"access_token": "at-1"})
        self.google = (200, {"sub": "g-123", "email_verified": True})
        self.github = (200, {"id": 4242})
        self.emails = (200, [{"email": "x@example.com", "primary": True, "verified": True}])

    def post_form(self, url, data, headers=None):
        self.calls.append(("POST", url, dict(data)))
        return self.token

    def get_json(self, url, headers=None):
        self.calls.append(("GET", url, dict(headers or {})))
        if "emails" in url:
            return self.emails
        return self.github if "github" in url else self.google


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(oauth_google_client_id="gid", oauth_google_client_secret="gsecret", oauth_github_client_id="hid", oauth_github_client_secret="hsecret")
        self.addCleanup(self.h.close)
        self.http = FakeHttp()
        self.providers = providers_from_settings(self.h.settings)
        self.user, self.token = self.h.make_user("alice@example.com")

    def svc(self, token=None):
        return self.h.services(providers=self.providers, http=self.http)

    def principal(self, token=None):
        return self.h.principal(token or self.token)

    def start(self, provider="google", token=None):
        url = self.svc().oauth.start_link(self.principal(token), provider)
        return url, parse_qs(urlsplit(url).query)

    def code_of(self, fn, *a, **k):
        with self.assertRaises(AppError) as ctx:
            fn(*a, **k)
        return ctx.exception

    def test_providers_without_credentials_report_not_configured_and_refuse(self):
        h = Harness()
        self.addCleanup(h.close)
        user, token = h.make_user("bob@example.com")
        s = h.services()
        self.assertEqual({p["id"]: p["configured"] for p in s.oauth.list_providers(h.principal(token))}, {"google": False, "github": False})
        self.assertEqual(self.code_of(s.oauth.start_link, h.principal(token), "google").code, "PROVIDER_NOT_CONFIGURED")
        self.assertEqual(self.code_of(s.oauth.start_link, h.principal(token), "myspace").code, "NOT_FOUND")

    def test_authorization_url_carries_state_pkce_and_the_callback(self):
        url, q = self.start()
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertEqual((q["response_type"], q["client_id"], q["code_challenge_method"]), (["code"], ["gid"], ["S256"]))
        self.assertEqual(q["redirect_uri"], ["http://localhost:5173/api/v1/auth/oauth/google/callback"])
        self.assertGreaterEqual(len(q["state"][0]), 32)
        self.assertNotIn("gsecret", url)

    def test_successful_link_uses_the_pkce_verifier_and_stores_only_the_subject(self):
        url, q = self.start()
        self.svc().oauth.complete_link(self.principal(), "google", "auth-code-1", q["state"][0])
        post = [c for c in self.http.calls if c[0] == "POST"][0]
        self.assertEqual(post[2]["code"], "auth-code-1")
        self.assertEqual(post[2]["client_secret"], "gsecret")
        expected = base64.urlsafe_b64encode(hashlib.sha256(post[2]["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(expected, q["code_challenge"][0])
        identity = self.h.rows("SELECT * FROM identities WHERE provider = 'google'")[0]
        self.assertEqual((identity["provider_subject"], identity["user_id"], identity["verified"], identity["password_hash"]),
                         ("g-123", self.user.user_id, 1, None))
        self.assertNotIn("at-1", self.h.dump())                                  # provider tokens are never stored
        self.assertEqual([e["event_type"] for e in self.h.rows("SELECT event_type FROM audit_events WHERE event_type LIKE 'oauth.%'")], [Event.OAUTH_LINKED])
        self.assertEqual(self.h.last_email("alice@example.com").subject, "A sign-in provider was connected")
        linked = {p["id"]: p["linked"] for p in self.svc().oauth.list_providers(self.principal())}
        self.assertEqual(linked, {"google": True, "github": False})

    def test_github_uses_the_verified_primary_email_flag(self):
        _, q = self.start("github")
        self.svc().oauth.complete_link(self.principal(), "github", "c", q["state"][0])
        self.assertEqual(self.h.rows("SELECT provider_subject, verified FROM identities WHERE provider = 'github'")[0], {"provider_subject": "4242", "verified": 1})

    def test_state_is_single_use_bound_to_the_user_and_the_provider(self):
        _, q = self.start()
        state = q["state"][0]
        self.svc().oauth.complete_link(self.principal(), "google", "c", state)
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", state).code, "INVALID_TOKEN")     # reuse
        bob, bob_token = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        _, q2 = self.start(token=self.token, provider="github")
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(bob_token), "github", "c", q2["state"][0]).code, "INVALID_TOKEN")  # other user
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", q2["state"][0]).code, "INVALID_TOKEN")        # wrong provider
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", "forged-state").code, "INVALID_TOKEN")
        _, q3 = self.start()
        self.h.clock.advance(minutes=11)
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", q3["state"][0]).code, "INVALID_TOKEN")  # expired

    def test_provider_failures_become_a_generic_error_and_are_audited(self):
        _, q = self.start()
        self.http.token = (400, {"error": "invalid_grant"})
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", q["state"][0]).code, "OAUTH_FAILED")
        _, q = self.start()
        self.http.token, self.http.google = (200, {"access_token": "x"}), (500, {})
        self.assertEqual(self.code_of(self.svc().oauth.complete_link, self.principal(), "google", "c", q["state"][0]).code, "OAUTH_FAILED")
        self.assertEqual(len(self.h.rows("SELECT * FROM audit_events WHERE event_type = ?", (Event.OAUTH_FAILED,))), 2)
        self.assertEqual(self.h.rows("SELECT * FROM identities WHERE provider = 'google'"), [])

    def test_an_external_account_cannot_be_attached_to_two_predictai_accounts(self):
        _, q = self.start()
        self.svc().oauth.complete_link(self.principal(), "google", "c", q["state"][0])
        bob, bob_token = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        _, q2 = self.start(token=bob_token)
        error = self.code_of(self.svc().oauth.complete_link, self.principal(bob_token), "google", "c", q2["state"][0])
        self.assertEqual((error.code, error.status_code), ("OAUTH_ALREADY_LINKED", 409))
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM identities WHERE provider = 'google'")[0]["n"], 1)

    def test_relinking_the_same_account_is_idempotent(self):
        for _ in range(2):
            _, q = self.start()
            self.svc().oauth.complete_link(self.principal(), "google", "c", q["state"][0])
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM identities WHERE provider = 'google'")[0]["n"], 1)

    def test_unlinking_needs_the_password_and_leaves_password_sign_in_intact(self):
        _, q = self.start()
        self.svc().oauth.complete_link(self.principal(), "google", "c", q["state"][0])
        self.assertEqual(self.code_of(self.svc().oauth.unlink, self.principal(), "google", "wrong-wrong-1").code, "INVALID_PASSWORD")
        self.svc().oauth.unlink(self.principal(), "google", GOOD_PASSWORD)
        self.assertEqual(self.h.rows("SELECT * FROM identities WHERE provider = 'google'"), [])
        self.assertEqual(self.code_of(self.svc().oauth.unlink, self.principal(), "google", GOOD_PASSWORD).code, "NOT_FOUND")
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)
        self.assertEqual(self.h.last_email("alice@example.com").subject, "A sign-in provider was disconnected")


class HttpSecurityTests(unittest.TestCase):
    ALLOWED = ("http://localhost:5173",)

    def test_safe_methods_are_never_blocked(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertIsNone(csrf_problem(method=method, origin="https://evil.example", allowed_origins=self.ALLOWED, session_csrf="t", provided_token=None))

    def test_foreign_and_null_origins_are_rejected_on_unsafe_methods(self):
        for origin in ("https://evil.example", "null", "http://localhost:5174"):
            self.assertEqual(csrf_problem(method="POST", origin=origin, allowed_origins=self.ALLOWED, session_csrf=None, provided_token=None), "origin")

    def test_session_requests_need_the_matching_token(self):
        kw = dict(method="POST", origin="http://localhost:5173", allowed_origins=self.ALLOWED, session_csrf="expected")
        self.assertEqual(csrf_problem(provided_token=None, **kw), "token")
        self.assertEqual(csrf_problem(provided_token="wrong", **kw), "token")
        self.assertIsNone(csrf_problem(provided_token="expected", **kw))
        self.assertEqual(csrf_problem(method="DELETE", origin=None, allowed_origins=self.ALLOWED, session_csrf="expected", provided_token=None), "token")

    def test_anonymous_requests_without_origin_are_allowed_but_foreign_origin_is_not(self):
        self.assertIsNone(csrf_problem(method="POST", origin=None, allowed_origins=self.ALLOWED, session_csrf=None, provided_token=None))
        self.assertIsNone(csrf_problem(method="POST", origin="http://localhost:5173", allowed_origins=self.ALLOWED, session_csrf=None, provided_token=None))

    def test_forwarded_for_is_ignored_unless_proxies_are_trusted(self):
        self.assertEqual(client_ip("10.0.0.1", "6.6.6.6, 7.7.7.7", 0), "10.0.0.1")
        self.assertEqual(client_ip("10.0.0.1", "6.6.6.6, 7.7.7.7", 1), "7.7.7.7")
        self.assertEqual(client_ip("10.0.0.1", "7.7.7.7", 2), "10.0.0.1")
        self.assertEqual(client_ip(None, None, 0), "unknown")

    def test_ips_are_truncated_for_audit(self):
        self.assertEqual(truncate_ip("203.0.113.77"), "203.0.113.0")
        self.assertEqual(truncate_ip("2001:db8:abcd:1234::1"), "2001:db8:abcd::")
        self.assertIsNone(truncate_ip("not-an-ip"))
        self.assertIsNone(truncate_ip(None))

    def test_device_labels_are_coarse(self):
        self.assertEqual(device_label("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537 Chrome/120 Safari/537"), "Chrome on macOS")
        self.assertEqual(device_label("Mozilla/5.0 (Windows NT 10.0) Firefox/121.0"), "Firefox on Windows")
        self.assertEqual(device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17) AppleWebKit Safari/604"), "Safari on iOS")
        self.assertEqual(device_label(None), "Unknown device")
        self.assertNotIn("120", device_label("Mozilla/5.0 (Macintosh) Chrome/120.0.6099.71"))   # no version fingerprint


class EmailTests(unittest.TestCase):
    def test_file_outbox_writes_private_readable_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            FileOutbox(tmp).send(EmailMessage("a@example.com", "Hello", "Body text"))
            files = list(Path(tmp).glob("*.eml"))
            self.assertEqual(len(files), 1)
            self.assertIn("Body text", files[0].read_text())
            self.assertEqual(oct(files[0].stat().st_mode & 0o777), "0o600")

    def test_security_notices_respect_the_configuration_and_ignore_unknown_kinds(self):
        for notify, expected in ((True, 1), (False, 0)):
            out = MemoryOutbox()
            Notifier(replace(load_settings({}), notify_security_events=notify), out).security_notice("a@example.com", "password_changed")
            self.assertEqual(len(out.messages), expected)
        out = MemoryOutbox()
        Notifier(load_settings({}), out).security_notice("a@example.com", "does_not_exist")
        self.assertEqual(out.messages, [])

    def test_essential_mail_is_sent_even_when_notices_are_off(self):
        out = MemoryOutbox()
        n = Notifier(replace(load_settings({}), notify_security_events=False), out)
        n.verification("a@example.com", "tok123")
        n.password_reset("a@example.com", "tok456", 30)
        self.assertEqual(len(out.messages), 2)
        self.assertIn("#token=tok123", out.messages[0].body)

    def test_a_failing_sender_never_breaks_the_request(self):
        class Broken:
            def send(self, message):
                raise OSError("smtp down")
        Notifier(load_settings({}), Broken()).password_reset("a@example.com", "t", 30)  # must not raise

    def test_smtp_sender_uses_starttls_login_and_send(self):
        s = replace(load_settings({}), email_backend="smtp", smtp_host="smtp.example.com", smtp_username="user", smtp_password="pw", smtp_security="starttls")
        with mock.patch("app.modules.identity.email.smtplib.SMTP") as smtp:
            SmtpSender(s).send(EmailMessage("a@example.com", "Hi", "Body"))
        client = smtp.return_value  # smtplib's context manager returns the same object
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("user", "pw")
        client.send_message.assert_called_once()

    def test_the_disabled_backend_sends_nothing(self):
        DisabledSender().send(EmailMessage("a@example.com", "Hi", "Body"))


class ProductionConfigTests(unittest.TestCase):
    def test_a_complete_production_configuration_is_accepted_and_locked_down(self):
        s = load_settings(PROD)
        self.assertTrue(s.cookie_secure)
        self.assertEqual(s.session_cookie_name, "__Host-pai_session")
        self.assertTrue(s.require_email_verification)
        self.assertFalse(s.auto_migrate)
        self.assertFalse(s.docs_enabled)
        self.assertEqual(s.email_backend, "smtp")

    def test_each_required_secret_or_url_is_enforced(self):
        for missing, needle in (("PREDICTAI_SECRET_KEY", "SECRET_KEY"), ("PREDICTAI_FRONTEND_BASE_URL", "FRONTEND_BASE_URL"), ("PREDICTAI_SMTP_HOST", "Email verification")):
            env = {k: v for k, v in PROD.items() if k != missing}
            with self.subTest(missing=missing), self.assertRaises(ConfigError) as ctx:
                load_settings(env)
            self.assertIn(needle, str(ctx.exception))

    def test_weak_or_placeholder_secrets_are_rejected(self):
        for secret in ("short", "change-me-change-me-change-me-change-me", "x" * 31):
            with self.subTest(secret=secret), self.assertRaises(ConfigError):
                load_settings({**PROD, "PREDICTAI_SECRET_KEY": secret})

    def test_insecure_hosted_settings_are_refused(self):
        for key, value in (("PREDICTAI_COOKIE_SECURE", "false"), ("PREDICTAI_EMAIL_BACKEND", "file"), ("PREDICTAI_SMTP_SECURITY", "none"),
                           ("PREDICTAI_FRONTEND_BASE_URL", "http://app.example.com")):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_settings({**PROD, key: value})

    def test_verification_can_be_switched_off_explicitly_in_staging_only_by_choice(self):
        env = {k: v for k, v in PROD.items() if k != "PREDICTAI_SMTP_HOST"}
        s = load_settings({**env, "PREDICTAI_REQUIRE_EMAIL_VERIFICATION": "false"})
        self.assertEqual((s.require_email_verification, s.email_backend), (False, "disabled"))

    def test_oauth_credentials_must_be_complete(self):
        with self.assertRaises(ConfigError):
            load_settings({"PREDICTAI_OAUTH_GOOGLE_CLIENT_ID": "only-id"})
        s = load_settings({"PREDICTAI_OAUTH_GOOGLE_CLIENT_ID": "id", "PREDICTAI_OAUTH_GOOGLE_CLIENT_SECRET": "secret"})
        self.assertEqual(s.oauth_google_client_id, "id")
        self.assertNotIn("secret", repr(s))                         # secrets stay out of any log of the settings object

    def test_numeric_policies_are_range_checked(self):
        for key, value in (("PREDICTAI_SESSION_IDLE_MINUTES", "1"), ("PREDICTAI_AUDIT_RETENTION_DAYS", "3"), ("PREDICTAI_LOGIN_MAX_FAILURES", "1"), ("PREDICTAI_MAX_OWNERS", "0")):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_settings({key: value})
        self.assertEqual(load_settings({"PREDICTAI_AUDIT_RETENTION_DAYS": "90"}).audit_retention_days, 90)

    def test_explicit_environment_loads_do_not_write_files(self):
        root = Path(__file__).resolve().parents[2]
        before = set(os.listdir(root))
        load_settings({})
        self.assertEqual(set(os.listdir(root)), before)


class MigrationTests(unittest.TestCase):
    def test_schema_applies_cleanly_twice_and_has_no_plaintext_password_column(self):
        h = Harness()
        self.addCleanup(h.close)
        self.assertEqual(migrate(h.conn), [])
        self.assertEqual(pending_versions(h.conn), [])
        columns = {r["name"] for r in h.conn.execute("PRAGMA table_info(identities)")}
        self.assertIn("password_hash", columns)
        self.assertFalse({"password", "plaintext"} & columns)
        self.assertEqual(h.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_editing_an_applied_migration_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            shutil.copy(MIGRATIONS_DIR / "0001_identity.sql", directory)
            db = Harness()
            self.addCleanup(db.close)
            import sqlite3
            conn = sqlite3.connect(":memory:", isolation_level=None)
            migrate(conn, directory)
            (directory / "0001_identity.sql").write_text((directory / "0001_identity.sql").read_text() + "\n-- sneaky edit\n")
            with self.assertRaises(MigrationError):
                migrate(conn, directory)

    def test_a_failing_migration_rolls_back_completely(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "0001_ok.sql").write_text("CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);\nINSERT INTO nope VALUES (1);\n")
            conn = sqlite3.connect(":memory:", isolation_level=None)
            with self.assertRaises(sqlite3.Error):
                migrate(conn, directory)
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE name IN ('a','b')").fetchall(), [])
            self.assertEqual(pending_versions(conn, directory), [1])

    def test_version_numbers_must_be_consecutive(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "0001_a.sql").write_text("SELECT 1;")
            Path(tmp, "0003_c.sql").write_text("SELECT 1;")
            with self.assertRaises(MigrationError):
                discover(Path(tmp))

    def test_ensure_ready_applies_or_demands_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            from app.db import Database
            Database(f"{tmp}/a.db").ensure_ready(auto_migrate=True)
            with self.assertRaises(MigrationError):
                Database(f"{tmp}/b.db").ensure_ready(auto_migrate=False)


class HousekeepingTests(unittest.TestCase):
    def test_retention_removes_old_records_and_keeps_recent_ones(self):
        h = Harness(audit_retention_days=30)
        self.addCleanup(h.close)
        h.make_user("alice@example.com")                                  # creates audit, session and rate-limit rows
        h.clock.advance(days=20)
        h.login("alice@example.com")
        old_audit = h.rows("SELECT COUNT(*) AS n FROM audit_events")[0]["n"]
        h.clock.advance(days=15)                                         # first batch is now 35 days old, second 15
        removed = run_housekeeping(h.services())
        self.assertGreater(removed["audit_events"], 0)
        remaining = h.rows("SELECT occurred_at FROM audit_events")
        self.assertTrue(remaining)                                       # the newer events (and the purge note) survive
        self.assertLess(len(remaining), old_audit + 2)
        self.assertTrue(any(e["event_type"] == Event.AUDIT_PURGED for e in h.rows("SELECT event_type FROM audit_events")))

    def test_expired_sessions_and_tokens_are_purged_after_their_grace_period(self):
        h = Harness(require_email_verification=True)
        self.addCleanup(h.close)
        h.register("alice@example.com")
        h.clock.advance(days=40)
        run_housekeeping(h.services())
        self.assertEqual(h.rows("SELECT COUNT(*) AS n FROM auth_tokens")[0]["n"], 0)

    def test_rate_limit_counters_do_not_accumulate_forever(self):
        h = Harness()
        self.addCleanup(h.close)
        h.register("alice@example.com")
        self.assertGreater(h.rows("SELECT COUNT(*) AS n FROM rate_limit_events")[0]["n"], 0)
        h.clock.advance(days=3)
        run_housekeeping(h.services())
        self.assertEqual(h.rows("SELECT COUNT(*) AS n FROM rate_limit_events")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
