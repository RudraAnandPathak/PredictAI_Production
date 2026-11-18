"""Hybrid retrieval: FTS5 keyword search (BM25) + vector search, fused with reciprocal rank fusion, then a transparent heuristic rerank and context selection.

Isolation: every query is bounded by workspace_id AND owner_user_id, and only chunks of `ready`, `included` sources are visible (plus the optional per-conversation scope).
Honesty: results say which channels contributed. The `score` is a ranking value, NOT a probability or a confidence, and is never shown as one."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..embeddings.provider import BUILTIN_MODEL, Embedder
from ..ingestion.intelligence import tokens
from .vectorstore import VectorStore

RRF_K = 60


@dataclass
class Hit:
    chunk_seq: int
    chunk_id: str
    source_id: str
    text: str
    section: Optional[str]
    page: Optional[int]
    locator: str
    flagged: bool
    ordinal: int
    channels: List[str] = field(default_factory=list)
    keyword_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_model: Optional[str] = None
    rrf: float = 0.0
    score: float = 0.0
    coverage: float = 0.0

    def public(self) -> Dict[str, Any]:
        return {"chunk_id": self.chunk_id, "source_id": self.source_id, "text": self.text, "section": self.section, "page": self.page, "locator": self.locator, "flagged": self.flagged,
                "channels": self.channels, "score": round(self.score, 4), "term_coverage": round(self.coverage, 3), "vector_model": self.vector_model}


def fts_query(question: str) -> Optional[str]:
    terms = []
    for t in tokens(question):
        t = re.sub(r"[^a-z0-9]", "", t)
        if len(t) >= 3 and t not in terms:
            terms.append(t)
    return " OR ".join(f'"{t}"' for t in terms[:24]) or None


class Retriever:
    def __init__(self, conn: sqlite3.Connection, store: VectorStore, embedder: Embedder) -> None:
        self.conn, self.store, self.embedder = conn, store, embedder

    # -------------------------------------------------------------- scope
    def visible_sources(self, workspace_id: str, owner_id: str, scope: Optional[Dict[str, str]] = None, only: Optional[Sequence[str]] = None) -> List[str]:
        rows = self.conn.execute("SELECT id FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ? AND status = 'ready' AND included = 1", (workspace_id, owner_id)).fetchall()
        ids = [r["id"] for r in rows]
        if scope:
            inc = [s for s, m in scope.items() if m == "include"]
            exc = {s for s, m in scope.items() if m == "exclude"}
            ids = [i for i in ids if i not in exc]
            if inc:
                ids = [i for i in ids if i in set(inc)]
        if only is not None:
            ids = [i for i in ids if i in set(only)]
        return ids

    def _hydrate(self, seqs: Sequence[int], workspace_id: str, owner_id: str) -> Dict[int, sqlite3.Row]:
        if not seqs:
            return {}
        rows = self.conn.execute(f"SELECT * FROM ri_chunks WHERE seq IN ({','.join('?' * len(seqs))}) AND workspace_id = ? AND owner_user_id = ?", [*seqs, workspace_id, owner_id]).fetchall()
        return {r["seq"]: r for r in rows}

    # -------------------------------------------------------------- channels
    def keyword(self, question: str, workspace_id: str, owner_id: str, source_ids: Sequence[str], k: int) -> List[int]:
        q = fts_query(question)
        if not q or not source_ids:
            return []
        try:
            rows = self.conn.execute(
                f"SELECT c.seq FROM ri_chunks_fts f JOIN ri_chunks c ON c.seq = f.rowid WHERE ri_chunks_fts MATCH ? AND c.workspace_id = ? AND c.owner_user_id = ? "
                f"AND c.source_id IN ({','.join('?' * len(source_ids))}) ORDER BY bm25(ri_chunks_fts) LIMIT ?", [q, workspace_id, owner_id, *source_ids, k]).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["seq"] for r in rows]

    def vector(self, question: str, workspace_id: str, owner_id: str, source_ids: Sequence[str], k: int) -> Dict[str, Any]:
        """Try the configured embedding model first when vectors of that model exist; otherwise the builtin lexical vectors. Reports which was used."""
        have = set(self.store.models(workspace_id=workspace_id, owner_id=owner_id))
        used: Optional[str] = None
        qvec: Optional[List[float]] = None
        semantic = False
        provider_models = [m for m in have if m != BUILTIN_MODEL]
        if provider_models:
            res = self.embedder.provider([question])
            if res is not None and res.model in have:
                used, qvec, semantic = res.model, res.vectors[0], True
        if qvec is None and BUILTIN_MODEL in have:
            used, qvec = BUILTIN_MODEL, self.embedder.builtin([question]).vectors[0]
        if qvec is None or used is None:
            return {"hits": [], "model": None, "semantic": False}
        try:
            hits = self.store.search(workspace_id=workspace_id, owner_id=owner_id, model=used, vector=qvec, k=k, source_ids=list(source_ids))
        except RuntimeError:
            return {"hits": [], "model": used, "semantic": semantic, "error": "VECTOR_STORE_UNAVAILABLE"}
        return {"hits": hits, "model": used, "semantic": semantic}

    # -------------------------------------------------------------- hybrid
    def search(self, question: str, workspace_id: str, owner_id: str, *, scope: Optional[Dict[str, str]] = None, only: Optional[Sequence[str]] = None, k: int = 12,
               mode: str = "hybrid") -> Dict[str, Any]:
        sources = self.visible_sources(workspace_id, owner_id, scope, only)
        info: Dict[str, Any] = {"mode": mode, "sources_searched": len(sources), "semantic": False, "vector_model": None, "notes": []}
        if not sources:
            return {"hits": [], "info": info}
        pool = max(k * 3, 30)
        kw = self.keyword(question, workspace_id, owner_id, sources, pool) if mode in ("hybrid", "keyword") else []
        vec = {"hits": [], "model": None, "semantic": False}
        if mode in ("hybrid", "semantic"):
            vec = self.vector(question, workspace_id, owner_id, sources, pool)
            if vec.get("error"):
                info["notes"].append("The vector store could not be reached; keyword results only.")
        info.update({"semantic": bool(vec["semantic"]), "vector_model": vec["model"]})
        if vec["model"] == BUILTIN_MODEL:
            info["notes"].append("Vector matching used the built-in lexical embedding (shared words, not meaning). Enable an embedding model in AI providers for semantic matching.")
        fused: Dict[int, Hit] = {}
        rows = self._hydrate(list(dict.fromkeys([*kw, *[s for s, _ in vec["hits"]]])), workspace_id, owner_id)
        for rank, seq in enumerate(kw, 1):
            if seq in rows:
                h = fused.setdefault(seq, self._hit(rows[seq]))
                h.keyword_rank = rank; h.rrf += 1.0 / (RRF_K + rank)
                if "keyword" not in h.channels:
                    h.channels.append("keyword")
        for rank, (seq, _score) in enumerate(vec["hits"], 1):
            if seq in rows:
                h = fused.setdefault(seq, self._hit(rows[seq]))
                h.vector_rank, h.vector_model = rank, vec["model"]
                h.rrf += 1.0 / (RRF_K + rank)
                label = "semantic" if vec["semantic"] else "lexical-vector"
                if label not in h.channels:
                    h.channels.append(label)
        hits = self.rerank(question, list(fused.values()))
        return {"hits": hits[:k], "info": info}

    @staticmethod
    def _hit(r: sqlite3.Row) -> Hit:
        return Hit(r["seq"], r["id"], r["source_id"], r["text"], r["section"], r["page_start"], r["locator"], bool(r["flagged"]), r["ordinal"])

    @staticmethod
    def rerank(question: str, hits: List[Hit]) -> List[Hit]:
        """Transparent heuristic: fused rank + query-term coverage + phrase match, with reference-list passages demoted. No learned model, no invented confidence."""
        qt = set(tokens(question))
        phrase = " ".join(tokens(question)[:4])
        top = max((h.rrf for h in hits), default=0.0) or 1.0
        for h in hits:
            ht = set(tokens(h.text))
            h.coverage = len(qt & ht) / len(qt) if qt else 0.0
            bonus = 0.15 if phrase and len(phrase) > 8 and phrase in " ".join(tokens(h.text)) else 0.0
            demote = 0.5 if h.section and re.search(r"reference|bibliograph", h.section, re.I) else 1.0
            h.score = (0.55 * (h.rrf / top) + 0.45 * h.coverage + bonus) * demote
        return sorted(hits, key=lambda h: (-h.score, h.source_id, h.ordinal))


def select_context(hits: Sequence[Hit], *, max_chars: int = 9000, per_source: int = 4, max_chunks: int = 10) -> List[Hit]:
    """Take the best passages while capping any one source, so one long document cannot crowd out the rest; stop at the character budget."""
    chosen: List[Hit] = []
    per: Dict[str, int] = {}
    used = 0
    for h in hits:
        if len(chosen) >= max_chunks:
            break
        if per.get(h.source_id, 0) >= per_source:
            continue
        if used + len(h.text) > max_chars and chosen:
            continue
        chosen.append(h)
        per[h.source_id] = per.get(h.source_id, 0) + 1
        used += len(h.text)
    return chosen
