"""An event is a group of articles about the same thing. This module summarises one group honestly: who reported it and when, what kind of statements they made, whether outlets agree
or disagree, and a status with the reasoning behind it.

Statuses (the words are chosen to avoid claiming more than was checked):
  confirmed   three or more outlets report matching facts in plain (not hedged, not opinion) wording and none disagrees. This is agreement between outlets, NOT verification by PredictAI.
  reported    two or more outlets carry it, without that level of agreement.
  disputed    outlets give different numbers or opposite statements about what looks like the same thing.
  unverified  a single outlet, or only hedged, opinion or forecast wording."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from ..research.evidence import _jaccard, same_claim
from ..research.sources import parse_date
from ..textkit import numbers_in, tokenize
from .cluster import centrality, vectorise

METHOD = "event-summary/1"
SOURCE_NOTE = "Agreement between outlets is not proof: outlets often repeat one wire report or one official statement."


def publisher_of(a: Dict[str, Any]) -> str:
    return (a.get("publisher") or a.get("domain") or "unknown").strip().lower()


def _claim(a: Dict[str, Any]) -> Dict[str, Any]:
    st = (a.get("derived") or {}).get("statement") or {}
    text = f"{a.get('title') or ''} {a.get('lead') or a.get('summary') or ''}"
    return {"source": publisher_of(a), "tokens": sorted(set(tokenize(text))), "numbers": st.get("numbers") or sorted(numbers_in(text)),
            "flags": {"negated": bool(st.get("negated")), "direction": st.get("direction")}}


def members_hash(ids: Sequence[str]) -> str:
    return hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()


def disagreements(members: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pairs from different outlets that look like the same claim with different numbers, opposite direction or opposite sense."""
    out: List[Dict[str, Any]] = []
    claims = [(a, _claim(a)) for a in members]
    for i, (a, ca) in enumerate(claims):
        for b, cb in claims[i + 1:]:
            if ca["source"] == cb["source"]:
                continue
            sim = _jaccard(set(ca["tokens"]) - set(ca["numbers"]), set(cb["tokens"]) - set(cb["numbers"]))
            if sim < 0.4:
                continue
            reason = None
            if ca["numbers"] and cb["numbers"] and not set(ca["numbers"]) & set(cb["numbers"]):
                reason = "different numbers"
            elif ca["flags"]["direction"] and cb["flags"]["direction"] and ca["flags"]["direction"] != cb["flags"]["direction"]:
                reason = "opposite direction"
            elif ca["flags"]["negated"] != cb["flags"]["negated"] and sim >= 0.5:
                reason = "one denies what the other states"
            if reason:
                out.append({"articles": [a["id"], b["id"]], "outlets": [ca["source"], cb["source"]], "reason": reason,
                            "numbers": {ca["source"]: ca["numbers"][:5], cb["source"]: cb["numbers"][:5]}})
    return out[:20]


