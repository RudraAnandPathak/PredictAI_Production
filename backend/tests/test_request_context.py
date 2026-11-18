import logging
import unittest

from app.core.middleware import RequestContextMiddleware
from app.core.request_context import (
    get_request_id,
    new_request_id,
    request_id_from_scope,
    sanitize_request_id,
)


def make_scope(headers=None, path="/x", query=b""):
    return {"type": "http", "method": "GET", "path": path, "query_string": query, "headers": headers or []}


async def drive(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


class Recorder:
    """Inner ASGI app that records what the middleware exposed and replies 200."""

    def __init__(self, response_headers=None):
        self.seen_context_id = None
        self.response_headers = response_headers or []

    async def __call__(self, scope, receive, send):
        self.seen_context_id = get_request_id()
        await send({"type": "http.response.start", "status": 200, "headers": list(self.response_headers)})
        await send({"type": "http.response.body", "body": b"{}"})


def header_values(message, name):
    return [v for k, v in message["headers"] if k == name]


class SanitizeTests(unittest.TestCase):
    def test_accepts_safe_ids(self):
        for value in ("abcdef12", "A.b_c-1234567890", "a" * 64):
            self.assertEqual(sanitize_request_id(value), value)

    def test_rejects_unsafe_ids(self):
        for value in (None, "", "short", "a" * 65, "has space 1234", "semi;colon1234", "new\nline12345", "<script>alert(1)"):
            self.assertIsNone(sanitize_request_id(value), value)

    def test_new_ids_are_unique_and_valid(self):
        a, b = new_request_id(), new_request_id()
        self.assertNotEqual(a, b)
        self.assertEqual(sanitize_request_id(a), a)

    def test_request_id_from_scope_prefers_state_then_falls_back(self):
        self.assertEqual(request_id_from_scope({"state": {"request_id": "from-state-123"}}), "from-state-123")
        self.assertTrue(request_id_from_scope({}))


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_id_and_exposes_it_everywhere(self):
        inner = Recorder()
        scope = make_scope()
        sent = await drive(RequestContextMiddleware(inner), scope)
        header_id = header_values(sent[0], b"x-request-id")[0].decode()
        self.assertEqual(sanitize_request_id(header_id), header_id)
        self.assertEqual(scope["state"]["request_id"], header_id)
        self.assertEqual(inner.seen_context_id, header_id)
        self.assertIsNone(get_request_id(), "context must be reset after the request")

    async def test_keeps_a_valid_incoming_id(self):
        sent = await drive(RequestContextMiddleware(Recorder()), make_scope([(b"x-request-id", b"client-id-12345")]))
        self.assertEqual(header_values(sent[0], b"x-request-id"), [b"client-id-12345"])

    async def test_replaces_an_unsafe_incoming_id(self):
        sent = await drive(RequestContextMiddleware(Recorder()), make_scope([(b"x-request-id", b"bad id;<x>")]))
        value = header_values(sent[0], b"x-request-id")[0]
        self.assertNotEqual(value, b"bad id;<x>")
        self.assertEqual(len(value), 32)

    async def test_adds_security_headers_without_duplicating(self):
        inner = Recorder(response_headers=[(b"cache-control", b"max-age=5"), (b"x-request-id", b"inner-value-1")])
        sent = await drive(RequestContextMiddleware(inner), make_scope())
        self.assertEqual(header_values(sent[0], b"cache-control"), [b"max-age=5"])
        self.assertEqual(header_values(sent[0], b"x-content-type-options"), [b"nosniff"])
        self.assertEqual(header_values(sent[0], b"referrer-policy"), [b"no-referrer"])
        self.assertEqual(len(header_values(sent[0], b"x-request-id")), 1)
        self.assertNotEqual(header_values(sent[0], b"x-request-id"), [b"inner-value-1"])

    async def test_non_http_scopes_pass_through_untouched(self):
        called = []

        async def inner(scope, receive, send):
            called.append(scope["type"])

        await RequestContextMiddleware(inner)({"type": "lifespan"}, None, None)
        self.assertEqual(called, ["lifespan"])

    async def test_logs_one_line_and_never_the_query_string(self):
        with self.assertLogs("predictai.access", level=logging.INFO) as logs:
            await drive(RequestContextMiddleware(Recorder()), make_scope(path="/api/v1/health", query=b"token=secret"))
        self.assertEqual(len(logs.records), 1)
        text = logs.records[0].getMessage()
        self.assertIn("GET /api/v1/health -> 200", text)
        self.assertNotIn("secret", text)
        self.assertEqual(logs.records[0].status, 200)

    async def test_failure_is_logged_as_500_context_is_reset_and_error_propagates(self):
        async def boom(scope, receive, send):
            raise RuntimeError("boom")

        with self.assertLogs("predictai.access", level=logging.INFO) as logs:
            with self.assertRaises(RuntimeError):
                await drive(RequestContextMiddleware(boom), make_scope())
        self.assertEqual(logs.records[0].status, 500)
        self.assertIsNone(get_request_id())


if __name__ == "__main__":
    unittest.main()
