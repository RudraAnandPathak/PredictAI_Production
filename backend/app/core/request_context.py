"""Per-request correlation ID, available to handlers, logging and error responses."""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Any, Mapping, Optional

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: Optional[str]) -> Optional[str]:
    """Accept a caller-supplied ID only if it is short and made of safe characters."""
    if value and _SAFE_REQUEST_ID.match(value):
        return value
    return None


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def request_id_from_scope(scope: Mapping[str, Any]) -> str:
    """Best available request ID for an ASGI scope (never raises, never empty)."""
    state = scope.get("state")
    if isinstance(state, dict) and state.get("request_id"):
        return str(state["request_id"])
    return get_request_id() or new_request_id()
