"""Sources and workspace AI settings (person side). Everything is bounded by the signed-in owner and an owned workspace; another person's id answers 404 exactly like a missing one.

Adding a source is cheap and immediate: the file or address is validated, de-duplicated, stored and queued; the heavy work (reading, indexing, embedding) happens in the job queue so a
request never waits on a parser. The Phase 8 `rw_sources` row is the shared record (limits, hash de-duplication, note links); `ri_sources` holds what Phase 10 knows about it."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from ....db import transaction
from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...p7 import choice, jload
from ...researchspace.service import ResearchSpaceService
from ...workspace.errors import conflict, invalid, not_found
from ..core import purge
from ..core.common import (EV_POLICY, EV_SOURCE_ADDED, EV_SOURCE_CHANGED, EV_SOURCE_DELETED, check_daily, limits, record_usage, root)
from ..security.files import validate_upload
from ..security.malware import scan_file
from ..security.urlguard import UrlRejected, validate_url
from .jobs import JobService

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+", re.I)
ARXIV_NEW = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d.])")
ARXIV_OLD = re.compile(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
RW_KIND = {"pdf": "pdf", "docx": "docx", "txt": "docx", "markdown": "docx", "csv": "csv", "xlsx": "xlsx", "html": "web_article", "metadata": "academic_paper"}


def parse_identifier(text: str) -> Optional[Dict[str, str]]:
    t = (text or "").strip()
    if not t or len(t) > 300:
        return None
    low = t.lower()
    if "arxiv" in low:
        m = ARXIV_NEW.search(t) or ARXIV_OLD.search(t)
        return {"kind": "arxiv", "id": m.group(1)} if m else None
    m = DOI_RE.search(t)
    if m and (low.startswith(("10.", "doi:", "https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi.org/")) or "doi" in low):
        return {"kind": "doi", "id": m.group(0).rstrip(".,;)").lower()}
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", t):
        return {"kind": "arxiv", "id": re.sub(r"v\d+$", "", t)}
    return None


def normalise_url(url: str) -> str:
    sp = urlsplit(url.strip())
    host = (sp.hostname or "").lower()
    path = sp.path if sp.path else "/"
    return f"{sp.scheme.lower()}://{host}{path}" + (f"?{sp.query}" if sp.query else "")


class SourceService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn
        self.jobs = JobService(ctx)
        self.spaces = ResearchSpaceService(ctx)

    # ------------------------------------------------------------------ shared
    def _workspace(self, principal: Principal, workspace_id: Any, *, writable: bool = False) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        row = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if row is None:
            raise not_found("research workspace")
        if writable and row["status"] != "active":
            raise conflict("WORKSPACE_ARCHIVED", "This workspace is archived. Restore it to add or change sources.")
        return row

    def _source(self, principal: Principal, source_id: Any, workspace_id: Optional[str] = None) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        sql, args = "SELECT * FROM ri_sources WHERE id = ? AND owner_user_id = ?", [source_id if isinstance(source_id, str) else "", principal.user_id]
        if workspace_id:
            sql, args = sql + " AND workspace_id = ?", args + [workspace_id]
        row = self.conn.execute(sql, args).fetchone()
        if row is None:
            raise not_found("source")
        return row

    def present(self, r: sqlite3.Row, *, detail: bool = False) -> Dict[str, Any]:
        intel = jload(r["intelligence"], None)
        out = {"id": r["id"], "workspace_id": r["workspace_id"], "title": r["title"], "format": r["format"], "origin": r["origin"], "authors": jload(r["authors"], []), "published": r["published"],
               "doi": r["doi"], "url": r["url"], "identifier": r["identifier"], "venue": r["venue"], "access": r["access"], "status": r["status"], "stage": r["stage"],
               "error_code": r["error_code"], "error_message": r["error_message"], "page_count": r["page_count"], "chunk_count": r["chunk_count"], "table_count": r["table_count"],
               "embed_status": r["embed_status"], "embed_model": r["embed_model"],
               "embedding_kind": None if not r["embed_model"] else ("lexical" if r["embed_model"] == "builtin-hash-384" else "semantic"),
               "malware": r["malware"], "injection_flags": r["injection_flags"], "included": bool(r["included"]), "byte_size": r["byte_size"], "version": r["version"],
               "keywords": jload(r["keywords"], []), "created_at": r["created_at"], "updated_at": r["updated_at"]}
        if detail:
            out.update({"abstract": r["abstract"], "structure": jload(r["structure"], []), "references": jload(r["refs"], []), "intelligence": intel,
                        "warnings": (intel or {}).get("warnings", [])})
        return out

    def _guard_add(self, principal: Principal, workspace_id: Any) -> sqlite3.Row:
        ws = self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_import:user", principal.user_id)
        check_daily(self.ctx, principal, "import", "imports_per_day", "You have reached today's limit of {limit} source imports. It resets tomorrow, or activate Special access for a higher limit.")
        self.spaces.ensure_source_slot(principal, ws["id"])
        return ws

    def _insert(self, principal: Principal, ws: sqlite3.Row, *, fmt: str, origin: str, title: str, url: Optional[str] = None, doi: Optional[str] = None, identifier: Optional[str] = None,
                content_hash: Optional[str] = None, stored_name: Optional[str] = None, byte_size: Optional[int] = None, malware: str = "not_scanned", job_kind: str, payload: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
        sid, now = str(uuid.uuid4()), self.ctx.clock.iso()
        title = (title or "Untitled")[:300]
        try:
            with transaction(self.conn):
                self.conn.execute("INSERT INTO rw_sources (id, workspace_id, owner_user_id, kind, title, url, content_hash, byte_size, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (sid, ws["id"], principal.user_id, RW_KIND[fmt], title, url, content_hash, byte_size, "pending", now, now))
                self.conn.execute("INSERT INTO ri_sources (id, workspace_id, owner_user_id, format, origin, title, doi, url, identifier, byte_size, content_sha256, stored_name, malware, status, stage, created_at, updated_at) "
                                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, ws["id"], principal.user_id, fmt, origin, title, doi, url, identifier, byte_size, content_hash, stored_name, malware, "queued", "queued", now, now))
                self.jobs.enqueue(principal.user_id, ws["id"], job_kind, sid, payload)
                record_usage(self.ctx, principal.user_id, ws["id"], "import", 1, origin)
        except sqlite3.IntegrityError:
            from ..core.common import remove_stored
            remove_stored(self.ctx, stored_name)
            raise conflict("DUPLICATE_SOURCE", "This source is already in the workspace.") from None
        self.ctx.audit.record(EV_SOURCE_ADDED, "success", actor=principal.user_id, metadata={"source_id": sid, "workspace_id": ws["id"], "origin": origin, "format": fmt})
        return self.present(self._source(principal, sid))

    # ------------------------------------------------------------------ add
    def add_file(self, principal: Principal, workspace_id: Any, filename: Any, data: bytes) -> Dict[str, Any]:
        ws = self._guard_add(principal, workspace_id)
        max_bytes = int(limits(self.ctx, principal)["max_upload_mb"] * 1024 * 1024)
        v = validate_upload(filename, data, max_bytes=max_bytes)
        digest = hashlib.sha256(data).hexdigest()
        if self.conn.execute("SELECT 1 FROM rw_sources WHERE workspace_id = ? AND content_hash = ?", (ws["id"], digest)).fetchone():
            raise conflict("DUPLICATE_SOURCE", "This exact file is already in the workspace.")
        stored = f"{uuid.uuid4().hex}.bin"
        path = os.path.join(root(self.ctx), stored)
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, 0o600)
        try:
            verdict = scan_file(path, self.ctx.settings.ri_malware_scan)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        title = os.path.splitext(v.display_name)[0][:300] or "Untitled"
        return self._insert(principal, ws, fmt=v.format, origin="upload", title=title, content_hash=digest, stored_name=stored, byte_size=len(data), malware=verdict, job_kind="ingest")

    def add_reference(self, principal: Principal, workspace_id: Any, text: Any) -> Dict[str, Any]:
        """A web address, a DOI or an arXiv ID."""
        if not isinstance(text, str) or not text.strip():
            raise invalid("reference", "Enter a web address, a DOI or an arXiv ID.")
        text = text.strip()
        ident = parse_identifier(text)
        if ident is None and not re.match(r"https?://", text, re.I):
            raise invalid("reference", "That is not a web address, a DOI or an arXiv ID.")
        if ident is not None and re.match(r"https?://", text, re.I) and not re.search(r"doi\.org|arxiv\.org", text, re.I):
            ident = None                                                                # an ordinary page that merely mentions an ID
        ws = self._guard_add(principal, workspace_id)
        if ident is not None:
            key = ident["id"]
            if ident["kind"] == "doi":
                if self.conn.execute("SELECT 1 FROM ri_sources WHERE workspace_id = ? AND doi = ?", (ws["id"], key)).fetchone():
                    raise conflict("DUPLICATE_SOURCE", "This DOI is already in the workspace.")
                return self._insert(principal, ws, fmt="metadata", origin="doi", title=f"DOI {key}", doi=key, identifier=key, content_hash=hashlib.sha256(f"doi:{key}".encode()).hexdigest(),
                                    job_kind="resolve_identifier", payload=ident)
            return self._insert(principal, ws, fmt="metadata", origin="arxiv", title=f"arXiv {key}", identifier=key, content_hash=hashlib.sha256(f"arxiv:{key}".encode()).hexdigest(),
                                job_kind="resolve_identifier", payload=ident)
        try:
            # Early, DNS-free check: scheme, credentials, port and literal addresses. The name is resolved, checked and pinned at fetch time (SafeFetcher), which is the check that counts.
            validate_url(text, resolver=lambda host, port: ["93.184.216.34"], allow_private=self.ctx.settings.ri_allow_private_urls)
        except UrlRejected as e:
            raise invalid("reference", e.message, e.code.lower()) from None
        url = normalise_url(text)
        if self.conn.execute("SELECT 1 FROM ri_sources WHERE workspace_id = ? AND url = ?", (ws["id"], url)).fetchone():
            raise conflict("DUPLICATE_SOURCE", "This web address is already in the workspace.")
        host = urlsplit(url).hostname or "web page"
        return self._insert(principal, ws, fmt="html", origin="url", title=host[:300], url=url, job_kind="fetch_url", payload={"url": url})

    def add_discovered(self, principal: Principal, workspace_id: Any, rec: Dict[str, Any], *, full_text: bool, library_id: str) -> Dict[str, Any]:
        """A saved discovery record becomes a workspace source. With `full_text` and an open-access address reported by a provider the worker tries to download it (and falls back to the
        abstract, saying so, if that fails); otherwise only the abstract and metadata the provider reported are indexed. Nothing is claimed to be downloaded here."""
        ws = self._guard_add(principal, workspace_id)
        doi, arxiv = rec.get("doi"), rec.get("arxiv_id")
        if doi and self.conn.execute("SELECT 1 FROM ri_sources WHERE workspace_id = ? AND doi = ?", (ws["id"], doi)).fetchone():
            raise conflict("DUPLICATE_SOURCE", "This paper (DOI) is already in the workspace.")
        if arxiv and self.conn.execute("SELECT 1 FROM ri_sources WHERE workspace_id = ? AND identifier = ?", (ws["id"], arxiv)).fetchone():
            raise conflict("DUPLICATE_SOURCE", "This arXiv paper is already in the workspace.")
        meta = {k: rec.get(k) for k in ("title", "authors", "published", "abstract", "venue", "doi", "arxiv_id", "fields", "license")}
        meta["keywords"] = list(rec.get("keywords") or rec.get("fields") or [])[:30]
        meta["providers"] = sorted((rec.get("providers") or {}).keys())
        meta["authors"] = list(meta["authors"] or [])[:60]
        title = (rec.get("title") or "Untitled")[:300]
        oa = rec.get("oa_url") if full_text else None
        if oa:
            try:
                validate_url(oa, resolver=lambda host, port: ["93.184.216.34"], allow_private=self.ctx.settings.ri_allow_private_urls)
            except UrlRejected:
                oa = None                                                               # an address that fails the safety check is never fetched; the abstract is imported instead
        if oa:
            url = normalise_url(oa)
            if self.conn.execute("SELECT 1 FROM ri_sources WHERE workspace_id = ? AND url = ?", (ws["id"], url)).fetchone():
                raise conflict("DUPLICATE_SOURCE", "This web address is already in the workspace.")
            out = self._insert(principal, ws, fmt="html", origin="discovery", title=title, url=url, doi=doi, identifier=arxiv, job_kind="fetch_url", payload={"url": url, "discovery": meta})
        else:
            out = self._insert(principal, ws, fmt="metadata", origin="discovery", title=title, doi=doi, identifier=arxiv,
                               content_hash=hashlib.sha256(f"discovery:{library_id}:{ws['id']}".encode()).hexdigest(), job_kind="resolve_identifier", payload={"kind": "record", "discovery": meta})
        self.conn.execute("INSERT OR REPLACE INTO ri_library_links (source_id, library_id, workspace_id, owner_user_id, created_at) VALUES (?,?,?,?,?)",
                          (out["id"], library_id, ws["id"], principal.user_id, self.ctx.clock.iso()))
        out["import_plan"] = ("The worker will try to download the open-access full text that the provider reported; if that fails the abstract is kept and the source says so." if oa
                              else "Only the abstract and bibliographic data reported by the provider will be imported. The full text is not downloaded.")
        return out

    # ------------------------------------------------------------------ read
    def list(self, principal: Principal, workspace_id: Any, status: Optional[str] = None) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        sql, args = "SELECT * FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ?", [ws["id"], principal.user_id]
        if status:
            sql, args = sql + " AND status = ?", args + [choice(status, "status", ("queued", "processing", "ready", "failed", "cancelled"))]
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id", args).fetchall()
        return {"items": [self.present(r) for r in rows], "limits": limits(self.ctx, principal)}

    def get(self, principal: Principal, workspace_id: Any, source_id: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id)
        return self.present(self._source(principal, source_id, workspace_id), detail=True)

    def chunks(self, principal: Principal, workspace_id: Any, source_id: Any, offset: int = 0, limit: int = 20) -> Dict[str, Any]:
        self._workspace(principal, workspace_id)
        src = self._source(principal, source_id, workspace_id)
        limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
        rows = self.conn.execute("SELECT * FROM ri_chunks WHERE source_id = ? AND owner_user_id = ? ORDER BY ordinal LIMIT ? OFFSET ?", (src["id"], principal.user_id, limit, offset)).fetchall()
        return {"items": [self._chunk(r) for r in rows], "total": src["chunk_count"]}

    @staticmethod
    def _chunk(r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "source_id": r["source_id"], "ordinal": r["ordinal"], "text": r["text"], "section": r["section"], "page": r["page_start"], "page_end": r["page_end"], "locator": r["locator"],
                "words": r["words"], "flagged": bool(r["flagged"])}

    def passage(self, principal: Principal, workspace_id: Any, chunk_id: Any) -> Dict[str, Any]:
        """The cited passage and its neighbours, for click-through from a citation."""
        self._workspace(principal, workspace_id)
        r = self.conn.execute("SELECT * FROM ri_chunks WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (chunk_id if isinstance(chunk_id, str) else "", workspace_id, principal.user_id)).fetchone()
        if r is None:
            raise not_found("passage")
        src = self.conn.execute("SELECT title FROM ri_sources WHERE id = ?", (r["source_id"],)).fetchone()
        near = self.conn.execute("SELECT * FROM ri_chunks WHERE source_id = ? AND ordinal IN (?, ?) AND owner_user_id = ?", (r["source_id"], r["ordinal"] - 1, r["ordinal"] + 1, principal.user_id)).fetchall()
        return {"source_title": src["title"] if src else None, "passage": self._chunk(r), "before": next((self._chunk(n) for n in near if n["ordinal"] < r["ordinal"]), None),
                "after": next((self._chunk(n) for n in near if n["ordinal"] > r["ordinal"]), None)}

    # ------------------------------------------------------------------ change
    def update(self, principal: Principal, workspace_id: Any, source_id: Any, *, included: Optional[bool] = None, title: Any = None) -> Dict[str, Any]:
        self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        src = self._source(principal, source_id, workspace_id)
        sets, args = [], []
        if included is not None:
            sets.append("included = ?"); args.append(1 if included else 0)
        if title is not None:
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 300:
                raise invalid("title", "Enter a title of up to 300 characters.")
            sets.append("title = ?"); args.append(re.sub(r"[\x00-\x1f\x7f]", "", title.strip()))
        if not sets:
            return self.present(src)
        now = self.ctx.clock.iso()
        self.conn.execute(f"UPDATE ri_sources SET {', '.join(sets)}, updated_at = ? WHERE id = ? AND owner_user_id = ?", [*args, now, src["id"], principal.user_id])
        if title is not None:
            self.conn.execute("UPDATE rw_sources SET title = ?, updated_at = ? WHERE id = ?", (args[-1] if included is None else args[-1], now, src["id"]))
        self.ctx.audit.record(EV_SOURCE_CHANGED, "success", actor=principal.user_id, metadata={"source_id": src["id"], "included": included})
        return self.present(self._source(principal, src["id"]))

    def reindex(self, principal: Principal, workspace_id: Any, source_id: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_job:user", principal.user_id)
        src = self._source(principal, source_id, workspace_id)
        if src["status"] in ("queued", "processing"):
            raise conflict("SOURCE_BUSY", "This source is still being processed.")
        if src["origin"] in ("doi", "arxiv") and not src["stored_name"]:
            kind, payload = "resolve_identifier", {"kind": src["origin"], "id": src["identifier"]}
        elif src["stored_name"]:
            kind, payload = "reindex", {}
        else:
            raise conflict("NOTHING_TO_REINDEX", "This source has no stored content to index again.")
        now = self.ctx.clock.iso()
        with transaction(self.conn):
            self.conn.execute("UPDATE ri_sources SET status='queued', stage='queued for re-indexing', error_code=NULL, error_message=NULL, updated_at=? WHERE id=?", (now, src["id"]))
            self.conn.execute("UPDATE rw_sources SET status='pending', updated_at=? WHERE id=?", (now, src["id"]))
            jid = self.jobs.enqueue(principal.user_id, workspace_id, kind, src["id"], payload)
        self.ctx.audit.record(EV_SOURCE_CHANGED, "success", actor=principal.user_id, metadata={"source_id": src["id"], "action": "reindex"})
        return {"source": self.present(self._source(principal, src["id"])), "job_id": jid}

    def delete(self, principal: Principal, workspace_id: Any, source_id: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        src = self._source(principal, source_id, workspace_id)
        with transaction(self.conn):
            counts = purge.purge_source(self.conn, self.ctx.settings, self.ctx.clock.iso, workspace_id, principal.user_id, src["id"])
        self.ctx.audit.record(EV_SOURCE_DELETED, "success", actor=principal.user_id, metadata={"source_id": src["id"], "workspace_id": workspace_id})
        return {"deleted": True, "id": src["id"], "removed": counts}

    # ------------------------------------------------------------------ AI data policy
    def settings(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        r = self.conn.execute("SELECT * FROM ri_workspace_settings WHERE workspace_id = ? AND owner_user_id = ?", (ws["id"], principal.user_id)).fetchone()
        from ..policy import external_allowed, person_allows_external
        person = person_allows_external(self.conn, self.ctx.settings, principal.user_id)
        policy = "external_allowed" if external_allowed(self.conn, self.ctx.settings, principal.user_id, ws["id"]) else "local_only"
        return {"workspace_id": ws["id"], "ai_data_policy": policy, "consent_at": r["consent_at"] if r else None, "your_external_ai_setting": person,
                "explanation": ("Your documents are only ever sent to AI models running on this computer. Answers fall back to extractive quotes when no local model is available." if policy == "local_only"
                                else "Passages of your documents may be sent to the external AI provider the platform owner enabled when you ask a question. Nothing else leaves the platform.")}

    def set_policy(self, principal: Principal, workspace_id: Any, policy: Any, confirm: bool = False) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id, writable=True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        policy = choice(policy, "ai_data_policy", ("local_only", "external_allowed"))
        if policy == "external_allowed" and confirm is not True:
            raise invalid("confirm", "Confirm that you understand parts of your documents may be sent to an external AI provider.", "confirmation_required")
        now = self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_workspace_settings (workspace_id, owner_user_id, ai_data_policy, consent_at, updated_at) VALUES (?,?,?,?,?) "
                          "ON CONFLICT(workspace_id) DO UPDATE SET ai_data_policy=excluded.ai_data_policy, consent_at=excluded.consent_at, updated_at=excluded.updated_at",
                          (ws["id"], principal.user_id, policy, now if policy == "external_allowed" else None, now))
        self.ctx.audit.record(EV_POLICY, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "policy": policy})
        return self.settings(principal, workspace_id)
