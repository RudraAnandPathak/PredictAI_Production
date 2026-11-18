"""Ingestion pipeline (worker side): extract -> chunk -> intelligence -> index. Runs without a signed-in person: every step re-reads the owning rows and writes only to them.

Stages are written to the source and the job as they happen, so a person watching sees honest progress. Failures carry a stable code and a message that says what to do. Nothing here
guesses: a field the document does not state stays empty, and what could not be read is reported in `intelligence.warnings`."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from ....db import transaction
from ...identity.context import Context
from ...identity.tokens import new_id
from ...p7 import jload
from ..core.common import EV_SOURCE_FAILED, record_usage, remove_stored, root, source_path
from ..embeddings.provider import BUILTIN_MODEL, Embedder
from ..ingestion import intelligence as intel
from ..ingestion.chunker import chunk_blocks
from ..ingestion.extract import Block, ExtractError, Extracted
from ..ingestion.isolated import run_extract
from ..retrieval.vectorstore import VectorStore, build_store
from ..security import injection
from ..security.malware import scan_file
from ..security.urlguard import SafeFetcher, UrlRejected
from ...workspace.errors import AppError
from .jobs import Cancelled, JobService

RETRY_CODES = {"URL_FETCH_FAILED", "URL_TIMEOUT", "UPSTREAM_UNAVAILABLE", "VECTOR_STORE_UNAVAILABLE"}


class PipelineError(Exception):
    def __init__(self, code: str, message: str, retry: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retry = code, message, retry


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


class Pipeline:
    def __init__(self, ctx: Context, *, fetcher: Optional[SafeFetcher] = None, router_factory: Optional[Callable[[], Any]] = None, store: Optional[VectorStore] = None,
                 extractor: Callable[..., Dict[str, Any]] = run_extract) -> None:
        self.ctx, self.conn = ctx, ctx.conn
        s = ctx.settings
        self.fetcher = fetcher or SafeFetcher(allow_private=s.ri_allow_private_urls, max_bytes=s.ri_url_max_mb * 1024 * 1024, timeout=float(s.ri_url_timeout_s))
        self.router_factory, self.extractor = router_factory, extractor
        self.store = store or build_store(ctx)
        self.jobs = JobService(ctx)

    # ------------------------------------------------------------------ entry
    def run(self, job: Dict[str, Any]) -> str:
        """Process one claimed job. Returns 'succeeded' | 'retry' | 'failed' | 'cancelled'."""
        jid = job["id"]
        try:
            handler = {"ingest": self._ingest, "reindex": self._ingest, "fetch_url": self._fetch_url, "resolve_identifier": self._resolve, "embed": self._embed_job, "analysis": self._analysis}[job["kind"]]
            handler(job)
            self.jobs.succeed(jid)
            return "succeeded"
        except Cancelled:
            self.jobs.cancelled(jid)
            if job.get("source_id"):
                self.conn.execute("UPDATE ri_sources SET status='cancelled', stage='cancelled', updated_at=? WHERE id=? AND status IN ('queued','processing')", (self.ctx.clock.iso(), job["source_id"]))
            return "cancelled"
        except PipelineError as e:
            return self._failed(job, e.code, e.message, e.retry)
        except ExtractError as e:
            return self._failed(job, e.code, e.message, False)
        except UrlRejected as e:
            return self._failed(job, e.code, e.message, e.code in RETRY_CODES)
        except AppError as e:
            return self._failed(job, e.code if isinstance(e.code, str) else str(e.code), e.message, False)
        except RuntimeError as e:
            return self._failed(job, "VECTOR_STORE_UNAVAILABLE", "The vector store could not be reached. The source stays searchable by keyword.", True) if "QDRANT" in str(e) else self._failed(job, "INTERNAL", "Processing failed unexpectedly.", False)
        except Exception:                                                              # noqa: BLE001 - a bug on one document must not stop the worker
            return self._failed(job, "INTERNAL", "Processing failed unexpectedly. The failure was recorded.", False)

    def _analysis(self, job: Dict[str, Any]) -> None:
        from ..reasoning.analysis import AnalysisService
        AnalysisService(self.ctx, router_factory=self.router_factory).run(job)

    def _failed(self, job: Dict[str, Any], code: str, message: str, retry: bool) -> str:
        outcome = self.jobs.fail(job, code, message, retry=retry)
        if job["kind"] == "analysis":
            from ..reasoning.analysis import AnalysisService
            AnalysisService(self.ctx).mark_failed(job, code, message, final=(outcome == "failed"))
        sid = job.get("source_id")
        if sid:
            if outcome == "failed":
                self.conn.execute("UPDATE ri_sources SET status='failed', stage='failed', error_code=?, error_message=?, updated_at=? WHERE id=?", (code, message[:500], self.ctx.clock.iso(), sid))
                self.conn.execute("UPDATE rw_sources SET status='failed', updated_at=? WHERE id=?", (self.ctx.clock.iso(), sid))
                self.ctx.audit.record(EV_SOURCE_FAILED, "failure", actor=job["owner_user_id"], reason=code, metadata={"source_id": sid})
            else:
                self.conn.execute("UPDATE ri_sources SET status='queued', stage='waiting to retry', error_code=?, error_message=?, updated_at=? WHERE id=?", (code, message[:500], self.ctx.clock.iso(), sid))
        return outcome

    # ------------------------------------------------------------------ helpers
    def _src(self, job: Dict[str, Any]) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM ri_sources WHERE id = ? AND owner_user_id = ? AND workspace_id = ?", (job["source_id"], job["owner_user_id"], job["workspace_id"])).fetchone()
        if row is None:
            raise Cancelled()                                                           # the source was deleted while queued
        if row["status"] == "cancelled":
            raise Cancelled()
        return row

    def _stage(self, job: Dict[str, Any], stage: str, pct: int) -> None:
        self.jobs.heartbeat(job["id"], pct, stage)
        self.conn.execute("UPDATE ri_sources SET status='processing', stage=?, updated_at=? WHERE id=? AND status IN ('queued','processing')", (stage, self.ctx.clock.iso(), job["source_id"]))

    def _allow_external(self, workspace_id: str, owner_id: str) -> bool:
        from ..policy import external_allowed
        return external_allowed(self.conn, self.ctx.settings, owner_id, workspace_id)

    def embedder(self, workspace_id: str, owner_id: str) -> Embedder:
        return Embedder(self.router_factory, owner_id, allow_external=self._allow_external(workspace_id, owner_id))

    # ------------------------------------------------------------------ ingest from stored file
    def _ingest(self, job: Dict[str, Any]) -> None:
        src = self._src(job)
        s = self.ctx.settings
        self._stage(job, "reading file", 10)
        try:
            with open(source_path(self.ctx, src["stored_name"]), "rb") as fh:
                data = fh.read()
        except (OSError, ValueError, TypeError):
            raise PipelineError("FILE_MISSING", "The stored file is no longer available. Upload it again.") from None
        self._stage(job, "extracting text", 25)
        raw = self.extractor(src["format"], data, max_pages=s.ri_pdf_max_pages, timeout_s=s.ri_extract_timeout_s, memory_mb=s.ri_extract_memory_mb, base_url=src["url"] or "")
        ex = Extracted(**{**raw, "blocks": [Block(**b) for b in raw["blocks"]]})
        d = self._discovery_meta(job)
        override = {k: d[k] for k in ("title", "authors", "published") if d.get(k)}
        if d.get("abstract") and not ex.abstract:
            override["abstract"] = d["abstract"]
        if d.get("venue"):
            self.conn.execute("UPDATE ri_sources SET venue=? WHERE id=?", (d["venue"], src["id"]))
        self._index(job, src, ex, access="full_text", meta_override=override or None)

    def _index(self, job: Dict[str, Any], src: sqlite3.Row, ex: Extracted, *, access: str, meta_override: Optional[Dict[str, Any]] = None) -> None:
        s = self.ctx.settings
        self._stage(job, "splitting into passages", 50)
        chunks = chunk_blocks(ex.blocks, target_words=s.ri_chunk_words, overlap_words=s.ri_chunk_overlap_words, max_chunks=s.ri_max_chunks_per_source)
        if not chunks:
            raise PipelineError("EMPTY_DOCUMENT", "No readable text was found in this source.")
        warnings = list(ex.warnings)
        if len(chunks) >= s.ri_max_chunks_per_source:
            warnings.append(f"Only the first {s.ri_max_chunks_per_source} passages were indexed (the per-source limit).")
        self._stage(job, "analysing", 62)
        intelligence = intel.build(chunks, ex.abstract)
        intelligence["warnings"] = warnings
        flagged = [injection.is_flagged(c.text) for c in chunks]
        structure = [{"title": b.text[:160], "level": b.level, "locator": b.locator, "page": b.page} for b in ex.blocks if b.kind == "heading"][:300]
        meta = {"title": ex.title, "authors": ex.authors, "published": ex.published, "abstract": ex.abstract, "keywords": ex.keywords or intelligence["keywords"][:8]}
        meta.update(meta_override or {})
        now = self.ctx.clock.iso()
        old = self.conn.execute("SELECT version, chunk_count FROM ri_sources WHERE id = ?", (src["id"],)).fetchone()
        version = old["version"] + (1 if old["chunk_count"] else 0)
        self._stage(job, "saving passages", 72)
        with transaction(self.conn):
            self.store.delete_source(workspace_id=src["workspace_id"], owner_id=src["owner_user_id"], source_id=src["id"])
            self.conn.execute("DELETE FROM ri_embeddings WHERE source_id = ?", (src["id"],))
            self.conn.execute("DELETE FROM ri_chunks WHERE source_id = ?", (src["id"],))
            self.conn.executemany(
                "INSERT INTO ri_chunks (id, source_id, workspace_id, owner_user_id, version, ordinal, text, section, page_start, page_end, locator, words, flagged, text_sha256, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(new_id(), src["id"], src["workspace_id"], src["owner_user_id"], version, c.ordinal, c.text, c.section, c.page_start, c.page_end, c.locator, c.words, int(f), c.sha256, now) for c, f in zip(chunks, flagged)])
            self.conn.execute("INSERT OR REPLACE INTO ri_source_versions (id, source_id, workspace_id, owner_user_id, version, content_sha256, chunk_count, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (new_id(), src["id"], src["workspace_id"], src["owner_user_id"], version, src["content_sha256"], len(chunks), "reprocessed" if version > 1 else "first import", now))
            title = (meta.get("title") or src["title"] or "Untitled")[:300]
            self.conn.execute(
                "UPDATE ri_sources SET title=?, authors=?, published=COALESCE(?, published), abstract=COALESCE(?, abstract), keywords=?, access=?, page_count=?, char_count=?, chunk_count=?, table_count=?, "
                "injection_flags=?, structure=?, refs=?, intelligence=?, version=?, embed_status='pending', stage='indexing', error_code=NULL, error_message=NULL, updated_at=? WHERE id=?",
                (title, json.dumps(meta.get("authors") or [])[:4000], meta.get("published"), meta.get("abstract"), json.dumps((meta.get("keywords") or [])[:30]), access, ex.page_count, sum(len(c.text) for c in chunks),
                 len(chunks), ex.table_count, sum(flagged), json.dumps(structure), json.dumps(ex.refs[:500]), json.dumps(intelligence), version, now, src["id"]))
            self.conn.execute("UPDATE rw_sources SET title = ?, page_count = ?, updated_at = ? WHERE id = ?", (title, ex.page_count, now, src["id"]))
        self._stage(job, "indexing for search", 80)
        self._embed(job, src["id"])
        done = self.ctx.clock.iso()
        self.conn.execute("UPDATE ri_sources SET status='ready', stage='ready', updated_at=? WHERE id=?", (done, src["id"]))
        self.conn.execute("UPDATE rw_sources SET status='ready', updated_at=? WHERE id=?", (done, src["id"]))

    # ------------------------------------------------------------------ embeddings
    def _embed(self, job: Optional[Dict[str, Any]], source_id: str) -> None:
        src = self.conn.execute("SELECT * FROM ri_sources WHERE id = ?", (source_id,)).fetchone()
        if src is None:
            return
        rows = self.conn.execute("SELECT seq, text FROM ri_chunks WHERE source_id = ? ORDER BY ordinal", (source_id,)).fetchall()
        if not rows:
            self.conn.execute("UPDATE ri_sources SET embed_status='none' WHERE id=?", (source_id,))
            return
        emb = self.embedder(src["workspace_id"], src["owner_user_id"])
        texts = [r["text"] for r in rows]
        base = {"source_id": source_id, "workspace_id": src["workspace_id"], "owner_user_id": src["owner_user_id"]}
        built = emb.builtin(texts)
        items = [{**base, "chunk_seq": r["seq"], "model": built.model, "provider": built.provider, "vector": v} for r, v in zip(rows, built.vectors)]
        model_used = built.model
        try:
            prov = emb.provider(texts)
        except Exception:                                                              # noqa: BLE001 - provider trouble degrades to lexical search, it does not fail the import
            prov = None
        if prov is not None:
            items += [{**base, "chunk_seq": r["seq"], "model": prov.model, "provider": prov.provider, "vector": v} for r, v in zip(rows, prov.vectors)]
            model_used = prov.model
            record_usage(self.ctx, src["owner_user_id"], src["workspace_id"], "embed_units", max(1, prov.units), prov.provider)
        try:
            with transaction(self.conn):
                self.store.upsert(items)
        except RuntimeError:
            self.conn.execute("UPDATE ri_sources SET embed_status='failed', embed_model=NULL, updated_at=? WHERE id=?", (self.ctx.clock.iso(), source_id))
            raise
        self.conn.execute("UPDATE ri_sources SET embed_status='ready', embed_model=?, updated_at=? WHERE id=?", (model_used, self.ctx.clock.iso(), source_id))

    def _embed_job(self, job: Dict[str, Any]) -> None:
        self._src(job)
        self._embed(job, job["source_id"])
        self.conn.execute("UPDATE ri_sources SET status='ready', stage='ready' WHERE id=? AND status IN ('queued','processing')", (job["source_id"],))

    # ------------------------------------------------------------------ URL import
    def _fetch_url(self, job: Dict[str, Any]) -> None:
        d = self._discovery_meta(job)
        if not d:
            return self._fetch_url_inner(job)
        src = self._src(job)
        try:
            return self._fetch_url_inner(job)
        except (UrlRejected, ExtractError) as e:
            if isinstance(e, UrlRejected) and e.code in ("URL_TIMEOUT", "URL_FETCH_FAILED"):
                raise                                                                    # transient: let the job retry instead of giving up on the full text
            self._fallback_to_abstract(job, src, d, getattr(e, "message", "") or "download failed")
        except PipelineError as e:
            if e.code in ("EMPTY_DOCUMENT",):
                self._fallback_to_abstract(job, src, d, e.message)
            else:
                raise

    def _fetch_url_inner(self, job: Dict[str, Any]) -> None:
        src = self._src(job)
        s = self.ctx.settings
        url = (jload(job["payload"], {}) or {}).get("url") or src["url"]
        self._stage(job, "downloading", 15)
        res = self.fetcher.fetch(url, accept=("text/html", "application/xhtml+xml", "application/pdf", "text/plain", "text/markdown"))
        body = res.body
        if body[:5] == b"%PDF-" or res.content_type == "application/pdf":
            fmt = "pdf"
        elif res.content_type in ("text/plain", "text/markdown"):
            fmt = "markdown" if res.content_type == "text/markdown" else "txt"
        else:
            fmt = "html"
        if not body.strip():
            raise PipelineError("EMPTY_DOCUMENT", "That address returned no content.")
        digest = hashlib.sha256(body).hexdigest()
        dup = self.conn.execute("SELECT id FROM rw_sources WHERE workspace_id = ? AND content_hash = ? AND id != ?", (src["workspace_id"], digest, src["id"])).fetchone()
        if dup is not None:
            raise PipelineError("DUPLICATE_SOURCE", "This page has the same content as a source already in the workspace.")
        stored = f"{uuid.uuid4().hex}.bin"
        path = os.path.join(root(self.ctx), stored)
        with open(path, "wb") as f:
            f.write(body)
        os.chmod(path, 0o600)
        try:
            verdict = scan_file(path, s.ri_malware_scan)
        except Exception:                                                              # noqa: BLE001 - infected or scanner required but missing: discard the file
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        self.conn.execute("UPDATE ri_sources SET format=?, byte_size=?, content_sha256=?, stored_name=?, malware=?, updated_at=? WHERE id=?",
                          (fmt, len(body), digest, stored, verdict, self.ctx.clock.iso(), src["id"]))
        self.conn.execute("UPDATE rw_sources SET content_hash=?, byte_size=?, updated_at=? WHERE id=?", (digest, len(body), self.ctx.clock.iso(), src["id"]))
        self._ingest({**job, "kind": "ingest"})

    # ------------------------------------------------------------------ DOI / arXiv
    def _get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        res = self.fetcher.fetch(url, accept=("application/json",), extra_headers=headers)
        try:
            return json.loads(res.body.decode("utf-8", "replace"))
        except ValueError:
            raise PipelineError("UPSTREAM_BAD_RESPONSE", "The metadata service answered in an unexpected format.") from None

    def _resolve(self, job: Dict[str, Any]) -> None:
        src = self._src(job)
        p = jload(job["payload"], {}) or {}
        self._stage(job, "looking up metadata", 20)
        if p.get("kind") == "doi":
            self._resolve_doi(job, src, p["id"])
        elif p.get("kind") == "arxiv":
            self._resolve_arxiv(job, src, p["id"])
        elif p.get("kind") == "record":
            self._resolve_record(job, src, p.get("discovery") or {})
        else:
            raise PipelineError("INVALID_IDENTIFIER", "That identifier is not a DOI or an arXiv ID.")

    # ------------------------------------------------------------------ discovered papers
    @staticmethod
    def _discovery_meta(job: Dict[str, Any]) -> Dict[str, Any]:
        d = (jload(job["payload"], {}) or {}).get("discovery")
        return d if isinstance(d, dict) else {}

    def _resolve_record(self, job, src, d: Dict[str, Any]) -> None:
        """A paper picked in Discover: its metadata and abstract came from a provider when the person searched, so nothing is fetched again. Only what the provider reported is indexed."""
        self.conn.execute("UPDATE ri_sources SET venue=?, updated_at=? WHERE id=?", (d.get("venue"), self.ctx.clock.iso(), src["id"]))
        where = ", ".join(d.get("providers") or []) or "the search provider"
        note = (d.get("note") or "")
        warnings = [f"Only the abstract and bibliographic data reported by {where} were imported. The full text was not downloaded." + (f" {note}" if note else "") + " Answers can cite the abstract only."]
        self._metadata_only(job, src, d.get("title") or src["title"], list(d.get("authors") or []), d.get("published"), d.get("abstract"), list(d.get("keywords") or d.get("fields") or []), warnings)

    def _fallback_to_abstract(self, job, src, d: Dict[str, Any], reason: str) -> None:
        """Open full text was reported but could not be used (paywall, wrong type, unreadable file): keep the abstract and say so, instead of failing or pretending."""
        row = self.conn.execute("SELECT stored_name FROM ri_sources WHERE id=?", (src["id"],)).fetchone()
        self.conn.execute("UPDATE ri_sources SET stored_name=NULL, byte_size=NULL, content_sha256=NULL WHERE id=?", (src["id"],))
        if row and row["stored_name"]:
            remove_stored(self.ctx, row["stored_name"])
        self._resolve_record(job, src, {**d, "note": f"The open full text could not be used ({reason})."})

    def _resolve_doi(self, job, src, doi: str) -> None:
        mail = self.ctx.settings.ri_crossref_mailto
        from urllib.parse import quote
        url = f"https://api.crossref.org/works/{quote(doi, safe='/')}" + (f"?mailto={quote(mail)}" if mail else "")
        try:
            data = self._get_json(url)
        except UrlRejected as e:
            if e.code == "URL_HTTP_ERROR" and "404" in e.message:
                raise PipelineError("IDENTIFIER_NOT_FOUND", "No record was found for that DOI.") from None
            raise
        m = (data or {}).get("message") or {}
        if not m:
            raise PipelineError("IDENTIFIER_NOT_FOUND", "No record was found for that DOI.")
        title = strip_tags((m.get("title") or [""])[0])[:300]
        authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "") for a in (m.get("author") or [])][:40]
        parts = ((m.get("issued") or m.get("published") or {}).get("date-parts") or [[None]])[0]
        published = "-".join(f"{int(x):02d}" if i else str(x) for i, x in enumerate(parts) if x) or None
        abstract = strip_tags(m.get("abstract") or "") or None
        venue = strip_tags((m.get("container-title") or [""])[0]) or None
        self.conn.execute("UPDATE ri_sources SET venue=?, updated_at=? WHERE id=?", (venue, self.ctx.clock.iso(), src["id"]))
        self._metadata_only(job, src, title or doi, authors, published, abstract, m.get("subject") or [])

    def _metadata_only(self, job, src, title, authors, published, abstract, keywords, warnings: Optional[List[str]] = None) -> None:
        if abstract:
            ex = Extracted("metadata", blocks=[Block("heading", "Abstract", None, 1, "abstract"), Block("paragraph", abstract, None, 0, "abstract", "Abstract")], title=title, authors=authors,
                           published=published, abstract=abstract, keywords=list(keywords)[:30],
                           warnings=warnings or ["Only the abstract and bibliographic data were imported (the full text is not available through this identifier). Answers can cite the abstract only."])
            self._index(job, src, ex, access="abstract_only")
            return
        now = self.ctx.clock.iso()
        self.conn.execute("UPDATE ri_sources SET title=?, authors=?, published=?, keywords=?, access='metadata_only', status='ready', stage='ready', chunk_count=0, embed_status='none', "
                          "intelligence=?, updated_at=? WHERE id=?", (title[:300], json.dumps(authors), published, json.dumps(list(keywords)[:30]),
                                                                       json.dumps({"summary": [], "keywords": [], "statistics": [], "claims": [], "warnings": (warnings or []) + ["The record has no abstract and no open full text, so there is nothing to search or cite."], "method": "extractive"}), now, src["id"]))
        self.conn.execute("UPDATE rw_sources SET title=?, status='ready', updated_at=? WHERE id=?", (title[:300], now, src["id"]))

    def _resolve_arxiv(self, job, src, aid: str) -> None:
        res = self.fetcher.fetch(f"https://export.arxiv.org/api/query?id_list={aid}&max_results=1", accept=("application/atom+xml", "application/xml", "text/xml"))
        raw = res.body.decode("utf-8", "replace")
        if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
            raise PipelineError("UPSTREAM_BAD_RESPONSE", "The metadata service answered in an unexpected format.")
        try:
            root_el = ET.fromstring(raw)
        except ET.ParseError:
            raise PipelineError("UPSTREAM_BAD_RESPONSE", "The metadata service answered in an unexpected format.") from None
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
        entry = root_el.find("a:entry", ns)
        title = strip_tags(entry.findtext("a:title", "", ns)) if entry is not None else ""
        if entry is None or not title or title.lower() == "error":
            raise PipelineError("IDENTIFIER_NOT_FOUND", "No arXiv paper was found for that ID.")
        authors = [strip_tags(a.findtext("a:name", "", ns)) for a in entry.findall("a:author", ns)][:40]
        published = (entry.findtext("a:published", "", ns) or "")[:10] or None
        abstract = strip_tags(entry.findtext("a:summary", "", ns)) or None
        venue = "arXiv"
        self.conn.execute("UPDATE ri_sources SET venue=?, updated_at=? WHERE id=?", (venue, self.ctx.clock.iso(), src["id"]))
        self._stage(job, "fetching full text", 35)
        try:
            pdf = self.fetcher.fetch(f"https://arxiv.org/pdf/{aid}", accept=("application/pdf",))
        except UrlRejected:
            pdf = None
        if pdf is not None and pdf.body[:5] == b"%PDF-":
            digest = hashlib.sha256(pdf.body).hexdigest()
            stored = f"{uuid.uuid4().hex}.bin"
            path = os.path.join(root(self.ctx), stored)
            with open(path, "wb") as f:
                f.write(pdf.body)
            os.chmod(path, 0o600)
            verdict = scan_file(path, self.ctx.settings.ri_malware_scan)
            self.conn.execute("UPDATE ri_sources SET stored_name=?, byte_size=?, content_sha256=?, malware=?, format='pdf', updated_at=? WHERE id=?", (stored, len(pdf.body), digest, verdict, self.ctx.clock.iso(), src["id"]))
            src = self._src(job)
            s = self.ctx.settings
            raw_ex = self.extractor("pdf", pdf.body, max_pages=s.ri_pdf_max_pages, timeout_s=s.ri_extract_timeout_s, memory_mb=s.ri_extract_memory_mb, base_url="")
            ex = Extracted(**{**raw_ex, "blocks": [Block(**b) for b in raw_ex["blocks"]]})
            self._index(job, src, ex, access="full_text", meta_override={"title": title, "authors": authors, "published": published, "abstract": abstract})
            return
        self._metadata_only(job, src, title, authors, published, abstract, [])
