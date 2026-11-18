"""Analytics errors: safe to show to users, never containing dataset values."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.errors import AppError
from ..workspace.errors import invalid, not_found  # noqa: F401  (re-exported: one 404/422 vocabulary)


def plan_invalid(field: str, message: str) -> AppError:
    return invalid(field, message)


def too_large(message: str) -> AppError:
    return AppError("ANALYSIS_TOO_LARGE", message, 413)


def unavailable(message: str) -> AppError:
    return AppError("ANALYTICS_UNAVAILABLE", message, 503)


def failed(code: str, message: str, details: Optional[List[Dict[str, Any]]] = None) -> AppError:
    return AppError(code, message, 422, details=details)


class AnalysisError(Exception):
    """Raised inside the engine; carries a stable code and a message that contains no data values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class AnalysisTimeout(AnalysisError):
    def __init__(self) -> None:
        super().__init__("ANALYSIS_TIMEOUT", "The analysis ran past its time limit and was stopped. Narrow the columns, add filters or analyse a sample.")
