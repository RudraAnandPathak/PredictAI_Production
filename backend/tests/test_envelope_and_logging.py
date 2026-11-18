import io
import json
import logging
import unittest

from app.core.envelope import failure, success
from app.core.errors import AppError, ErrorCode, describe_status
from app.core.logging import JsonFormatter, configure_logging
from app.core.request_context import request_id_var


class EnvelopeTests(unittest.TestCase):
    def test_success_shape(self):
        self.assertEqual(success({"a": 1}, "rid-12345"), {"data": {"a": 1}, "meta": {"request_id": "rid-12345"}})

    def test_failure_shape_omits_empty_details(self):
        body = failure("NOT_FOUND", "Nope.", "rid-12345")
        self.assertEqual(body, {"error": {"code": "NOT_FOUND", "message": "Nope.", "request_id": "rid-12345"}})
        self.assertNotIn("details", body["error"])

    def test_failure_includes_details_when_given(self):
        body = failure("VALIDATION_ERROR", "Bad.", "rid-12345", [{"field": "q", "issue": "missing"}])
        self.assertEqual(body["error"]["details"], [{"field": "q", "issue": "missing"}])


class ErrorTests(unittest.TestCase):
    def test_known_statuses_have_safe_messages(self):
        self.assertEqual(describe_status(404)[0], ErrorCode.NOT_FOUND)
        self.assertEqual(describe_status(405)[0], ErrorCode.METHOD_NOT_ALLOWED)
        self.assertEqual(describe_status(422)[0], ErrorCode.VALIDATION_ERROR)
        self.assertEqual(describe_status(500)[0], ErrorCode.INTERNAL_ERROR)

    def test_unknown_status_gets_generic_message(self):
        self.assertEqual(describe_status(418), (ErrorCode.HTTP_ERROR, "The request could not be completed."))

    def test_app_error_carries_its_fields(self):
        err = AppError("DEMO", "Demo failed.", 409, [{"field": "x"}])
        self.assertEqual((err.code, err.message, err.status_code, err.details), ("DEMO", "Demo failed.", 409, [{"field": "x"}]))


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.saved_handlers = list(self.root.handlers)
        self.saved_level = self.root.level

    def tearDown(self):
        self.root.handlers[:] = self.saved_handlers
        self.root.setLevel(self.saved_level)

    def test_configure_logging_is_idempotent(self):
        configure_logging("INFO", "text")
        configure_logging("DEBUG", "json")
        ours = [h for h in self.root.handlers if getattr(h, "_predictai_handler", False)]
        self.assertEqual(len(ours), 1)
        self.assertEqual(self.root.level, logging.DEBUG)

    def test_json_lines_include_request_id_and_fields_but_not_secrets(self):
        configure_logging("INFO", "json")
        handler = [h for h in self.root.handlers if getattr(h, "_predictai_handler", False)][0]
        stream = io.StringIO()
        handler.setStream(stream)
        token = request_id_var.set("req-abcdef12")
        try:
            logging.getLogger("predictai.test").info("hello %s", "world", extra={"status": 200, "duration_ms": 1.5})
        finally:
            request_id_var.reset(token)
        record = json.loads(stream.getvalue().strip())
        self.assertEqual(record["message"], "hello world")
        self.assertEqual(record["request_id"], "req-abcdef12")
        self.assertEqual((record["status"], record["duration_ms"]), (200, 1.5))
        self.assertTrue(record["timestamp"].endswith("+00:00"))

    def test_json_formatter_includes_exception_text_for_server_side_logs(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
        self.assertIn("ValueError: boom", json.loads(JsonFormatter().format(record))["exception"])


if __name__ == "__main__":
    unittest.main()
