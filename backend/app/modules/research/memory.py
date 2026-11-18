"""Research memory: finding earlier research again and comparing two runs.

Search is a plain keyword ranking over each session's question, conclusions and the person's own notes (BM25, no model, no network). Comparison is a set difference on
what two bundles contain; it reports what changed and never says which run is 'right'."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from ..retrieval.lexical import BM25
from ..textkit import sentences, tokenize

MAX_SEARCH_TEXT = 20_000


def build_search_text(question: str, title: str, conclusions: Sequence[str], notes: Sequence[str]) -> str:
    text = " \n".join([title, question, *conclusions, *notes])
    return text[:MAX_SEARCH_TEXT]


def search(rows: Sequence[Mapping[str, Any]], query: str, limit: int = 20) -> List[Dict[str, Any]]:
    terms = tokenize(query)
    if not terms or not rows:
        return []
    docs = [tokenize(r["search_text"] or f"{r['title']} {r['question']}") for r in rows]
    scores = BM25(docs).scores(terms)
    qset = set(terms)
    hits = []
    for r, sc in zip(rows, scores):
        if sc <= 0:
            continue
        snippet = next((s for s in sentences(r["search_text"] or "", max_len=300) if qset & set(tokenize(s))), r["question"])
        hits.append({"id": r["id"], "title": r["title"], "question": r["question"], "level": r["level"], "status": r["status"], "saved": bool(r["saved"]), "updated_at": r["updated_at"],
                     "latest_bundle_id": r["latest_bundle_id"], "score": round(float(sc), 4), "snippet": snippet[:300]})
    hits.sort(key=lambda h: (-h["score"], h["updated_at"]), reverse=False)
    return hits[:limit]


def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> Dict[str, Any]:
    """`a` is the earlier bundle, `b` the later one (the caller orders them by time)."""
    sa, sb = {s["key"]: s for s in a["sources"]}, {s["key"]: s for s in b["sources"]}
    added = [_src(sb[k]) for k in sb if k not in sa]
    removed = [_src(sa[k]) for k in sa if k not in sb]
    changed = [{**_src(sb[k]), "note": "The page content is different from when it was read in the earlier run."} for k in sb
               if k in sa and sa[k].get("content_hash") and sb[k].get("content_hash") and sa[k]["content_hash"] != sb[k]["content_hash"]]
    unreadable_now = [_src(sb[k]) for k in sb if k in sa and sa[k].get("status") == "ok" and sb[k].get("status") != "ok"]
    ca, cb = {c["text"]: c for c in a["conclusions"]}, {c["text"]: c for c in b["conclusions"]}
    same = [{"text": t, "earlier": ca[t]["confidence"], "later": cb[t]["confidence"], "changed_strength": ca[t]["confidence"] != cb[t]["confidence"]} for t in cb if t in ca]
    new = [{"id": cb[t]["id"], "text": t, "confidence": cb[t]["confidence"], "kind": cb[t]["kind"]} for t in cb if t not in ca]
    gone = [{"id": ca[t]["id"], "text": t, "confidence": ca[t]["confidence"], "kind": ca[t]["kind"]} for t in ca if t not in cb]
    out = {"schema": "predictai.research_comparison/1", "earlier": _head(a), "later": _head(b),
           "sources": {"added": added, "removed": removed, "content_changed": changed, "no_longer_readable": unreadable_now, "shared": len(set(sa) & set(sb))},
           "conclusions": {"unchanged": [s for s in same if not s["changed_strength"]], "strength_changed": [s for s in same if s["changed_strength"]], "new": new, "no_longer_present": gone},
           "conflicts": {"earlier": len(a["conflicts"]), "later": len(b["conflicts"])},
           "quality": {"earlier": a["quality"]["verdict"], "later": b["quality"]["verdict"]},
           "same_question": a["question"].strip().lower() == b["question"].strip().lower(),
           "note": "This lists what is different between the two runs. It does not say which is more correct: sources change over time and a different search can reach different pages."}
    return out


def _src(s: Mapping[str, Any]) -> Dict[str, Any]:
    return {"id": s["id"], "title": s["title"], "url": s.get("url"), "origin": s["origin"]}


def _head(b: Mapping[str, Any]) -> Dict[str, Any]:
    return {"bundle_id": b["id"], "number": b["number"], "created_at": b["created_at"], "status": b["status"], "level": b["level"], "question": b["question"], "used_web": b["used_web"],
            "sources": len(b["sources"]), "conclusions": len(b["conclusions"])}
