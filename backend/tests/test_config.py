import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import config
from app.core.config import DEFAULT_DEV_ORIGINS, ConfigError, load_settings

# Phase 2 made secrets and the frontend URL mandatory in production, so a valid production setup needs them.
PROD_ENV = {
    "PREDICTAI_ENV": "production",
    "PREDICTAI_CORS_ALLOWED_ORIGINS": "https://app.example.com",
    "PREDICTAI_SECRET_KEY": "Zq7vN2xW9pLm4RtY6uHb3cEa5sDfG1jK8nV0",
    "PREDICTAI_FRONTEND_BASE_URL": "https://app.example.com",
    "PREDICTAI_REQUIRE_EMAIL_VERIFICATION": "false",
}


class LoadSettingsTests(unittest.TestCase):
    def test_development_defaults(self):
        s = load_settings({})
        self.assertEqual(s.environment, "development")
        self.assertEqual((s.host, s.port), ("127.0.0.1", 8000))
        self.assertEqual(s.cors_allowed_origins, DEFAULT_DEV_ORIGINS)
        self.assertEqual((s.log_level, s.log_format), ("INFO", "text"))
        self.assertTrue(s.docs_enabled)
        self.assertEqual(s.api_prefix, "/api/v1")

    def test_overrides_are_applied(self):
        s = load_settings({
            "PREDICTAI_ENV": "TEST",
            "PREDICTAI_API_HOST": "0.0.0.0",
            "PREDICTAI_API_PORT": "9100",
            "PREDICTAI_LOG_LEVEL": "debug",
            "PREDICTAI_LOG_FORMAT": "JSON",
            "PREDICTAI_API_DOCS_ENABLED": "no",
        })
        self.assertEqual(s.environment, "test")
        self.assertEqual((s.host, s.port), ("0.0.0.0", 9100))
        self.assertEqual((s.log_level, s.log_format), ("DEBUG", "json"))
        self.assertFalse(s.docs_enabled)

    def test_invalid_port(self):
        for bad in ("abc", "0", "70000", ""):
            with self.subTest(port=bad), self.assertRaises(ConfigError) as ctx:
                load_settings({"PREDICTAI_API_PORT": bad})
            self.assertIn("PREDICTAI_API_PORT", str(ctx.exception))

    def test_wildcard_origin_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PREDICTAI_CORS_ALLOWED_ORIGINS": "*"})
        self.assertIn("'*' is not allowed", str(ctx.exception))

    def test_origin_must_be_scheme_and_host_only(self):
        for bad in ("localhost:5173", "http://localhost:5173/", "http://localhost:5173/app", "ftp://x.test"):
            with self.subTest(origin=bad), self.assertRaises(ConfigError):
                load_settings({"PREDICTAI_CORS_ALLOWED_ORIGINS": bad})

    def test_origins_are_trimmed_and_deduplicated(self):
        s = load_settings({"PREDICTAI_CORS_ALLOWED_ORIGINS": " http://a.test , http://b.test,http://a.test,"})
        self.assertEqual(s.cors_allowed_origins, ("http://a.test", "http://b.test"))

    def test_production_requires_explicit_origins(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PREDICTAI_ENV": "production"})
        self.assertIn("PREDICTAI_CORS_ALLOWED_ORIGINS must be set", str(ctx.exception))

    def test_staging_requires_explicit_origins(self):
        with self.assertRaises(ConfigError):
            load_settings({"PREDICTAI_ENV": "staging"})

    def test_production_empty_origins_means_same_origin_only(self):
        s = load_settings({**PROD_ENV, "PREDICTAI_CORS_ALLOWED_ORIGINS": ""})
        self.assertEqual(s.cors_allowed_origins, ())

    def test_production_requires_https_origins(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PREDICTAI_ENV": "production", "PREDICTAI_CORS_ALLOWED_ORIGINS": "http://app.example.com"})
        self.assertIn("https", str(ctx.exception))

    def test_production_defaults_are_conservative(self):
        s = load_settings(PROD_ENV)
        self.assertTrue(s.is_production)
        self.assertFalse(s.docs_enabled)
        self.assertEqual(s.log_format, "json")

    def test_docs_can_be_enabled_explicitly(self):
        s = load_settings({**PROD_ENV, "PREDICTAI_API_DOCS_ENABLED": "true"})
        self.assertTrue(s.docs_enabled)

    def test_all_problems_are_reported_together(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"PREDICTAI_ENV": "qa", "PREDICTAI_API_PORT": "x", "PREDICTAI_LOG_LEVEL": "loud"})
        message = str(ctx.exception)
        for name in ("PREDICTAI_ENV", "PREDICTAI_API_PORT", "PREDICTAI_LOG_LEVEL"):
            self.assertIn(name, message)

    def test_boolean_must_be_valid(self):
        with self.assertRaises(ConfigError):
            load_settings({"PREDICTAI_API_DOCS_ENABLED": "maybe"})


class DotenvTests(unittest.TestCase):
    def test_real_environment_overrides_dotenv_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("PREDICTAI_API_PORT=9001\nPREDICTAI_LOG_LEVEL=DEBUG\n", encoding="utf-8")
            with mock.patch.object(config, "DOTENV_PATH", dotenv), mock.patch.object(config, "DATA_DIR", Path(tmp) / "data"), mock.patch.dict(
                os.environ, {"PREDICTAI_LOG_LEVEL": "ERROR"}, clear=True
            ):
                s = load_settings()
        self.assertEqual(s.port, 9001)  # from the file
        self.assertEqual(s.log_level, "ERROR")  # real environment wins

    def test_missing_dotenv_file_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DOTENV_PATH", Path("/nonexistent/.env")), mock.patch.object(
            config, "DATA_DIR", Path(tmp) / "data"
        ), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_settings().port, 8000)


class PredictiveModellingSettings(unittest.TestCase):
    def test_defaults_are_isolated_and_small(self):
        s = load_settings({})
        self.assertEqual((s.ml_isolation, s.ml_workers), ("subprocess", 2))

    def test_valid_values_are_applied_and_case_is_ignored(self):
        s = load_settings({"PREDICTAI_ML_ISOLATION": " InProcess ", "PREDICTAI_ML_WORKERS": "4"})
        self.assertEqual((s.ml_isolation, s.ml_workers), ("inprocess", 4))

    def test_bad_values_are_refused_with_a_message_that_names_the_setting(self):
        for key, value in (("PREDICTAI_ML_ISOLATION", "banana"), ("PREDICTAI_ML_ISOLATION", "thread"), ("PREDICTAI_ML_WORKERS", "0"), ("PREDICTAI_ML_WORKERS", "9"),
                           ("PREDICTAI_ML_WORKERS", "many"), ("PREDICTAI_ML_WORKERS", "-1")):
            with self.assertRaises(ConfigError) as ctx:
                load_settings({key: value})
            self.assertIn(key, str(ctx.exception), (key, value))

    def test_running_training_inside_the_api_process_is_refused_when_hosted(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({**PROD_ENV, "PREDICTAI_ML_ISOLATION": "inprocess"})
        self.assertIn("PREDICTAI_ML_ISOLATION", str(ctx.exception))
        self.assertEqual(load_settings({**PROD_ENV}).ml_isolation, "subprocess")


if __name__ == "__main__":
    unittest.main()
