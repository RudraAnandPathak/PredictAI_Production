"""Why is Google/GitHub sign-in "not set up"? The diagnostics name the cause without ever printing a secret."""
import io
import tempfile
import unittest
from pathlib import Path

from app.cli import main as cli_main
from app.core.config import load_settings, oauth_diagnostics

SECRET = "super-secret-value-123"


class OAuthDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env_file = self.tmp / ".env"
        self.env_file.write_text("")

    def diag(self, env):
        return {d["provider"]: d for d in oauth_diagnostics(env, self.env_file)}

    def test_correct_variables_are_configured_and_settings_agree(self):
        env = {"PREDICTAI_OAUTH_GOOGLE_CLIENT_ID": "id", "PREDICTAI_OAUTH_GOOGLE_CLIENT_SECRET": SECRET}
        report = self.diag(env)
        self.assertTrue(report["google"]["configured"])
        self.assertFalse(report["github"]["configured"])
        self.assertTrue(load_settings(env).oauth_google_client_id)  # diagnostics and settings agree

    def test_unprefixed_names_are_reported_as_the_likely_cause(self):
        report = self.diag({"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": SECRET})
        google = report["google"]
        self.assertFalse(google["configured"])
        self.assertEqual(google["missing"], ["PREDICTAI_OAUTH_GOOGLE_CLIENT_ID", "PREDICTAI_OAUTH_GOOGLE_CLIENT_SECRET"])
        text = " ".join(google["hints"])
        self.assertIn("GOOGLE_CLIENT_ID", text)
        self.assertIn("restart", text.lower())
        self.assertNotIn(SECRET, repr(report))
        self.assertNotIn("GOOGLE", " ".join(report["github"]["hints"]))

    def test_missing_dotenv_file_is_pointed_out(self):
        self.env_file.unlink()
        self.assertTrue(any("No .env file" in h for h in self.diag({})["google"]["hints"]))

    def test_cli_prints_status_without_secrets(self):
        lines = []
        code = cli_main(["oauth-status"], out=lines.append)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("google:", text)
        self.assertIn("github:", text)
        self.assertNotIn(SECRET, text)


if __name__ == "__main__":
    unittest.main()
