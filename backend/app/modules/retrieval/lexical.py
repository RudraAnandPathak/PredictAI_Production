"""Okapi BM25 (Robertson-Sparck Jones). idf = ln(1 + (N - n + 0.5) / (n + 0.5)); score = sum idf * tf (k1 + 1) / (tf + k1 (1 - b + b dl / avgdl)). k1 = 1.5, b = 0.75."""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence

import numpy as np

K1, B = 1.5, 0.75


class BM25:
    def __init__(self, docs: Sequence[Sequence[str]], k1: float = K1, b: float = B) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.lengths = np.array([len(d) for d in docs], dtype=float)
        self.avgdl = float(self.lengths.mean()) if self.n and self.lengths.sum() else 1.0
        self.postings: Dict[str, List[tuple]] = {}
        for i, d in enumerate(docs):
            for term, tf in Counter(d).items():
                self.postings.setdefault(term, []).append((i, tf))
        self.df = {t: len(p) for t, p in self.postings.items()}

    def idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.n - n + 0.5) / (n + 0.5))

    def scores(self, query_terms: Sequence[str]) -> np.ndarray:
        out = np.zeros(self.n)
        for term in set(query_terms):
            post = self.postings.get(term)
            if not post:
                continue
            idf = self.idf(term)
            for i, tf in post:
                out[i] += idf * tf * (self.k1 + 1.0) / (tf + self.k1 * (1.0 - self.b + self.b * self.lengths[i] / self.avgdl))
        return out
