import io
import tempfile
import unittest
from dataclasses import replace

from app import cli
from app.core.config import load_settings
from support import GOOD_PASSWORD, OTHER_PASSWORD, Harness


class CliTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def run_cli(self, argv, password=None, prompts=None, settings=None):
        lines = []
        stdin = io.StringIO((password + "\n") if password is not None else "")
        answers = iter(prompts or [])
        code = cli.main(argv, settings=settings or self.h.settings, stdin=stdin, prompt=lambda _p: next(answers), out=lines.append)
        return code, "\n".join(lines)

    def test_migrate_applies_then_reports_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(self.h.settings, database_path=f"{tmp}/fresh.db")
            self.assertIn("Applied migrations: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]", self.run_cli(["migrate"], settings=settings)[1])
            self.assertIn("up to date", self.run_cli(["migrate"], settings=settings)[1])

    def test_other_commands_refuse_to_run_on_an_unmigrated_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = replace(self.h.settings, database_path=f"{tmp}/fresh.db")
            code, text = self.run_cli(["list-owners"], settings=settings)
            self.assertEqual(code, 1)
            self.assertIn("migrate", text)

    def test_provisioning_an_owner_with_a_hidden_prompt_and_never_echoing_the_password(self):
        code, text = self.run_cli(["provision-owner", "--email", "boss@example.com", "--display-name", "Boss"], prompts=[GOOD_PASSWORD, GOOD_PASSWORD])
        self.assertEqual(code, 0)
        self.assertIn("Created owner", text)
        self.assertNotIn(GOOD_PASSWORD, text)
        self.assertTrue(self.h.login("boss@example.com").principal.is_owner)
        self.assertIn("boss@example.com", self.run_cli(["list-owners"])[1])

    def test_a_second_owner_needs_an_explicit_flag(self):
        self.run_cli(["provision-owner", "--email", "boss@example.com", "--password-stdin"], password=GOOD_PASSWORD)
        code, text = self.run_cli(["provision-owner", "--email", "backup@example.com", "--password-stdin"], password=GOOD_PASSWORD)
        self.assertEqual(code, 1)
        self.assertIn("--allow-additional", text)
        code, text = self.run_cli(["provision-owner", "--email", "backup@example.com", "--password-stdin", "--allow-additional"], password=GOOD_PASSWORD)
        self.assertEqual(code, 0)

    def test_mismatched_or_weak_passwords_create_nothing(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["provision-owner", "--email", "boss@example.com"], prompts=[GOOD_PASSWORD, OTHER_PASSWORD])
        code, text = self.run_cli(["provision-owner", "--email", "boss@example.com", "--password-stdin"], password="short")
        self.assertEqual(code, 1)
        self.assertIn("at least 10 characters", text)
        self.assertEqual(self.h.rows("SELECT * FROM users"), [])

    def test_an_existing_account_is_promoted_without_asking_for_a_password(self):
        self.h.make_user("alice@example.com")
        code, text = self.run_cli(["provision-owner", "--email", "alice@example.com"])
        self.assertEqual((code, "Promoted" in text), (0, True))

    def test_break_glass_recovery(self):
        owner, token = self.h.make_owner("boss@example.com")
        code, text = self.run_cli(["recover-owner", "--email", "boss@example.com", "--password-stdin"], password=OTHER_PASSWORD)
        self.assertEqual(code, 0)
        self.assertNotIn(OTHER_PASSWORD, text)
        self.assertTrue(self.h.login("boss@example.com", OTHER_PASSWORD).principal.is_owner)
        code, text = self.run_cli(["recover-owner", "--email", "nobody@example.com", "--password-stdin"], password=OTHER_PASSWORD)
        self.assertEqual(code, 1)

    def test_purge_reports_counts(self):
        code, text = self.run_cli(["purge"])
        self.assertEqual(code, 0)
        self.assertIn("audit_events=", text)


if __name__ == "__main__":
    unittest.main()