def summarise(members: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything the event page shows, from member articles (id, title, lead, publisher, published, url, derived)."""
    vecs = vectorise(members)
    cent = centrality(members, vecs)
    rep = max(members, key=lambda a: (cent[a["id"]], -(parse_date(a["published"]).timestamp() if a.get("published") and parse_date(a["published"]) else 9e12), a["id"]))
    dated = sorted((a for a in members if a.get("published") and parse_date(a["published"])), key=lambda a: parse_date(a["published"]))
    outlets = sorted({publisher_of(a) for a in members})
    kinds = Counter(((a.get("derived") or {}).get("statement") or {}).get("kind", "stated") for a in members)
    conflicts = disagreements(members)
    corroborating = 0
    claims = [_claim(a) for a in members]
    for i, ca in enumerate(claims):
        for cb in claims[i + 1:]:
            if same_claim(ca, cb):
                corroborating += 1
    plain = kinds.get("stated", 0) / len(members)
    if conflicts:
        status, basis = "disputed", f"{len(conflicts)} pair(s) of outlets give {'different numbers or opposite statements' if len(conflicts) > 1 else conflicts[0]['reason']} about what looks like the same thing."
    elif len(outlets) >= 3 and corroborating >= 1 and plain >= 0.6:
        status, basis = "confirmed", f"{len(outlets)} outlets report matching facts in plain wording and no disagreement was found."
    elif len(outlets) >= 2:
        status, basis = "reported", f"{len(outlets)} outlets carry it; there is not enough matching plain-worded reporting to call it confirmed."
    elif plain == 0:
        status, basis = "unverified", "A single outlet, and its wording is hedged, opinion or a forecast."
    else:
        status, basis = "unverified", "Only one outlet carries it."
    topics = Counter(t["topic"] for a in members for t in ((a.get("derived") or {}).get("topics") or {}).get("topics", []))
    ents = Counter(e["text"].lower() for a in members for e in ((a.get("derived") or {}).get("entities") or {}).get("entities", [])[:8])
    return {"label": rep["title"], "label_from": {"article_id": rep["id"], "publisher": publisher_of(rep), "note": "The headline of the most typical article, quoted from its outlet."},
            "first_seen": dated[0]["published"] if dated else None, "last_seen": dated[-1]["published"] if dated else None, "article_count": len(members), "source_count": len(outlets),
            "outlets": outlets[:20], "status": status, "basis": basis, "statement_kinds": dict(kinds), "disagreements": conflicts, "topics": [t for t, _ in topics.most_common(3)],
            "entities": [e for e, c in ents.most_common(8)], "undated": len(members) - len(dated), "note": SOURCE_NOTE, "method": METHOD}


def timeline(members: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Articles in time order. Undated ones are listed last and say so; the first dated one is marked."""
    def key(a: Dict[str, Any]):
        t = parse_date(a["published"]) if a.get("published") else None
        return (0, t.timestamp(), a["id"]) if t else (1, 0.0, a["id"])

    out = []
    first_marked = False
    for a in sorted(members, key=key):
        st = (a.get("derived") or {}).get("statement") or {}
        dated = bool(a.get("published") and parse_date(a["published"]))
        out.append({"article_id": a["id"], "time": a.get("published") if dated else None, "publisher": publisher_of(a), "headline": a["title"], "url": a.get("url"), "kind": st.get("kind", "stated"),
                    "tone": ((a.get("derived") or {}).get("tone") or {}).get("label"), "first_reported": bool(dated and not first_marked)})
        first_marked = first_marked or dated
    return out


def compare_outlets(members: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Side by side: what each outlet said first, in what tone and with which numbers. Differences are listed; none is called right."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for a in members:
        by.setdefault(publisher_of(a), []).append(a)
    rows = []
    first_time: Optional[datetime] = None
    for name, items in by.items():
        items.sort(key=lambda a: (parse_date(a["published"]).timestamp() if a.get("published") and parse_date(a["published"]) else 9e12, a["id"]))
        head = items[0]
        t = parse_date(head["published"]) if head.get("published") else None
        if t and (first_time is None or t < first_time):
            first_time = t
        text = f"{head['title']} {head.get('lead') or head.get('summary') or ''}"
        rows.append({"outlet": name, "articles": len(items), "headline": head["title"], "lead": head.get("lead") or head.get("summary") or "", "url": head.get("url"), "published": head.get("published"),
                     "kind": ((head.get("derived") or {}).get("statement") or {}).get("kind", "stated"), "tone": ((head.get("derived") or {}).get("tone") or {}).get("label"),
                     "numbers": sorted(numbers_in(text))[:8], "_t": t})
    allnums = Counter(n for r in rows for n in set(r["numbers"]))
    for r in rows:
        r["hours_after_first"] = round((r["_t"] - first_time).total_seconds() / 3600, 1) if r["_t"] and first_time else None
        r["only_this_outlet_gives"] = sorted(n for n in r["numbers"] if allnums[n] == 1)[:5] if len(rows) > 1 else []
        del r["_t"]
    rows.sort(key=lambda r: (r["hours_after_first"] is None, r["hours_after_first"] or 0, r["outlet"]))
    tones = sorted({r["tone"] for r in rows if r["tone"]})
    return {"outlets": rows, "tones": tones, "tone_differs": len(tones) > 1, "note": "Differences in wording, tone and numbers are shown side by side. None of them is judged right or wrong here, and tone is not truth."}


def related(event: Dict[str, Any], others: Sequence[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    """Other events that share names or topics with this one (and the shared names, as the reason)."""
    ents, topics = set(event.get("entities") or []), set(event.get("topics") or [])
    out = []
    for o in others:
        if o["id"] == event["id"]:
            continue
        shared = sorted(ents & set(o.get("entities") or []))
        sim = _jaccard(ents, set(o.get("entities") or []))
        shared_topics = sorted(topics & set(o.get("topics") or []))
        if len(shared) >= 2 or (shared and shared_topics and sim >= 0.15):
            out.append({"event_id": o["id"], "label": o["label"], "status": o["status"], "shared_entities": shared[:6], "shared_topics": shared_topics, "similarity": round(sim, 3)})
    out.sort(key=lambda r: (-r["similarity"], r["event_id"]))
    return out[:limit]
