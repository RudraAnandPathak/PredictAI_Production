"""Pure-ASGI middleware: request IDs, safe response headers and one access-log line.

Written against the ASGI interface only (no framework imports) so it can be tested
in isolation. The query string is deliberately never logged.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, MutableMapping, Optional, Tuple

from .request_context import REQUEST_ID_HEADER, new_request_id, request_id_var, sanitize_request_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SECURITY_HEADERS: Tuple[Tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
)

access_logger = logging.getLogger("predictai.access")


def security_headers() -> Dict[str, str]:
    """The same headers as a str dict, for responses built outside this middleware."""
    return {name.decode(): value.decode() for name, value in SECURITY_HEADERS}


def _incoming_request_id(scope: Scope) -> Optional[str]:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            return sanitize_request_id(value.decode("latin-1"))
    return None


def _has_header(headers: List[Tuple[bytes, bytes]], name: bytes) -> bool:
    return any(existing == name for existing, _ in headers)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or new_request_id()
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name != REQUEST_ID_HEADER.lower().encode()
                ]
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                for name, value in SECURITY_HEADERS:
                    if not _has_header(headers, name):
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            method = scope.get("method", "-")
            path = scope.get("path", "-")
            access_logger.info(
                "%s %s -> %s (%.1f ms)",
                method,
                path,
                status["code"],
                duration_ms,
                extra={"method": method, "path": path, "status": status["code"], "duration_ms": duration_ms},
            )
            request_id_var.reset(token)
