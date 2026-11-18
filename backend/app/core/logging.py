"""Logging setup: text for local development, JSON lines for hosted environments."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .request_context import get_request_id

_EXTRA_FIELDS = ("method", "path", "status", "duration_ms")
_HANDLER_MARK = "_predictai_handler"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# Libraries that read people's files or make outbound calls. At DEBUG some of them write the text they are parsing (document contents, request URLs) into the log, so they are kept at
# WARNING whatever level the operator chose for the application.
QUIET_LIBRARIES = ("pdfminer", "pdfplumber", "pypdf", "PIL", "httpcore", "httpx", "urllib3")


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install one stderr handler on the root logger. Safe to call more than once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
