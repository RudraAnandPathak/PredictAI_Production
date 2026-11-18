"""Check model-written text against the evidence it was given. A sentence is kept only when its citations exist, its numbers appear in the cited passages, it is lexically
supported by them, and any causal wording is present in them too. Dropped sentences are returned with the reason, so a rejected answer is visible rather than silent.
Nothing is repaired or invented: a sentence is kept as written or removed."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

from ..textkit import numbers_in, sentences, tokenize

MARKER = re.compile(r"\[S(\d+)\]")
CAUSAL = re.compile(r"\b(causes?|caused|causing|because of|leads? to|led to|results? in|resulted in|due to|responsible for|drives?|driven by)\b", re.I)


def check_generated(text: str, evidence: Mapping[str, str], *, min_support: float = 0.5) -> Dict[str, Any]:
    """`evidence` maps a source id such as 'S1' to its passage text."""
    ids = {k: v for k, v in evidence.items()}
    pool = " ".join(ids.values())
    pool_terms = set(tokenize(pool))
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for sent in sentences(text, max_len=500):
        cited = [f"S{m}" for m in MARKER.findall(sent)]
        body = MARKER.sub("", sent).strip()
        if len(tokenize(body)) < 3:
            continue
        unknown = [c for c in cited if c not in ids]
        if unknown:
            dropped.append({"text": body, "reason": f"cites a source that does not exist ({', '.join(unknown)})"})
            continue
        support_text = " ".join(ids[c] for c in cited) if cited else pool
        terms = [t for t in tokenize(body)]
        have = set(tokenize(support_text))
        overlap = (sum(1 for t in terms if t in have) / len(terms)) if terms else 0.0
        if overlap < min_support:
            dropped.append({"text": body, "reason": "not supported by the cited passages" if cited else "not supported by the available passages"})
            continue
        stray = sorted(n for n in numbers_in(body) if n not in numbers_in(support_text))
        if stray:
            dropped.append({"text": body, "reason": f"contains a number that is not in the evidence ({', '.join(stray[:3])})"})
            continue
        if CAUSAL.search(body) and not CAUSAL.search(support_text):
            dropped.append({"text": body, "reason": "states a cause that the evidence does not state"})
            continue
        kept.append({"text": body, "sources": cited, "uncited": not cited, "support": round(overlap, 2)})
    return {"kept": kept, "dropped": dropped, "clean": not dropped and bool(kept)}
