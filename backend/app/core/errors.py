"""Typed application errors and the safe, user-facing messages for HTTP statuses.

Nothing here depends on FastAPI. Error responses never include stack traces,
secrets, raw exception text or echoed request input.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class ErrorCode:
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_SAFE_BY_STATUS = {
    404: (ErrorCode.NOT_FOUND, "The requested resource was not found."),
    405: (ErrorCode.METHOD_NOT_ALLOWED, "This method is not allowed for the requested resource."),
    422: (ErrorCode.VALIDATION_ERROR, "The request contains invalid data."),
    500: (ErrorCode.INTERNAL_ERROR, "Something went wrong on our side. Please try again."),
}


def describe_status(status_code: int) -> Tuple[str, str]:
    """Return the (code, safe message) pair for an HTTP status."""
    return _SAFE_BY_STATUS.get(
        status_code, (ErrorCode.HTTP_ERROR, "The request could not be completed.")
    )


class AppError(Exception):
    """An expected, user-visible failure raised by routers or domain services."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[List[Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.headers = headers
