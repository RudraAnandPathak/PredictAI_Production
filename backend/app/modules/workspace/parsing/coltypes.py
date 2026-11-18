"""Value classification and column type inference shared by extraction, profiling and cleaning.
Everything here is a heuristic over text values and is labelled as such where it is shown to users."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, Iterable, Optional, Tuple

MISSING_TOKENS = frozenset({"", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "?", "#n/a", "#na", "missing"})
_INT = re.compile(r"^[+-]?\d{1,18}$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_SLASH_YMD = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")
TRUE_TOKENS = frozenset({"true", "yes", "y", "t"})
FALSE_TOKENS = frozenset({"false", "no", "n", "f"})

NUMERIC = ("integer", "float")


def is_missing(value: Optional[str]) -> bool:
    return value is None or value.strip().lower() in MISSING_TOKENS


def classify(value: str) -> str:
    """One of: missing, integer, float, boolean, date, datetime, text."""
    text = value.strip()
    low = text.lower()
    if low in MISSING_TOKENS:
        return "missing"
    if _INT.match(text):
        return "integer"
    if _FLOAT.match(text):
        return "float"
    if low in TRUE_TOKENS or low in FALSE_TOKENS:
        return "boolean"
    if _ISO_DATE.match(text) or _SLASH_YMD.match(text):
        return "date" if _valid_date(text.replace("/", "-")) else "text"
    if _ISO_DATETIME.match(text):
        return "datetime"
    return "text"


def _valid_date(text: str) -> bool:
    try:
        parts = [int(p) for p in text.split("-")]
        date(*parts)
        return True
    except (ValueError, TypeError):
        return False


def to_float(value: str) -> Optional[float]:
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def parse_date(value: str) -> Optional[datetime]:
    text = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.replace("Z", "")[:19], fmt)
        except ValueError:
            continue
    return None


class ColumnTally:
    """Counts value classes for one column in a single pass."""

    __slots__ = ("counts", "total")

    def __init__(self) -> None:
        self.counts: Dict[str, int] = {}
        self.total = 0

    def add(self, value: str) -> None:
        kind = classify(value)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.total += 1

    @property
    def missing(self) -> int:
        return self.counts.get("missing", 0)

    def inferred(self) -> Tuple[str, int]:
        """(type, invalid_count). A type is chosen when at least 90% of the non-missing values fit it; otherwise 'mixed'.
        'invalid' counts non-missing values that do not fit the chosen type (always 0 for text and mixed)."""
        present = self.total - self.missing
        if present == 0:
            return "empty", 0
        c = self.counts
        numeric = c.get("integer", 0) + c.get("float", 0)
        groups = {"numeric": numeric, "boolean": c.get("boolean", 0), "date": c.get("date", 0) + c.get("datetime", 0), "text": c.get("text", 0)}
        name, share = max(groups.items(), key=lambda kv: kv[1])
        if share / present < 0.9:
            return "mixed", 0
        if name == "numeric":
            return ("integer" if c.get("float", 0) == 0 else "float"), present - numeric
        if name == "date":
            return ("datetime" if c.get("datetime", 0) else "date"), present - share
        if name == "boolean":
            return "boolean", present - share
        return "text", 0
