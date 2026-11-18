"""'Semantic' scoring without a paid service or a downloaded model.

LSA (latent semantic analysis): TF-IDF vectors of the passages are reduced with a truncated SVD (keeping the dimensions before the biggest drop in the singular values), so words that tend to occur together in THESE documents end up close
(for example 'rainfall' and 'flood'). It is fitted on the documents being searched, deterministic (fixed seed) and runs locally. It is not a pretrained language model;
for very small collections it falls back to plain TF-IDF cosine, and the result says which one was used. Dense embeddings from a configured provider are an optional upgrade."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

SEED = 7


def elbow(singular_values, lowest: int = 2) -> int:
    """Number of latent dimensions to keep: the position of the largest drop between neighbouring singular values (a scree-plot elbow), never fewer than `lowest`.
    LSA only links related words (for example 'inundation' and 'flooding') when it keeps about as many dimensions as there are real themes in the text; keeping more
    just reproduces keyword matching. A heuristic: real documents have a gentler spectrum than a clean example, so the evaluation report is the check."""
    best_ratio, best_k = 0.0, lowest
    for i in range(lowest, len(singular_values)):
        ratio = float(singular_values[i - 1]) / max(float(singular_values[i]), 1e-12)
        if ratio > best_ratio:
            best_ratio, best_k = ratio, i
    return best_k


class Semantic:
    def __init__(self, token_lists: Sequence[Sequence[str]]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize
        self.method = "tfidf"
        self.ok = False
        docs = [list(t) for t in token_lists]
        if len(docs) < 2 or not any(docs):
            return
        self.vec = TfidfVectorizer(analyzer=lambda x: x, sublinear_tf=True, norm="l2")
        try:
            tfidf = self.vec.fit_transform(docs)
        except ValueError:
            return
        self.ok = True
        n_feat = tfidf.shape[1]
        kmax = min(30, len(docs) - 1, n_feat - 1)
        self.svd: Optional[TruncatedSVD] = None
        self.k = 0
        if len(docs) >= 12 and kmax >= 3:
            self.svd = TruncatedSVD(n_components=kmax, random_state=SEED, algorithm="randomized", n_iter=7)
            reduced = self.svd.fit_transform(tfidf)
            self.k = elbow(self.svd.singular_values_)
            self.matrix = normalize(reduced[:, : self.k])
            self.method = "lsa"
        else:
            self.matrix = tfidf
        self._normalize = normalize

    def scores(self, query_terms: Sequence[str]) -> np.ndarray:
        n = self.matrix.shape[0] if self.ok else 0
        if not self.ok or not query_terms:
            return np.zeros(n)
        q = self.vec.transform([list(query_terms)])
        if q.nnz == 0:
            return np.zeros(n)
        if self.svd is not None:
            qv = self._normalize(self.svd.transform(q)[:, : self.k])
            return np.asarray(self.matrix @ qv[0]).ravel()
        return np.asarray((self.matrix @ q.T).todense()).ravel()


def dense_scores(matrix: np.ndarray, query_vec: Sequence[float]) -> np.ndarray:
    q = np.asarray(query_vec, dtype=float)
    norm = np.linalg.norm(q)
    return matrix @ (q / norm) if norm else np.zeros(matrix.shape[0])


def normalise_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def rank(scores: np.ndarray, allowed: np.ndarray, limit: int, min_score: float = 0.0) -> List[int]:
    """Indices of the best `limit` allowed passages, best first; ties broken by position so the order is deterministic."""
    idx = np.where(allowed & (scores > min_score))[0]
    order = sorted(idx.tolist(), key=lambda i: (-float(scores[i]), i))
    return order[:limit]


def rrf(rankings: Sequence[Sequence[int]], k: int = 60) -> List[Tuple[int, float]]:
    """Reciprocal rank fusion (Cormack et al. 2009): score(d) = sum over rankers of 1 / (k + rank)."""
    fused: dict = {}
    for ranking in rankings:
        for pos, i in enumerate(ranking, start=1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (k + pos)
    return sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
