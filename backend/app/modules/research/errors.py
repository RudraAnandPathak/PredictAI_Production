"""Research errors. Messages are safe to show and say what to do next."""
from __future__ import annotations

from typing import Optional

from ...core.errors import AppError


class Cancelled(Exception):
    """The person cancelled the run; nothing is saved."""


class ResearchFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def blocked(code: str, message: str) -> AppError:
    return AppError(code, message, 403)


def busy(message: str) -> AppError:
    return AppError("RESEARCH_BUSY", message, 409)


def limit(message: str) -> AppError:
    return AppError("RESEARCH_LIMIT", message, 429)
