"""When a provider's quota day starts. Google resets the Gemini request quota at midnight Pacific time, not at midnight UTC, so a cap that used the wrong midnight would either stop too early
or let a request through that the provider then refuses. `quota_tz` names the zone; empty means UTC. An unknown zone name falls back to UTC (a zone that cannot be found must not turn the guard off)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ...core.clock import to_iso


def zone(name: Optional[str]):
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                                   # noqa: BLE001  missing tz database or an unknown name: UTC is the safe, predictable choice
        return timezone.utc


def day_start(now: datetime, tz_name: Optional[str]) -> str:
    local = now.astimezone(zone(tz_name))
    return to_iso(local.replace(hour=0, minute=0, second=0, microsecond=0))


def next_reset(now: datetime, tz_name: Optional[str]) -> str:
    local = now.astimezone(zone(tz_name)).replace(hour=0, minute=0, second=0, microsecond=0)
    return to_iso(local + timedelta(days=1))
