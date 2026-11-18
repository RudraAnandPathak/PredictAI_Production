"""Coverage trends: how often a topic or a name appears in the articles the person collected, this week compared with last.

Counting coverage says what was written about, not what happened or what matters, and it only reflects the feeds the person chose. A change is called "rising" or "falling" only when it
is large relative to chance (a Poisson-style z of 2 or more) AND there are enough articles; otherwise it is "steady" or "too few to say"."""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..research.sources import parse_date

METHOD = "coverage-trend/1"
MIN_TOTAL = 8


def day_of(published: Optional[str]) -> Optional[str]:
    t = parse_date(published) if published else None
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d") if t else None


def rate_change(recent: int, previous: int, *, min_total: int = MIN_TOTAL) -> Dict[str, Any]:
    total = recent + previous
    if total < min_total:
        return {"label": "too few to say", "z": None, "recent": recent, "previous": previous}
    z = (recent - previous) / math.sqrt(total)
    return {"label": "rising" if z >= 2 else "falling" if z <= -2 else "steady", "z": round(z, 2), "recent": recent, "previous": previous}


def daily_series(articles: Sequence[Dict[str, Any]], *, topic: Optional[str] = None, entity: Optional[str] = None, measure: str = "count") -> Dict[str, float]:
    """{YYYY-MM-DD: value} over days with at least one matching article. measure: count, or tone (mean tone score)."""
    sums: Dict[str, List[float]] = {}
    for a in articles:
        d = day_of(a.get("published"))
        if d is None:
            continue
        der = a.get("derived") or {}
        if topic and topic not in [t["topic"] for t in (der.get("topics") or {}).get("topics", [])]:
            continue
        if entity and entity.lower() not in [e["text"].lower() for e in (der.get("entities") or {}).get("entities", [])]:
            continue
        sums.setdefault(d, []).append(float(((der.get("tone") or {}).get("score")) or 0.0))
    if measure == "tone":
        return {d: sum(v) / len(v) for d, v in sums.items()}
    return {d: float(len(v)) for d, v in sums.items()}


def fill_days(series: Dict[str, float], start: str, end: str, fill: float = 0.0) -> Dict[str, float]:
    d0, d1 = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    out, d = {}, d0
    while d <= d1:
        k = d.strftime("%Y-%m-%d")
        out[k] = series.get(k, fill)
        d += timedelta(days=1)
    return out


def trends(articles: Sequence[Dict[str, Any]], *, window_days: int = 7, now: Optional[datetime] = None, top: int = 12) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    recent_start, prev_start = now - timedelta(days=window_days), now - timedelta(days=2 * window_days)
    rec_t, prev_t, rec_e, prev_e = Counter(), Counter(), Counter(), Counter()
    n_recent = n_prev = undated = 0
    for a in articles:
        t = parse_date(a["published"]) if a.get("published") else None
        if t is None:
            undated += 1
            continue
        if t > now or t < prev_start:
            continue
        recent = t >= recent_start
        n_recent += recent
        n_prev += not recent
        der = a.get("derived") or {}
        topics = {x["topic"] for x in (der.get("topics") or {}).get("topics", [])}
        ents = {e["text"].lower() for e in (der.get("entities") or {}).get("entities", [])[:10]}
        (rec_t if recent else prev_t).update(topics)
        (rec_e if recent else prev_e).update(ents)

    def table(rec: Counter, prev: Counter) -> List[Dict[str, Any]]:
        keys = sorted(set(rec) | set(prev), key=lambda k: (-(rec[k] + prev[k]), k))[:top]
        return [{"name": k, **rate_change(rec[k], prev[k])} for k in keys]

    return {"window_days": window_days, "recent_articles": n_recent, "previous_articles": n_prev, "undated_articles": undated, "topics": table(rec_t, prev_t), "entities": table(rec_e, prev_e), "method": METHOD,
            "caveats": ["Counts reflect only the feeds you added and how they were fetched.", "More coverage does not mean more happened, and less coverage does not mean it stopped.",
                        "A change is called rising or falling only when it is large compared with chance and there are enough articles."]}
