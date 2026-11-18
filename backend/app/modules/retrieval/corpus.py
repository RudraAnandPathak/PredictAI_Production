"""In-memory search structures for one or more document indexes. Built from the stored passages; nothing here is persisted except the passages themselves."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..textkit import stem, tokenize
from .chunker import Chunk
from .lexical import BM25
from .semantic import Semantic, dense_scores, normalise_rows, rank, rrf

MODES = ("keyword", "semantic", "dense", "hybrid")


@dataclass
class DocIndex:
    asset_id: str
    version_id: str
    name: str
    extraction_id: Optional[str]
    source_checksum: Optional[str]
    page_count: Optional[int]
    page_estimated: bool
    chunks: List[Chunk]
    warnings: List[str] = field(default_factory=list)
    built_at: str = ""
    sha256: str = ""
    dense: Optional[np.ndarray] = None
    dense_model: Optional[str] = None


class Corpus:
    def __init__(self, docs: Sequence[DocIndex]) -> None:
        self.docs = list(docs)
        self.rows: List[Tuple[int, Chunk]] = [(di, c) for di, d in enumerate(self.docs) for c in d.chunks]
        self.tokens = [tokenize(c.text) for _, c in self.rows]
        self.token_sets = [set(t) for t in self.tokens]
        self.bm25 = BM25(self.tokens)
        self._semantic: Optional[Semantic] = None
        models = {d.dense_model for d in self.docs}
        self.dense_model = next(iter(models)) if len(models) == 1 and all(d.dense is not None for d in self.docs) else None
        self.dense = np.vstack([d.dense for d in self.docs]) if self.dense_model else None

    @property
    def size(self) -> int:
        return len(self.rows)

    def semantic(self) -> Semantic:
        if self._semantic is None:
            self._semantic = Semantic(self.tokens)
        return self._semantic

    def allowed(self, filters: Optional[Dict[str, Any]]) -> np.ndarray:
        mask = np.ones(self.size, dtype=bool)
        f = filters or {}
        pages, section, min_words = f.get("pages"), (f.get("section") or "").lower(), f.get("min_words")
        for i, (_, c) in enumerate(self.rows):
            if pages and not (pages[0] <= c.page_start <= pages[1]):
                mask[i] = False
            elif section and section not in (c.section or "").lower():
                mask[i] = False
            elif min_words and c.words < min_words:
                mask[i] = False
        return mask

    def search(self, query: str, *, mode: str = "hybrid", k: int = 8, filters: Optional[Dict[str, Any]] = None, query_vector: Optional[Sequence[float]] = None) -> Dict[str, Any]:
        q_stems = tokenize(query)
        q_words = [w for w in tokenize(query, stemming=False)]
        notes: List[str] = []
        allowed = self.allowed(filters)
        depth = max(50, k * 5)
        ranked: Dict[str, List[int]] = {}
        raw: Dict[str, np.ndarray] = {}
        if not q_stems and query_vector is None:
            return {"hits": [], "used": [], "notes": ["The question has no searchable words."], "semantic_method": None, "candidates": int(allowed.sum())}
        want = {"keyword": ["keyword"], "semantic": ["semantic"], "dense": ["dense"], "hybrid": ["keyword", "semantic", "dense"]}[mode]
        if "keyword" in want:
            raw["keyword"] = self.bm25.scores(q_stems)
        if "semantic" in want:
            sem = self.semantic()
            if sem.ok:
                raw["semantic"] = sem.scores(q_stems)
            else:
                notes.append("Not enough text for the meaning-based search; keyword results are shown.")
        if "dense" in want:
            if self.dense is not None and query_vector is not None:
                raw["dense"] = dense_scores(self.dense, query_vector)
            elif mode == "dense":
                notes.append("No embeddings are available for these documents, so the embedding search could not run.")
        for name, scores in raw.items():
            ranked[name] = rank(scores, allowed, depth)
        used = [m for m in ("keyword", "semantic", "dense") if m in ranked]
        if mode == "hybrid":
            fused = rrf([ranked[m] for m in used]) if used else []
        else:
            m = used[0] if used else None
            fused = [(i, float(raw[m][i])) for i in ranked[m]] if m else []
        hits = []
        position = {m: {i: r for r, i in enumerate(ranked[m], start=1)} for m in used}
        for place, (i, score) in enumerate(fused[:k], start=1):
            di, chunk = self.rows[i]
            doc = self.docs[di]
            methods = {m: {"rank": position[m][i], "score": round(float(raw[m][i]), 4)} for m in used if i in position[m]}
            matched = sorted({w for w in q_words if stem(w) in self.token_sets[i]})
            hits.append({"rank": place, "chunk_id": chunk.id, "asset_id": doc.asset_id, "version_id": doc.version_id, "document": doc.name, "page_start": chunk.page_start, "page_end": chunk.page_end,
                         "page_estimated": doc.page_estimated, "section": chunk.section, "text": chunk.text, "score": round(float(score), 5), "methods": methods, "matched_terms": matched,
                         "citation": citation_for(doc, chunk)})
        sem = self._semantic
        return {"hits": hits, "used": used, "notes": notes, "semantic_method": (sem.method if sem is not None and sem.ok and "semantic" in used else None), "candidates": int(allowed.sum()),
                "dense_model": self.dense_model if "dense" in used else None}


def citation_for(doc: DocIndex, chunk: Chunk) -> Dict[str, Any]:
    return {"asset_id": doc.asset_id, "version_id": doc.version_id, "document": doc.name, "chunk_id": chunk.id, "page_start": chunk.page_start, "page_end": chunk.page_end,
            "page_estimated": doc.page_estimated, "section": chunk.section, "start": chunk.start, "end": chunk.end, "quote_sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()[:16]}


class LruCache:
    def __init__(self, capacity: int) -> None:
        self.capacity, self._d, self._lock = capacity, OrderedDict(), threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CORPUS_CACHE = LruCache(8)
