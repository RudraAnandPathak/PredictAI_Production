"""API tests. They need FastAPI and httpx (pip install -r requirements-dev.txt)."""
import json
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    import httpx  # noqa: F401  (TestClient dependency)
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise unittest.SkipTest("fastapi and httpx are required: pip install -r requirements-dev.txt") from exc

import os
import secrets

# Importing app.main builds the module-level `app`, which in development would create and persist a dev secret under
# data/. Tests must not touch the project folder, so give that import a throwaway secret first.
os.environ.setdefault("PREDICTAI_SECRET_KEY", secrets.token_urlsafe(48))

from app import __version__
from app.core.config import load_settings
from app.core.errors import AppError
from app.main import create_app

CONTRACTS = Path(__file__).resolve().parents[2] / "shared" / "contracts"
DEV = load_settings({})


def shape(value):
    """Reduce JSON to its structure and scalar types so it can be compared with the contract."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def contract(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(DEV))

    def test_health_matches_the_shared_contract(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(shape(body), shape(contract("health.example.json")))
        self.assertEqual(body["data"]["status"], "ok")
        self.assertEqual(body["data"]["version"], __version__)
        self.assertEqual(body["data"]["environment"], "development")
        self.assertEqual(body["data"]["service"], "predictai-api")

    def test_request_id_is_generated_and_matches_body(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.headers["x-request-id"], response.json()["meta"]["request_id"])

    def test_valid_incoming_request_id_is_echoed_and_unsafe_one_replaced(self):
        ok = self.client.get("/api/v1/health", headers={"X-Request-ID": "client-id-12345"})
        self.assertEqual(ok.headers["x-request-id"], "client-id-12345")
        bad = self.client.get("/api/v1/health", headers={"X-Request-ID": "bad id <x>"})
        self.assertNotEqual(bad.headers["x-request-id"], "bad id <x>")

    def test_safe_headers_are_present(self):
        headers = self.client.get("/api/v1/health").headers
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["cache-control"], "no-store")

    def test_head_is_not_an_error(self):
        self.assertIn(self.client.head("/api/v1/health").status_code, (200, 405))


class ErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DEV)

        async def echo(count: int):
            return {"count": count}

        async def boom():
            raise RuntimeError("secret internal detail")

        async def conflict():
            raise AppError("DEMO_CONFLICT", "Demo conflict.", 409)

        self.app.add_api_route("/api/v1/__t/echo", echo)
        self.app.add_api_route("/api/v1/__t/boom", boom)
        self.app.add_api_route("/api/v1/__t/conflict", conflict)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_unknown_route_uses_error_envelope(self):
        response = self.client.get("/api/v1/nope")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(shape(body), shape(contract("error.example.json")))
        self.assertEqual(body["error"]["code"], "NOT_FOUND")
        self.assertEqual(response.headers["x-request-id"], body["error"]["request_id"])

    def test_wrong_method_is_405_with_allow_header(self):
        response = self.client.post("/api/v1/health")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")
        self.assertIn("GET", response.headers.get("allow", ""))

    def test_validation_error_names_the_field_but_not_the_value(self):
        response = self.client.get("/api/v1/__t/echo", params={"count": "not-a-number"})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(body["error"]["details"][0]["field"], "query.count")
        self.assertNotIn("not-a-number", response.text)

    def test_unhandled_exception_is_a_safe_500(self):
        response = self.client.get("/api/v1/__t/boom")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret internal detail", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertTrue(response.headers.get("x-request-id"))

    def test_app_error_maps_to_its_status_and_code(self):
        response = self.client.get("/api/v1/__t/conflict")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "DEMO_CONFLICT")


class CorsAndDocsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(DEV))

    def preflight(self, origin):
        return self.client.options(
            "/api/v1/health",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )

    def test_allowed_origin_passes_preflight(self):
        response = self.preflight("http://localhost:5173")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_other_origin_gets_no_cors_grant(self):
        response = self.preflight("https://evil.example")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_docs_are_served_in_development(self):
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/openapi.json").status_code, 200)

    def test_docs_are_off_in_production(self):
        settings = load_settings({
            "PREDICTAI_ENV": "production",
            "PREDICTAI_CORS_ALLOWED_ORIGINS": "https://app.example.com",
            "PREDICTAI_SECRET_KEY": "Zq7vN2xW9pLm4RtY6uHb3cEa5sDfG1jK8nV0",
            "PREDICTAI_FRONTEND_BASE_URL": "https://app.example.com",
            "PREDICTAI_REQUIRE_EMAIL_VERIFICATION": "false",
        })
        client = TestClient(create_app(settings))
        self.assertEqual(client.get("/api/v1/docs").status_code, 404)
        self.assertEqual(client.get("/api/v1/openapi.json").status_code, 404)
        self.assertEqual(client.get("/api/v1/health").json()["data"]["environment"], "production")


if __name__ == "__main__":
    unittest.main()
