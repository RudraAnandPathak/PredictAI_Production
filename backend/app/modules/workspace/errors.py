"""Workspace errors. Messages are safe to show to users and say what to do next."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.errors import AppError, ErrorCode


def not_found(what: str = "resource") -> AppError:
    # Same answer for "does not exist" and "belongs to someone else", so IDs cannot be probed.
    return AppError(ErrorCode.NOT_FOUND, f"That {what} was not found.", 404)


def invalid(field: str, message: str, issue: str = "invalid") -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, "Please correct the highlighted fields.", 422,
                    details=[{"field": field, "issue": issue, "message": message}])


def conflict(code: str, message: str) -> AppError:
    return AppError(code, message, 409)


def project_not_active() -> AppError:
    return conflict("PROJECT_NOT_ACTIVE", "This project is archived. Restore it to add or change data.")


def too_large(limit_mb: float) -> AppError:
    shown = int(limit_mb) if limit_mb == int(limit_mb) else round(limit_mb, 1)
    return AppError("FILE_TOO_LARGE", f"This file is larger than the {shown} MB limit. Split it or remove unneeded data and try again.", 413)


def unsupported(detail: str) -> AppError:
    return AppError("UNSUPPORTED_FILE", detail, 415)


def corrupted(detail: str) -> AppError:
    return AppError("FILE_CORRUPTED", detail, 422)


def quota(code: str, message: str) -> AppError:
    return AppError(code, message, 403)


def bad_request(code: str, message: str, details: Optional[List[Dict[str, Any]]] = None) -> AppError:
    return AppError(code, message, 400, details=details)
