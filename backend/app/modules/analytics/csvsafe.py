"""Spreadsheet-formula neutralisation for exported cells; dependency-free so both the analytics service and the isolated prediction worker can use it."""
from __future__ import annotations

from typing import Any

FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """Neutralises spreadsheet formula injection in exported cells (a text value starting with = + - @ is prefixed with an apostrophe)."""
    if isinstance(value, str) and value.startswith(FORMULA_CHARS):
        try:
            float(value)
            return value                       # a plain negative/positive number is not a formula
        except ValueError:
            return "'" + value
    return value
