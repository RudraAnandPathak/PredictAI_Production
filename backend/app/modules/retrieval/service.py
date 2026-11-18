"""Document retrieval: index, keyword / meaning-based / embedding / hybrid search, metadata filters, multi-document search, comparison, passage extraction and quality evaluation.

Everything runs on the person's own extracted documents with open-source code (BM25, LSA, reciprocal rank fusion). Dense embeddings are optional and only come from a provider the
owner enabled; document text is private material and so never reaches an external provider. Every call goes through AssetService.get_owned, so a document that is missing, foreign or
being deleted answers exactly like one that does not exist. Retrieved text is untrusted data: this module returns it, it never acts on it."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..identity.context import Context
from ..identity.tokens import new_id
from ..providers import Consent, ProviderService
from ..storage import StorageBackend, StorageError
from ..textkit import numbers_in, sentences, stem, tokenize
from ..workspace import keys
from ..workspace.assets import AssetService
from ..workspace.errors import conflict, invalid, not_found
from ..workspace.extraction import DataViews
from ..workspace.repository import WorkspaceRepository
from .chunker import CHUNKER_VERSION, Chunk, chunk_document
from .corpus import CORPUS_CACHE, MODES, Corpus, DocIndex, citation_for
from .metrics import pick_chunks, summarise
from .repository import RetrievalRepository
from .semantic import normalise_rows

INDEX_VERSION = f"1.{CHUNKER_VERSION}"
MAX_DOCS, MAX_QUERY, MAX_K = 10, 500, 50
DENSE_CHUNK_CAP, DENSE_BATCH = 2000, 16


class RetrievalService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, assets: AssetService, views: DataViews, providers: Optional[ProviderService] = None) -> None:
        self.ctx, self.repo, self.storage, self.assets, self.views, self.providers = ctx, repo, storage, assets, views, providers
        self.rrepo = RetrievalRepository(ctx.conn)

    # ------------------------------------------------------------------ index lifecycle
    def _resolve(self, principal: Principal, asset_id: str, version_id: Optional[str]):
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != "document":
            raise invalid("asset", "Only documents have text that can be searched.")
        vid = version_id or asset["current_version_id"]
        version = self.repo.version(principal.user_id, vid) if isinstance(vid, str) and len(vid) <= 64 else None
        if version is None or version["asset_id"] != asset["id"]:
            raise not_found("version")
        ext = self.repo.extraction_for_version(principal.user_id, version["id"])
        if ext is None or ext["status"] not in ("ready", "partial") or not ext["text_key"]:
            raise not_found("extraction")
        return asset, version, ext

    def _parse(self, blob: bytes, row) -> DocIndex:
        d = json.loads(blob.decode("utf-8"))
        return DocIndex(asset_id=d["asset_id"], version_id=d["version_id"], name=d["name"], extraction_id=d.get("extraction_id"), source_checksum=d.get("source_checksum"), page_count=d.get("page_count"),
                        page_estimated=bool(d.get("page_estimated")), chunks=[Chunk.from_json(c) for c in d["chunks"]], warnings=d.get("warnings", []), built_at=row["built_at"], sha256=row["sha256"])

    def _attach_dense(self, index: DocIndex, row) -> None:
        if not row["embedding_key"] or not row["embedding_model"]:
            return
        try:
            d = json.loads(self.storage.read_bytes(row["embedding_key"]).decode("utf-8"))
            m = np.asarray(d["vectors"], dtype=float)
            if m.shape[0] == len(index.chunks) and m.ndim == 2 and np.isfinite(m).all() and d.get("model") == row["embedding_model"]:
                index.dense, index.dense_model = normalise_rows(m), row["embedding_model"]
        except (StorageError, ValueError, KeyError, TypeError):
            index.dense, index.dense_model = None, None                                        # a damaged embedding file only costs the embedding search

    def _load(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any], DocIndex, bool]:
        asset, version, ext = self._resolve(principal, asset_id, version_id)
        row = self.rrepo.get(principal.user_id, asset["id"], version["id"])
        index = None
        if row is not None and row["index_version"] == INDEX_VERSION and row["extraction_id"] == ext["id"]:
            try:
                blob = self.storage.read_bytes(row["key"])
                if hashlib.sha256(blob).hexdigest() == row["sha256"]:
                    index = self._parse(blob, row)
            except (StorageError, ValueError, KeyError):
                index = None
        built = index is None
        if built:
            index = self._build(principal, asset, version, ext)
            row = self.rrepo.get(principal.user_id, asset["id"], version["id"])
        self._attach_dense(index, row)
        return asset, version, index, built

    def _build(self, principal: Principal, asset, version, ext) -> DocIndex:
        self.ctx.limiter.hit("retrieval_build:user", principal.user_id)
        _, _, _, doc = self.views.document_json(principal, asset["id"], version["id"])
        chunks, warnings = chunk_document(doc)
        now = self.ctx.clock.iso()
        payload = {"index_version": INDEX_VERSION, "asset_id": asset["id"], "version_id": version["id"], "name": asset["name"], "extraction_id": ext["id"], "source_checksum": version["checksum_sha256"],
                   "page_count": doc.get("page_count"), "page_estimated": bool(doc.get("page_count_estimated")), "warnings": warnings, "chunks": [c.to_json() for c in chunks]}
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        key = keys.doc_index_key(asset["project_id"], asset["id"], version["id"])
        self.storage.put(key, [blob], overwrite=True)
        sha = hashlib.sha256(blob).hexdigest()
        self.rrepo.upsert(id=new_id(), project_id=asset["project_id"], owner=principal.user_id, asset_id=asset["id"], version_id=version["id"], extraction_id=ext["id"], index_version=INDEX_VERSION,
                          chunk_count=len(chunks), page_count=doc.get("page_count"), key=key, sha256=sha, size_bytes=len(blob), now=now)
        self.ctx.audit.record("retrieval.index.built", actor=principal.user_id, target=asset["id"], metadata={"chunks": len(chunks)})
        return DocIndex(asset_id=asset["id"], version_id=version["id"], name=asset["name"], extraction_id=ext["id"], source_checksum=version["checksum_sha256"], page_count=doc.get("page_count"),
                        page_estimated=bool(doc.get("page_count_estimated")), chunks=chunks, warnings=warnings, built_at=now, sha256=sha)

    def build(self, principal: Principal, asset_id: str, *, version_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        asset, version, ext = self._resolve(principal, asset_id, version_id)
        if force:
            self._build(principal, asset, version, ext)
            CORPUS_CACHE.clear()
        _, _, index, built = self._load(principal, asset["id"], version["id"])
        return {"asset_id": asset["id"], "version_id": version["id"], "built": built or force, "chunks": len(index.chunks), "pages": index.page_count, "page_numbers": "estimated" if index.page_estimated else "exact",
                "warnings": index.warnings, "dense_model": index.dense_model, "index_version": INDEX_VERSION}

    def status(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        project = self.assets.projects.get_owned(principal, project_id)
        items = []
        indexes = {(r["asset_id"], r["version_id"]): r for r in self.rrepo.for_project(principal.user_id, project["id"])}
        for a in self.assets.list(principal, project["id"], kind="document", limit=200)["items"]:
            vid = a.get("current_version_id") or self.repo.asset(principal.user_id, a["id"])["current_version_id"]
            ext = self.repo.extraction_for_version(principal.user_id, vid) if vid else None
            row = indexes.get((a["id"], vid))
            ready = ext is not None and ext["status"] in ("ready", "partial") and bool(ext["text_key"])
            items.append({"asset_id": a["id"], "name": a["name"], "extraction_ready": ready, "indexed": row is not None, "stale": bool(row and ready and (row["extraction_id"] != ext["id"] or row["index_version"] != INDEX_VERSION)),
                          "chunks": row["chunk_count"] if row else 0, "built_at": row["built_at"] if row else None, "dense_model": row["embedding_model"] if row else None})
        return {"project_id": project["id"], "documents": items, "index_version": INDEX_VERSION,
                "methods": {"keyword": "BM25 on the words in each passage", "semantic": "latent semantic analysis fitted on your own documents (local; not a pretrained model)", "hybrid": "keyword and meaning-based rankings combined by reciprocal rank fusion",
                            "dense": "embeddings from a provider the platform owner enabled (optional)"}}

    # ------------------------------------------------------------------ search
    @staticmethod
    def _query(q: Any) -> str:
        if not isinstance(q, str) or not q.strip():
            raise invalid("query", "Enter what you want to find.")
        q = re.sub(r"[\x00-\x1f\x7f]", " ", q).strip()
        if len(q) > MAX_QUERY:
            raise invalid("query", f"Keep the question under {MAX_QUERY} characters.")
        return q

    @staticmethod
    def _filters(f: Any) -> Dict[str, Any]:
        if f in (None, {}):
            return {}
        if not isinstance(f, dict) or set(f) - {"pages", "section", "min_words"}:
            raise invalid("filters", "Filters can use pages, section and min_words.")
        out: Dict[str, Any] = {}
        if f.get("pages") is not None:
            p = f["pages"]
            if not (isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= 100000 for x in p) and p[0] <= p[1]):
                raise invalid("filters.pages", "Use two page numbers, for example [3, 8].")
            out["pages"] = p
        if f.get("section") is not None:
            if not isinstance(f["section"], str) or len(f["section"]) > 120:
                raise invalid("filters.section", "Use a short piece of a section title.")
            out["section"] = f["section"].strip()
        if f.get("min_words") is not None:
            if not isinstance(f["min_words"], int) or isinstance(f["min_words"], bool) or not 1 <= f["min_words"] <= 2000:
                raise invalid("filters.min_words", "Use a whole number from 1 to 2000.")
            out["min_words"] = f["min_words"]
        return out

    def _select(self, principal: Principal, project: Dict[str, Any], asset_ids: Optional[Sequence[str]]) -> Tuple[List[DocIndex], List[Dict[str, str]]]:
        skipped: List[Dict[str, str]] = []
        if asset_ids is None:
            listing = self.assets.list(principal, project["id"], kind="document", limit=200)["items"]
            ids = [a["id"] for a in listing]
            explicit = False
        else:
            if not isinstance(asset_ids, (list, tuple)) or not asset_ids or not all(isinstance(a, str) for a in asset_ids):
                raise invalid("assets", "Choose at least one document.")
            ids = list(dict.fromkeys(asset_ids))
            explicit = True
        if len(ids) > MAX_DOCS:
            if explicit:
                raise invalid("assets", f"Search up to {MAX_DOCS} documents at a time.")
            skipped += [{"asset_id": i, "reason": f"Only the first {MAX_DOCS} documents are searched at once."} for i in ids[MAX_DOCS:]]
            ids = ids[:MAX_DOCS]
        docs: List[DocIndex] = []
        for aid in ids:
            try:
                asset, _, index, _ = self._load(principal, aid)
                if asset["project_id"] != project["id"]:
                    raise not_found("file")
                docs.append(index)
            except AppError as error:
                if explicit or error.status_code != 404:
                    raise
                skipped.append({"asset_id": aid, "reason": "Its text has not been extracted yet."})
        return docs, skipped

    def _corpus(self, docs: Sequence[DocIndex]) -> Corpus:
        key = tuple((d.sha256, d.dense_model) for d in docs)
        c = CORPUS_CACHE.get(key)
        if c is None:
            c = Corpus(docs)
            CORPUS_CACHE.put(key, c)
        return c

    def _query_vector(self, principal: Principal, project_id: str, query: str, model: str, consent: Optional[Consent]) -> Tuple[Optional[List[float]], Optional[str]]:
        if self.providers is None:
            return None, None
        out = self.providers.router().embed(principal.user_id, project_id, [query], data_class="private", consent=consent)
        if not out.ok or not out.vectors:
            return None, "The embedding search was skipped because no allowed embedding provider answered."
        if out.model != model:
            return None, "The embedding search was skipped because the provider now uses a different model from the one that indexed the documents."
        return out.vectors[0], None

    def search(self, principal: Principal, project_id: str, query: Any, *, asset_ids: Optional[Sequence[str]] = None, mode: str = "hybrid", k: int = 8, filters: Any = None,
               consent: Optional[Consent] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("retrieval:user", principal.user_id)
        q = self._query(query)
        if mode not in MODES:
            raise invalid("mode", "Use keyword, semantic, dense or hybrid.")
        if not isinstance(k, int) or isinstance(k, bool):
            raise invalid("k", "Use a whole number.")
        k = max(1, min(k, MAX_K))
        flt = self._filters(filters)
        project = self.assets.projects.get_owned(principal, project_id)
        docs, skipped = self._select(principal, project, asset_ids)
        if not docs:
            return {"query": q, "mode": mode, "hits": [], "used": [], "notes": ["There are no searchable documents yet. Upload a document and wait for its text to be extracted."], "documents": [],
                    "skipped": skipped, "semantic_method": None, "candidates": 0, "filters": flt}
        corpus = self._corpus(docs)
        qvec, note = None, None
        if mode in ("dense", "hybrid") and corpus.dense_model:
            qvec, note = self._query_vector(principal, project["id"], q, corpus.dense_model, consent)
        result = corpus.search(q, mode=mode, k=k, filters=flt, query_vector=qvec)
        if note:
            result["notes"].append(note)
        if not result["hits"] and not result["notes"]:
            result["notes"].append("No passage matched. Nothing was found in your documents for this question.")
        result.update(query=q, mode=mode, filters=flt, skipped=skipped, documents=[{"asset_id": d.asset_id, "name": d.name, "chunks": len(d.chunks), "dense_model": d.dense_model} for d in docs])
        return result

    # ------------------------------------------------------------------ passages and citations
    def passage(self, principal: Principal, asset_id: str, chunk_id: str, *, version_id: Optional[str] = None, context: int = 1) -> Dict[str, Any]:
        asset, version, index, _ = self._load(principal, asset_id, version_id)
        pos = next((i for i, c in enumerate(index.chunks) if c.id == chunk_id), None) if isinstance(chunk_id, str) else None
        if pos is None:
            raise not_found("passage")
        context = max(0, min(int(context), 3))
        window = index.chunks[max(0, pos - context): pos + context + 1]
        c = index.chunks[pos]
        return {"asset_id": asset["id"], "version_id": version["id"], "document": index.name, "page_estimated": index.page_estimated, "passage": {**c.to_json(), "citation": citation_for(index, c)},
                "before": [w.to_json() for w in window if w.ordinal < c.ordinal], "after": [w.to_json() for w in window if w.ordinal > c.ordinal], "verification": self.verify_citation(principal, citation_for(index, c))}

    def verify_citation(self, principal: Principal, citation: Any) -> Dict[str, Any]:
        """Re-read the stored extraction and check that the cited span still exists and says what the citation recorded. Never trusts text supplied by the caller."""
        need = ("asset_id", "version_id", "page_start", "start", "end", "quote_sha256")
        if not isinstance(citation, dict) or any(k not in citation for k in need):
            return {"verified": False, "reason": "The citation is incomplete."}
        try:
            _, _, _, doc = self.views.document_json(principal, citation["asset_id"], citation["version_id"])
        except AppError:
            return {"verified": False, "reason": "The document is no longer available."}
        page = next((p for p in doc["pages"] if p["number"] == citation["page_start"]), None)
        s, e = citation["start"], citation["end"]
        if page is None or not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(page.get("text") or "")):
            return {"verified": False, "reason": "The cited page or position does not exist in the document."}
        text = page["text"][s:e]
        if hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] != citation["quote_sha256"]:
            return {"verified": False, "reason": "The document text at that position has changed since the citation was made."}
        return {"verified": True, "reason": None, "text": text, "ocr": page.get("method") == "ocr", "ocr_confidence": page.get("ocr_confidence")}

    # ------------------------------------------------------------------ comparison
    @staticmethod
    def _surface(texts: Sequence[str]) -> Dict[str, str]:
        counts: Dict[str, Counter] = {}
        for t in texts:
            for w in tokenize(t, stemming=False):
                counts.setdefault(stem(w), Counter())[w] += 1
        return {s: c.most_common(1)[0][0] for s, c in counts.items()}

    def compare(self, principal: Principal, project_id: str, asset_ids: Any, topic: Any, *, k: int = 3) -> Dict[str, Any]:
        self.ctx.limiter.hit("retrieval:user", principal.user_id)
        q = self._query(topic)
        if not isinstance(asset_ids, list) or not 2 <= len(set(asset_ids)) <= 5 or not all(isinstance(a, str) for a in asset_ids):
            raise invalid("assets", "Choose two to five different documents to compare.")
        project = self.assets.projects.get_owned(principal, project_id)
        docs, _ = self._select(principal, project, list(dict.fromkeys(asset_ids)))
        k = max(1, min(int(k), 6))
        per_doc, stems_by_doc, surface_src = [], [], []
        for d in docs:
            res = Corpus([d]).search(q, mode="hybrid", k=k)
            texts = [h["text"] for h in res["hits"]]
            toks = {t for text in texts for t in tokenize(text)}
            stems_by_doc.append(toks)
            surface_src += texts
            per_doc.append({"asset_id": d.asset_id, "document": d.name, "passages": res["hits"], "found": bool(texts), "numbers": sorted(numbers_in(" ".join(texts)))[:30]})
        surface = self._surface(surface_src)
        shared = set.intersection(*stems_by_doc) if all(stems_by_doc) else set()
        union = set.union(*stems_by_doc) if stems_by_doc else set()
        distinct = {pd["asset_id"]: sorted((s for s in stems_by_doc[i] - set.union(*(stems_by_doc[:i] + stems_by_doc[i + 1:])) if len(s) > 2), key=lambda s: s)[:20] for i, pd in enumerate(per_doc)} if len(per_doc) > 1 else {}
        for pd in per_doc:
            others = set()
            for o in per_doc:
                if o is not pd:
                    others |= set(o["numbers"])
            pd["numbers_only_here"] = [n for n in pd["numbers"] if n not in others]
        return {"topic": q, "documents": per_doc, "shared_terms": sorted(surface.get(s, s) for s in shared)[:30],
                "distinct_terms": {a: sorted(surface.get(s, s) for s in v) for a, v in distinct.items()}, "term_overlap": round(len(shared) / len(union), 3) if union else 0.0,
                "caveat": "This is an extractive comparison of the passages that best match the topic. It shows what each document says and where their wording and numbers differ; it does not decide which is correct, "
                          "and a different number may describe a different thing."}

    # ------------------------------------------------------------------ dense embeddings (optional)
    def build_dense(self, principal: Principal, asset_id: str, *, version_id: Optional[str] = None, consent: Optional[Consent] = None) -> Dict[str, Any]:
        asset, version, index, _ = self._load(principal, asset_id, version_id)               # authorise first: nobody learns how the platform is configured from a document that is not theirs
        if self.providers is None:
            raise conflict("NO_EMBEDDING_PROVIDER", "No embedding provider is available. Keyword and meaning-based search still work.")
        self.ctx.limiter.hit("retrieval_build:user", principal.user_id)
        row = self.rrepo.get(principal.user_id, asset["id"], version["id"])
        texts = [c.text for c in index.chunks][:DENSE_CHUNK_CAP]
        router = self.providers.router()
        vectors: List[List[float]] = []
        model: Optional[str] = None
        for i in range(0, len(texts), DENSE_BATCH):
            out = router.embed(principal.user_id, asset["project_id"], texts[i:i + DENSE_BATCH], data_class="private", consent=consent)
            if not out.ok or not out.vectors or (model is not None and out.model != model):
                why = "; ".join(f"{s['provider']}: {s['reason']}" for s in out.skipped) or "the provider did not answer"
                raise conflict("EMBEDDING_UNAVAILABLE", f"Embeddings could not be created ({why}). Keyword and meaning-based search still work.")
            model = out.model
            vectors += out.vectors
        key = keys.doc_embedding_key(asset["project_id"], asset["id"], version["id"])
        payload = {"model": model, "dims": len(vectors[0]) if vectors else 0, "count": len(vectors), "vectors": [[round(x, 6) for x in v] for v in vectors]}
        self.storage.put(key, [json.dumps(payload, separators=(",", ":")).encode("utf-8")], overwrite=True)
        self.rrepo.set_dense(row["id"], model, key)
        CORPUS_CACHE.clear()
        self.ctx.audit.record("retrieval.embeddings.built", actor=principal.user_id, target=asset["id"], metadata={"model": model, "chunks": len(vectors)})
        covered = len(vectors)
        return {"asset_id": asset["id"], "model": model, "chunks_embedded": covered, "chunks_total": len(index.chunks), "complete": covered == len(index.chunks)}

    # ------------------------------------------------------------------ quality evaluation
    def evaluate(self, principal: Principal, project_id: str, *, asset_ids: Optional[Sequence[str]] = None, labelled: Any = None, sample: int = 30) -> Dict[str, Any]:
        self.ctx.limiter.hit("retrieval:user", principal.user_id)
        project = self.assets.projects.get_owned(principal, project_id)
        docs, skipped = self._select(principal, project, asset_ids)
        if not docs:
            raise invalid("assets", "There are no searchable documents to evaluate.")
        corpus = self._corpus(docs)
        gid = [f"{corpus.docs[di].asset_id}:{c.id}" for di, c in corpus.rows]
        index_of = {g: i for i, g in enumerate(gid)}
        modes = ("keyword", "semantic", "hybrid")

        def run(query: str, relevant: set) -> Dict[str, List[str]]:
            return {m: [f"{h['asset_id']}:{h['chunk_id']}" for h in corpus.search(query, mode=m, k=10)["hits"]] for m in modes}

        if labelled is not None:
            if not isinstance(labelled, list) or not 1 <= len(labelled) <= 50:
                raise invalid("labelled", "Give 1 to 50 labelled questions.")
            runs: Dict[str, List[Dict[str, Any]]] = {m: [] for m in modes}
            unusable = 0
            for item in labelled:
                if not isinstance(item, dict) or not isinstance(item.get("query"), str):
                    raise invalid("labelled", "Each item needs a query.")
                relevant: set = set()
                for cid in item.get("chunk_ids") or []:
                    relevant |= {g for g in gid if g.endswith(":" + str(cid)) and (not item.get("asset_id") or g.startswith(item["asset_id"] + ":"))}
                for pg in item.get("pages") or []:
                    relevant |= {gid[i] for i, (di, c) in enumerate(corpus.rows) if c.page_start == pg and (not item.get("asset_id") or corpus.docs[di].asset_id == item["asset_id"])}
                if isinstance(item.get("quote"), str) and item["quote"].strip():
                    needle = " ".join(item["quote"].lower().split())
                    relevant |= {gid[i] for i, (_, c) in enumerate(corpus.rows) if needle in " ".join(c.text.lower().split())}
                if not relevant:
                    unusable += 1
                    continue
                got = run(self._query(item["query"]), relevant)
                for m in modes:
                    runs[m].append({"ranked": got[m], "relevant": relevant})
            return {"kind": "labelled", "by_mode": {m: summarise(runs[m]) for m in modes}, "unusable_questions": unusable, "skipped": skipped,
                    "caveat": "Measured against the relevant passages you supplied. Results describe these questions and documents only."}
        n = max(5, min(int(sample), 60))
        picks = pick_chunks(corpus.size, n)
        styles: Dict[str, Dict[str, List[Dict[str, Any]]]] = {s: {m: [] for m in modes} for s in ("terms", "sentence")}
        misses = []
        for i in picks:
            chunk = corpus.rows[i][1]
            toks = corpus.token_sets[i]
            rare = sorted(toks, key=lambda t: (-corpus.bm25.idf(t), t))[:6]
            sents = sorted(sentences(chunk.text), key=len, reverse=True)
            queries = {"terms": " ".join(rare), "sentence": " ".join(tokenize(sents[0], stemming=False)[:12]) if sents else ""}
            for style, qtext in queries.items():
                if not qtext.strip():
                    continue
                got = run(qtext, {gid[i]})
                for m in modes:
                    styles[style][m].append({"ranked": got[m], "relevant": {gid[i]}})
                if gid[i] not in got["hybrid"][:5] and len(misses) < 5:
                    misses.append({"style": style, "query": qtext, "expected_page": chunk.page_start, "expected_chunk": chunk.id})
        return {"kind": "synthetic_self_retrieval", "documents": len(docs), "passages": corpus.size, "sampled": len(picks),
                "by_style": {s: {m: summarise(v) for m, v in per.items()} for s, per in styles.items()}, "misses": misses, "skipped": skipped,
                "caveat": "No relevance labels were supplied, so this only checks that each sampled passage can be found again from words and sentences taken from itself. "
                          "It shows the index works; it does not measure how well real questions are answered. Supply labelled questions for that."}
