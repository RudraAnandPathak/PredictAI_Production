"""Extractive source intelligence: a summary, keywords, statistics and key claims computed from the text itself. Every item carries the chunk it came from, and nothing is generated, so
nothing can be invented. This is a baseline; a model-written summary (when the owner has enabled a provider and the workspace policy allows it) is a later, separate feature."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from .chunker import Chunk

STOP = set("""a about above after again against all also am an and any are as at be because been before being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its just may me might more most must my no nor not of off on once only or other our out over own same she should so some
such than that the their them then there these they this those through to too under until up us very was we were what when where which while who whom why will with would you your et al fig figure
table also et using used use based within among however thus therefore""".split())
CLAIM_CUES = re.compile(r"\b(we (find|found|show|showed|propose|demonstrate|conclude|observe|report)|results? (show|indicate|suggest)|(this|our) (study|paper|analysis|work) (shows?|finds?|suggests?|demonstrates?)|"
                        r"significantly|increased|decreased|improved|reduced|outperform\w*|associated with|correlat\w+|in conclusion|we conclude)\b", re.I)
STAT = re.compile(r"(?<![\w.])(?:[<>≈~=]\s*)?\d+(?:[.,]\d+)?\s?(?:%|percent|per cent|x|×|fold|million|billion|thousand|ms|s|kg|km|m|mm|cm|hz|GB|MB)?|p\s?[<=>]\s?0?\.\d+|n\s?=\s?\d+", re.I)


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z][a-z\-']{2,}", text.lower()) if t not in STOP]


def keywords(chunks: Sequence[Chunk], top: int = 12) -> List[str]:
    tf, df = Counter(), Counter()
    for c in chunks:
        toks = tokens(c.text)
        tf.update(toks)
        df.update(set(toks))
    n = max(1, len(chunks))
    scored = {t: f * (1 + math.log((n + 1) / (1 + df[t]))) for t, f in tf.items() if f >= 2}
    return [t for t, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def _sents(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", text.replace("\n", " ")) if 30 <= len(s.strip()) <= 400]


def build(chunks: Sequence[Chunk], abstract: Optional[str]) -> Dict[str, Any]:
    if not chunks:
        return {"summary": [], "keywords": [], "statistics": [], "claims": [], "method": "extractive"}
    kw = keywords(chunks)
    kwset = set(kw)
    scored = []
    stats: List[Dict[str, Any]] = []
    claims: List[Dict[str, Any]] = []
    seen = set()
    for c in chunks:
        if c.section and re.search(r"reference|bibliograph", c.section, re.I):
            continue
        for s in _sents(c.text):
            key = s.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            toks = set(tokens(s))
            score = len(toks & kwset) / (1 + math.log(1 + len(toks)))
            scored.append((score, c.ordinal, s, c))
            if STAT.search(s) and re.search(r"\d", s) and len(stats) < 12 and re.search(r"[a-zA-Z]{3}", s):
                stats.append({"text": s, "chunk_ordinal": c.ordinal, "locator": c.locator})
            if CLAIM_CUES.search(s) and len(claims) < 10:
                claims.append({"text": s, "chunk_ordinal": c.ordinal, "locator": c.locator})
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = sorted(scored[:5], key=lambda t: t[1])
    summary = [{"text": s, "chunk_ordinal": c.ordinal, "locator": c.locator} for _, _, s, c in top]
    return {"summary": summary, "keywords": kw, "statistics": stats, "claims": claims, "abstract_present": bool(abstract), "method": "extractive"}
