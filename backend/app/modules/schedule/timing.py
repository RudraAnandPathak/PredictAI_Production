"""When a schedule runs next. Pure functions, timezone-aware (zoneinfo), no clock access.

Rules (also in the user guide): a time that does not exist on a daylight-saving change runs at the first valid moment after it; a time that happens twice runs the first time;
a monthly schedule on the 29th to 31st runs on the last day of shorter months; a missed run is never replayed - after downtime the next run is the first future one."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def zone(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not 1 <= len(name) <= 64:
        raise ValueError("timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("timezone") from None


def hhmm(text: Any) -> tuple:
    m = _HHMM.match(text) if isinstance(text, str) else None
    if not m:
        raise ValueError("local_time")
    return int(m.group(1)), int(m.group(2))


def _at(day: date, h: int, m: int, tz: ZoneInfo) -> datetime:
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
    return local.astimezone(timezone.utc).astimezone(tz).astimezone(timezone.utc)      # round trip moves a non-existent local time to the first valid moment after it


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def next_after(s: Dict[str, Any], after: datetime) -> Optional[datetime]:
    """First run strictly after `after` (UTC), or None for a one-off that has run."""
    freq = s["frequency"]
    tz = zone(s["timezone"])
    h, m = hhmm(s["local_time"])
    local_after = after.astimezone(tz)
    if freq == "once":
        at = parse_utc(s["run_at"]) if s.get("run_at") else None
        return at if at and at > after else None
    if freq == "daily":
        d = local_after.date()
        for i in range(0, 3):
            c = _at(d + timedelta(days=i), h, m, tz)
            if c > after:
                return c
    if freq == "weekly":
        wd = int(s["weekday"])
        d = local_after.date()
        for i in range(0, 9):
            day = d + timedelta(days=i)
            if day.weekday() == wd:
                c = _at(day, h, m, tz)
                if c > after:
                    return c
    if freq == "monthly":
        dom = int(s["day_of_month"])
        y, mo = local_after.year, local_after.month
        for _ in range(14):
            day = date(y, mo, min(dom, calendar.monthrange(y, mo)[1]))
            c = _at(day, h, m, tz)
            if c > after:
                return c
            mo += 1
            if mo == 13:
                y, mo = y + 1, 1
    raise ValueError("frequency")
