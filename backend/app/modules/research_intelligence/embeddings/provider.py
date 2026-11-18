"""Embeddings. Two kinds exist and they are labelled differently everywhere they appear:

* `builtin-hash-384` (provider "builtin"): a local hashed bag of words and bigrams. It is LEXICAL: it matches shared vocabulary, not meaning, so synonyms are NOT found. It needs no
  model, no network and no cost, and it is always available.
* A model embedding from the platform's provider router (e.g. Ollama `nomic-embed-text`, or an OpenAI-compatible service the owner enabled). Only these are SEMANTIC.

Research material is private by default (data class `private`): it reaches a provider other than a local one only when the workspace policy allows external use, and every provider the
router skips is reported back, never hidden."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from ...providers.base import Consent

BUILTIN_MODEL, BUILTIN_PROVIDER, BUILTIN_DIMS = "builtin-hash-384", "builtin", 384
_TOK = re.compile(r"[a-z0-9][a-z0-9\-']*")


def hash_embed(text: str) -> List[float]:
    toks = _TOK.findall(text.lower())
    v = np.zeros(BUILTIN_DIMS, dtype=np.float32)
    grams = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "big")
        v[h % BUILTIN_DIMS] += 1.0 if (h >> 63) & 1 else -1.0
    norm = float(np.linalg.norm(v))
    return (v / norm).tolist() if norm else v.tolist()


@dataclass
class EmbedResult:
    vectors: List[List[float]]
    model: str
    provider: str
    semantic: bool
    units: int = 0
    skipped: List[Dict[str, str]] = field(default_factory=list)


class Embedder:
    """`router_factory` returns a platform Router (or None). `allow_external` is the workspace policy; the router itself enforces local-only for private data otherwise."""

    def __init__(self, router_factory: Optional[Callable[[], Any]], owner_id: str, *, allow_external: bool = False, batch: int = 32) -> None:
        self.router_factory, self.owner_id, self.allow_external, self.batch = router_factory, owner_id, allow_external, batch

    def builtin(self, texts: Sequence[str]) -> EmbedResult:
        return EmbedResult([hash_embed(t) for t in texts], BUILTIN_MODEL, BUILTIN_PROVIDER, False)

    def provider(self, texts: Sequence[str]) -> Optional[EmbedResult]:
        """Embeddings from a configured model, or None when no provider may or can embed (the caller keeps the builtin path)."""
        if self.router_factory is None or not texts:
            return None
        router = self.router_factory()
        vectors: List[List[float]] = []
        model = provider = None
        skipped: List[Dict[str, str]] = []
        units = 0
        for i in range(0, len(texts), self.batch):
            out = router.embed(self.owner_id, None, list(texts[i:i + self.batch]), data_class="private", consent=Consent(external=self.allow_external))
            skipped = out.skipped
            if not out.ok or not out.vectors or len(out.vectors) != len(texts[i:i + self.batch]):
                return None
            if model is not None and (out.model != model or out.provider != provider):
                return None                                      # a provider switch mid-document would mix vector spaces; the whole attempt is dropped
            model, provider = out.model, out.provider
            vectors.extend(out.vectors)
            units += out.units_in
        dims = {len(v) for v in vectors}
        if len(dims) != 1 or 0 in dims:
            return None
        return EmbedResult(vectors, f"{provider}:{model}", provider or "", True, units, skipped)


def cosine_top(matrix: np.ndarray, query: np.ndarray, k: int) -> List[tuple]:
    if matrix.size == 0:
        return []
    qn = float(np.linalg.norm(query))
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    scores = (matrix @ query) / (norms * (qn or 1.0))
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k] if k < len(scores) else np.arange(len(scores))
    idx = idx[np.argsort(-scores[idx])]
    return [(int(i), float(scores[i])) for i in idx if math.isfinite(float(scores[i]))]
