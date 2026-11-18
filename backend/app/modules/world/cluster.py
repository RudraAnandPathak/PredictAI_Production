"""Event identification: which articles are about the same thing.

Articles are compared by TF-IDF cosine similarity of headline plus first sentence (headline words count double), helped a little by shared names, and only when they were published within a
few days of each other. Each article joins the group it is most similar to if the average similarity to that group's closest members is high enough, otherwise it starts a new group. The
result is deterministic. It is a heuristic: it will sometimes keep one story in two groups or join two similar stories (two different floods, say), which is why every event lists its
articles so a person can check."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..research.sources import parse_date
from ..textkit import tokenize

METHOD = "tfidf-agglomerative/1"
DEFAULTS = {"threshold": 0.30, "window_days": 5.0, "max_articles": 2000, "entity_bonus": 0.10, "common_df": 0.35}


def _when(a: Dict[str, Any]) -> Optional[datetime]:
    return parse_date(a.get("published")) if a.get("published") else None


def vectorise(articles: Sequence[Dict[str, Any]], common_df: float = DEFAULTS["common_df"]) -> Dict[str, Dict[str, float]]:
    docs: Dict[str, Counter] = {}
    for a in articles:
        c: Counter = Counter()
        for t in tokenize(a.get("title") or ""):
            c[t] += 2
        for t in tokenize(a.get("lead") or a.get("summary") or ""):
            c[t] += 1
        docs[a["id"]] = c
    n = max(1, len(docs))
    df: Counter = Counter()
    for c in docs.values():
        df.update(c.keys())
    vecs: Dict[str, Dict[str, float]] = {}
    for aid, c in docs.items():
        v = {t: (1 + math.log(f)) * (math.log((n + 1) / (df[t] + 1)) + 1) for t, f in c.items() if n < 6 or df[t] / n <= common_df}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[aid] = {t: x / norm for t, x in v.items()}
    return vecs


def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def similarity(a: Dict[str, Any], b: Dict[str, Any], va: Dict[str, float], vb: Dict[str, float], *, window_days: float, entity_bonus: float) -> float:
    ta, tb = _when(a), _when(b)
    if ta and tb and abs((ta - tb).total_seconds()) > window_days * 86400:
        return 0.0
    s = cosine(va, vb)
    shared = set(a.get("entities") or []) & set(b.get("entities") or [])
    if s > 0.05 and len(shared) >= 2:
        s += entity_bonus
    return min(1.0, s)


def cluster(articles: Sequence[Dict[str, Any]], *, threshold: float = DEFAULTS["threshold"], window_days: float = DEFAULTS["window_days"], max_articles: int = DEFAULTS["max_articles"],
            entity_bonus: float = DEFAULTS["entity_bonus"]) -> Dict[str, Any]:
    """{groups: [[article ids]], skipped: n, method}. Articles need id, title, lead/summary, published, entities."""
    far = datetime.max.replace(tzinfo=None)

    def key(a: Dict[str, Any]) -> Tuple[Any, str]:
        t = _when(a)
        return (t.replace(tzinfo=None) if t else far, a["id"])

    ordered = sorted(articles, key=key)
    skipped = 0
    if len(ordered) > max_articles:
        skipped = len(ordered) - max_articles
        ordered = ordered[-max_articles:]
    vecs = vectorise(ordered)
    by_id = {a["id"]: a for a in ordered}
    groups: List[List[str]] = []
    group_of: Dict[str, int] = {}
    postings: Dict[str, List[str]] = defaultdict(list)
    for a in ordered:
        scores: Dict[int, List[float]] = defaultdict(list)
        seen = set()
        for t in vecs[a["id"]]:
            for other in postings.get(t, ()):
                if other in seen:
                    continue
                seen.add(other)
                s = similarity(a, by_id[other], vecs[a["id"]], vecs[other], window_days=window_days, entity_bonus=entity_bonus)
                if s > 0:
                    scores[group_of[other]].append(s)
        best, best_score = None, 0.0
        for gi, sims in scores.items():
            members = len(groups[gi])
            top = sorted(sims, reverse=True)[:3]
            score = sum(top) / min(3, members)                                                   # members that share no word with the article count as zero
            if score > best_score or (score == best_score and best is not None and gi < best):
                best, best_score = gi, score
        if best is not None and best_score >= threshold:
            groups[best].append(a["id"])
            group_of[a["id"]] = best
        else:
            groups.append([a["id"]])
            group_of[a["id"]] = len(groups) - 1
        for t in vecs[a["id"]]:
            postings[t].append(a["id"])
    return {"groups": groups, "skipped": skipped, "method": METHOD, "parameters": {"threshold": threshold, "window_days": window_days, "entity_bonus": entity_bonus}}


def centrality(members: Sequence[Dict[str, Any]], vecs: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, float]:
    """Mean similarity of each article to the others in its group (used to pick a representative headline)."""
    vecs = vecs or vectorise(members)
    out: Dict[str, float] = {}
    for a in members:
        others = [cosine(vecs[a["id"]], vecs[b["id"]]) for b in members if b["id"] != a["id"]]
        out[a["id"]] = sum(others) / len(others) if others else 1.0
    return out
