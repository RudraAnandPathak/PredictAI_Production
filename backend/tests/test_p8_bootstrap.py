"""Phase 8: owner bootstrap from the server environment."""
import io
import logging
import unittest
from dataclasses import replace

from app import cli
from app.core.errors import AppError
from app.modules.identity.bootstrap import bootstrap_owner_from_env, code_problem
from app.modules.identity.tokens import new_access_code
from support import GOOD_PASSWORD, Harness

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.code = new_access_code()

    def env(self, **kw):
        base = dict(owner_bootstrap_name="Root", owner_bootstrap_email="root@example.com", owner_bootstrap_password=GOOD_PASSWORD, owner_bootstrap_code=self.code)
        base.update(kw)
        self.h.settings = replace(self.h.settings, **base)
        return self.h.services().ctx

    def owners(self):
        return self.h.rows("SELECT user_id FROM user_roles WHERE role = 'owner'") if self.h.rows("SELECT name FROM sqlite_master WHERE name='user_roles'") else []

    def test_nothing_happens_without_environment_values(self):
        ctx = self.env(owner_bootstrap_email="", owner_bootstrap_password="")
        self.assertEqual(bootstrap_owner_from_env(ctx)["owner"], "skipped")
        self.assertEqual(ctx.repo.active_owner_ids(), [])

    def test_creates_the_owner_who_can_sign_in_and_is_idempotent(self):
        ctx = self.env()
        self.assertEqual(bootstrap_owner_from_env(ctx)["owner"], "created")
        self.assertEqual(len(ctx.repo.active_owner_ids()), 1)
        principal = self.h.login("root@example.com", GOOD_PASSWORD).principal
        self.assertTrue(principal.is_owner)
        again = bootstrap_owner_from_env(self.env())
        self.assertEqual(again["owner"], "skipped")
        self.assertEqual(len(ctx.repo.active_owner_ids()), 1)

    def test_a_different_email_or_password_never_creates_a_second_owner_or_resets_the_first(self):
        bootstrap_owner_from_env(self.env())
        bootstrap_owner_from_env(self.env(owner_bootstrap_email="evil@example.com", owner_bootstrap_password="another-long-password-1"))
        ctx = self.h.services().ctx
        self.assertEqual(len(ctx.repo.active_owner_ids()), 1)
        self.assertIsNone(ctx.repo.user_by_email("evil@example.com"))
        self.h.login("root@example.com", GOOD_PASSWORD)   # the original password still works

    def test_a_weak_activation_code_is_refused_and_nothing_is_created(self):
        for weak in ("letmein", "PAI-AAAAA", "special123"):
            self.assertIsNotNone(code_problem(weak))
        ctx = self.env(owner_bootstrap_code="letmein")
        with self.assertRaises(AppError):
            bootstrap_owner_from_env(ctx)
        self.assertEqual(ctx.repo.active_owner_ids(), [])

    def test_the_code_is_stored_hashed_and_works_once_for_a_user(self):
        bootstrap_owner_from_env(self.env())
        dump = self.h.dump()
        self.assertNotIn(self.code, dump)
        self.assertNotIn(GOOD_PASSWORD, dump)
        user, _ = self.h.make_user("user@example.com")
        self.assertEqual(self.h.services().codes.activate(user, self.code)["tier"], "special")
        other, _ = self.h.make_user("other@example.com")
        with self.assertRaises(AppError) as e:
            self.h.services().codes.activate(other, self.code)
        self.assertEqual(e.exception.code, "INVALID_CODE")

    def test_the_bootstrap_code_expires(self):
        from datetime import timedelta
        bootstrap_owner_from_env(self.env())
        self.h.clock.advance(days=31)
        user, _ = self.h.make_user("late@example.com")
        with self.assertRaises(AppError):
            self.h.services().codes.activate(user, self.code)

    def test_no_secret_in_logs_or_settings_repr(self):
        with self.assertLogs(level="DEBUG") as logs:
            logging.getLogger("t").info("start")
            bootstrap_owner_from_env(self.env())
        text = "\n".join(logs.output) + repr(self.h.settings)
        self.assertNotIn(GOOD_PASSWORD, text)
        self.assertNotIn(self.code, text)

    def test_the_audit_trail_records_the_environment_source_without_secrets(self):
        bootstrap_owner_from_env(self.env())
        dump = self.h.dump()
        self.assertIn("environment", dump)

    def test_cli_command(self):
        lines = []
        code = cli.main(["bootstrap-owner"], settings=self.env() and self.h.settings, stdin=io.StringIO(""), prompt=lambda _p: "", out=lines.append)
        self.assertEqual(code, 0)
        self.assertIn("Owner: created", "\n".join(lines))
        self.assertNotIn(GOOD_PASSWORD, "\n".join(lines))
        lines.clear()
        cli.main(["bootstrap-owner"], settings=self.h.settings, stdin=io.StringIO(""), prompt=lambda _p: "", out=lines.append)
        self.assertIn("already exists", "\n".join(lines))

    def test_settings_read_the_documented_variable_names(self):
        from app.core.config import load_settings
        s = load_settings({"PREDICTAI_ENV": "test", "OWNER_EMAIL": "a@b.io", "OWNER_PASSWORD": "x" * 14, "OWNER_NAME": "N", "OWNER_ACTIVATION_CODE": "c"})
        self.assertEqual((s.owner_bootstrap_email, s.owner_bootstrap_name), ("a@b.io", "N"))
        s2 = load_settings({"PREDICTAI_ENV": "test", "OWNER_EMAIL": "a@b.io", "PREDICTAI_OWNER_EMAIL": "p@b.io"})
        self.assertEqual(s2.owner_bootstrap_email, "p@b.io")
        self.assertNotIn("x" * 14, repr(s))

    def test_there_is_no_public_owner_registration_route(self):
        import app.main as m
        app = m.create_app() if hasattr(m, "create_app") else m.app
        for route in app.routes:
            path = getattr(route, "path", "")
            self.assertNotIn("bootstrap", path)
            self.assertNotRegex(path, r"owner.*(register|create|provision)")


if __name__ == "__main__":
    unittest.main()
