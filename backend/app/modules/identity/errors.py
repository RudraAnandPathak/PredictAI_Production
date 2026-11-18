"""Typed errors for identity use cases. Messages are safe to show to users."""
from __future__ import annotations

from typing import Any, Dict, List

from ...core.errors import AppError, ErrorCode


def unauthenticated() -> AppError:
    return AppError(ErrorCode.UNAUTHENTICATED, "Please sign in to continue.", 401)


def session_expired() -> AppError:
    return AppError("SESSION_EXPIRED", "Your session has ended. Please sign in again.", 401)


def forbidden() -> AppError:
    return AppError(ErrorCode.FORBIDDEN, "You do not have permission to do that.", 403)


def reauth_required() -> AppError:
    return AppError("REAUTH_REQUIRED", "Please confirm your password to continue.", 403)


def csrf_failed() -> AppError:
    return AppError("CSRF_FAILED", "The request could not be verified. Reload the page and try again.", 403)


def rate_limited(retry_after_seconds: int) -> AppError:
    seconds = max(1, int(retry_after_seconds))
    return AppError(
        ErrorCode.RATE_LIMITED,
        "Too many attempts. Please wait a little and try again.",
        429,
        details=[{"retry_after_seconds": seconds}],
        headers={"Retry-After": str(seconds)},
    )


def invalid_credentials() -> AppError:
    # One message for every sign-in failure, so it never reveals whether an account exists.
    return AppError("INVALID_CREDENTIALS", "We could not sign you in. Check your email and password, or reset your password.", 401)


def wrong_password() -> AppError:
    return AppError("INVALID_PASSWORD", "That password is not correct.", 400)


def invalid_token() -> AppError:
    return AppError("INVALID_TOKEN", "This link is invalid or has expired. Please request a new one.", 400)


def invalid_code() -> AppError:
    return AppError("INVALID_CODE", "That access code is not valid.", 400)


def conflict(code: str, message: str) -> AppError:
    return AppError(code, message, 409)


def not_found() -> AppError:
    return AppError(ErrorCode.NOT_FOUND, "The requested resource was not found.", 404)


def unavailable(message: str = "This feature is not available right now.") -> AppError:
    return AppError(ErrorCode.SERVICE_UNAVAILABLE, message, 503)


def validation_failed(problems: List[Dict[str, Any]]) -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, "Please correct the highlighted fields.", 422, details=problems)
