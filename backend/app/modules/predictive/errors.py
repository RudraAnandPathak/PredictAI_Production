"""Predictive errors: safe to show to users, never containing dataset values."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.errors import AppError
from ..workspace.errors import invalid, not_found  # noqa: F401  (one 404/422 vocabulary across the product)


def config_invalid(field: str, message: str) -> AppError:
    return invalid(field, message)


def unsuitable(code: str, message: str, details: Optional[List[Dict[str, Any]]] = None) -> AppError:
    return AppError(code, message, 422, details=details)


def conflict(code: str, message: str) -> AppError:
    return AppError(code, message, 409)


def quota(code: str, message: str) -> AppError:
    return AppError(code, message, 409)


def too_large(message: str) -> AppError:
    return AppError("MODEL_INPUT_TOO_LARGE", message, 413)


class MlError(Exception):
    """Raised inside the modelling code; carries a stable code and a message that contains no data values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class Cancelled(Exception):
    """The job was cancelled while it was running."""


class OutOfTime(MlError):
    def __init__(self) -> None:
        super().__init__("TRAINING_TIMEOUT", "Training reached its time limit.")
