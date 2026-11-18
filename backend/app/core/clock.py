"""Time source. All stored timestamps are fixed-width UTC ISO strings, so they sort as text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_FORMAT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _FORMAT).replace(tzinfo=timezone.utc)


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def iso(self, offset: Optional[timedelta] = None) -> str:
        return to_iso(self.now() + (offset or timedelta(0)))


class ManualClock(Clock):
    """A clock that only moves when told to; used by tests and verification harnesses."""

    def __init__(self, start: Optional[datetime] = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)
