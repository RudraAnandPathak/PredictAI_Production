"""Resumable, chunked uploads. The browser sends a file in fixed-size chunks; the server stores them under an
opaque temporary prefix, then assembles, validates and records the file in one guarded step.

Guarantees: limits are enforced here regardless of what the client says; sending a chunk or calling complete
twice is harmless (a repeated complete returns the same asset; the same bytes in the same project never create
a second live asset); nothing is ever written under a user-supplied name; the original is written once and kept."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import timedelta
from typing import Any, Dict, Iterator, Optional

from ...core.clock import from_iso, to_iso
from ...core.errors import AppError
from ...db import transaction
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from .safelog import get_logger
from . import keys
from .constants import MAX_ASSETS_PER_PROJECT, AssetKind, AssetStatus, Retention, VersionKind, WEvent
from .errors import bad_request, conflict, invalid, not_found, too_large
from .formats import check_extension, kind_of, validate_content
from .governance import Governance
from .presenters import present_asset, present_upload
from .processing import Processor
from .projects import ProjectService
from .quota import QuotaService
from .repository import WorkspaceRepository
from .validation import sanitize_filename

logger = get_logger()
MAX_OPEN_UPLOADS = 5
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
READ_BLOCK = 1024 * 1024


class UploadService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, projects: ProjectService, quota: QuotaService,
                 governance: Governance, processor: Processor) -> None:
        self.ctx, self.repo, self.storage = ctx, repo, storage
        self.projects, self.quota, self.governance, self.processor = projects, quota, governance, processor

    # ---------------------------------------------------------------- helpers
    def _owned(self, principal: Principal, upload_id: Any):
        require(principal, Perm.DATA_MANAGE_OWN)
        row = self.repo.upload(principal.user_id, upload_id) if isinstance(upload_id, str) and 0 < len(upload_id) <= 64 else None
        if row is None:
            raise not_found("upload")
        return row

    def _present(self, row) -> Dict[str, Any]:
        return present_upload(row, [c["chunk_index"] for c in self.repo.chunks(row["id"])])

    def _asset_payload(self, asset_id: str) -> Dict[str, Any]:
        row = self.ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return present_asset(row, len(self.repo.versions(asset_id)))

    def _expired(self, row) -> bool:
        return row["expires_at"] <= self.ctx.clock.iso()

    # ---------------------------------------------------------------- start
    def start(self, principal: Principal, project_id: str, filename: Any, size: Any, checksum: Any = None) -> Dict[str, Any]:
        ctx, s = self.ctx, self.ctx.settings
        require(principal, Perm.DATA_MANAGE_OWN)
        project = self.projects.get_owned(principal, project_id, writable=True)
        ctx.limiter.hit("upload_start:user", principal.user_id)
        name, extension = sanitize_filename(filename)
        fmt = check_extension(extension)
        kind = kind_of(fmt)
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise invalid("size", "The file is empty or its size is missing.")
        if size > s.upload_max_bytes:
            ctx.audit.record(WEvent.UPLOAD_REJECTED, "denied", actor=principal.user_id, reason="too_large", metadata={"size": size, "limit": s.upload_max_bytes})
            raise too_large(s.upload_max_bytes / (1024 * 1024))
        if checksum is not None and not (isinstance(checksum, str) and _SHA256.match(checksum.lower())):
            raise invalid("checksum", "The checksum must be a SHA-256 value written as 64 hexadecimal characters.")
        checksum = checksum.lower() if checksum else None
        if checksum:
            existing = self.repo.asset_by_checksum(project["id"], checksum)
            if existing is not None:
                ctx.audit.record(WEvent.ASSET_DUPLICATE, actor=principal.user_id, target=principal.user_id, metadata={"project_id": project["id"], "asset_id": existing["id"], "stage": "start"})
                return {"duplicate": True, "asset": self._asset_payload(existing["id"])}
        if self.repo.count_assets(project["id"]) >= MAX_ASSETS_PER_PROJECT:
            raise conflict("PROJECT_FULL", f"A project can hold {MAX_ASSETS_PER_PROJECT} files. Delete files you no longer need or start a new project.")
        if kind == AssetKind.DOCUMENT:
            self.quota.ensure_document_slot(principal, project["id"])
        resumed = self.repo.open_upload_match(principal.user_id, project["id"], name, size, checksum)
        if resumed is not None and not self._expired(resumed):
            return {"duplicate": False, "resumed": True, "upload": self._present(resumed)}
        if self.repo.count_open_uploads(principal.user_id) >= MAX_OPEN_UPLOADS:
            raise conflict("TOO_MANY_UPLOADS", f"You already have {MAX_OPEN_UPLOADS} uploads in progress. Finish or cancel one first.")
        chunk_size = s.upload_chunk_max_bytes
        total = -(-size // chunk_size)
        now = ctx.clock.now()
        upload_id = new_id()
        with transaction(ctx.conn):
            self.repo.create_upload(id=upload_id, project_id=project["id"], owner=principal.user_id, filename=name, size=size, checksum=checksum,
                                    chunk_size=chunk_size, total_chunks=total, now=to_iso(now), expires_at=to_iso(now + timedelta(hours=s.upload_session_ttl_hours)))
            ctx.audit.record(WEvent.UPLOAD_STARTED, actor=principal.user_id, target=principal.user_id,
                             metadata={"project_id": project["id"], "upload_id": upload_id, "size": size, "format": fmt})
        return {"duplicate": False, "resumed": False, "upload": self._present(self.repo.upload(principal.user_id, upload_id))}

    # ---------------------------------------------------------------- inspect
    def status(self, principal: Principal, upload_id: str) -> Dict[str, Any]:
        return {"upload": self._present(self._owned(principal, upload_id))}

    def list_open(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        project = self.projects.get_owned(principal, project_id)
        return {"items": [self._present(u) for u in self.repo.uploads_for_project(principal.user_id, project["id"]) if not self._expired(u)]}

    # ---------------------------------------------------------------- chunks
    def put_chunk(self, principal: Principal, upload_id: str, index: Any, data: bytes, claimed_sha256: Optional[str] = None) -> Dict[str, Any]:
        ctx = self.ctx
        ctx.limiter.hit("workspace_write:user", principal.user_id)
        upload = self._owned(principal, upload_id)
        self.projects.get_owned(principal, upload["project_id"], writable=True)
        if upload["status"] != "open":
            raise conflict("UPLOAD_NOT_OPEN", "This upload is no longer accepting data. Start the upload again.")
        if self._expired(upload):
            raise conflict("UPLOAD_EXPIRED", "This upload took too long and expired. Start the upload again.")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < upload["total_chunks"]:
            raise invalid("chunk", "That chunk number is not part of this upload.")
        last = index == upload["total_chunks"] - 1
        expected = upload["declared_size"] - upload["chunk_size"] * (upload["total_chunks"] - 1) if last else upload["chunk_size"]
        if len(data) != expected:
            raise bad_request("CHUNK_SIZE_MISMATCH", f"This chunk should be {expected} bytes but {len(data)} arrived. Retry the chunk.")
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        if claimed_sha256 and claimed_sha256.lower() != digest:
            raise bad_request("CHUNK_CHECKSUM_MISMATCH", "The chunk was damaged in transit. Retry it.")
        try:
            self.storage.put(keys.chunk_key(upload["id"], index), [data], overwrite=True)
        except (StorageError, OSError) as error:
            logger.warning("Could not store an upload chunk", exc_info=True)
            raise AppError("STORAGE_ERROR", "The file could not be saved right now. Please retry in a moment.", 503) from error
        with transaction(ctx.conn):
            self.repo.put_chunk(upload["id"], index, len(data), digest)
            received = self.repo.chunks(upload["id"])
            self.repo.update_upload(upload["id"], received_bytes=sum(c["size_bytes"] for c in received), updated_at=ctx.clock.iso())
        return {"received_chunks": [c["chunk_index"] for c in received], "received_bytes": sum(c["size_bytes"] for c in received)}

    # ---------------------------------------------------------------- cancel
    def cancel(self, principal: Principal, upload_id: str) -> Dict[str, Any]:
        ctx = self.ctx
        upload = self._owned(principal, upload_id)
        if upload["status"] == "completed":
            raise conflict("UPLOAD_COMPLETED", "This upload already finished. Delete the file from the project instead.")
        if upload["status"] == "assembling":
            raise conflict("UPLOAD_IN_PROGRESS", "This upload is being finalised and cannot be cancelled. Wait a moment.")
        self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
        with transaction(ctx.conn):
            self.repo.delete_upload(upload["id"])
            ctx.audit.record(WEvent.UPLOAD_CANCELLED, actor=principal.user_id, target=principal.user_id, metadata={"project_id": upload["project_id"], "upload_id": upload["id"]})
        return {"cancelled": True}

    # ---------------------------------------------------------------- complete
    def complete(self, principal: Principal, upload_id: str) -> Dict[str, Any]:
        ctx = self.ctx
        upload = self._owned(principal, upload_id)
        if upload["status"] == "completed" and upload["asset_id"]:
            return {"duplicate": False, "asset": self._asset_payload(upload["asset_id"]), "repeat": True}
        if upload["status"] == "assembling":
            raise conflict("UPLOAD_IN_PROGRESS", "This upload is being finalised. Check again in a moment.")
        if upload["status"] != "open":
            raise conflict("UPLOAD_NOT_OPEN", f"This upload cannot be completed ({upload['status']}). Start the upload again.")
        project = self.projects.get_owned(principal, upload["project_id"], writable=True)
        if self._expired(upload):
            raise conflict("UPLOAD_EXPIRED", "This upload took too long and expired. Start the upload again.")
        chunks = self.repo.chunks(upload["id"])
        have = {c["chunk_index"] for c in chunks}
        missing = [i for i in range(upload["total_chunks"]) if i not in have]
        if missing:
            raise bad_request("UPLOAD_INCOMPLETE", f"{len(missing)} part(s) of the file have not arrived yet. Resume the upload to send them.",
                              details=[{"missing_chunks": missing[:50]}])
        if not self.repo.claim_upload(upload["id"], ctx.clock.iso()):  # another request got there first
            fresh = self.repo.upload(principal.user_id, upload["id"])
            if fresh["status"] == "completed" and fresh["asset_id"]:
                return {"duplicate": False, "asset": self._asset_payload(fresh["asset_id"]), "repeat": True}
            raise conflict("UPLOAD_IN_PROGRESS", "This upload is being finalised. Check again in a moment.")
        try:
            return self._assemble(principal, upload, project)
        except AppError as error:
            self._fail(principal, upload, error.code)
            raise
        except Exception:
            self._fail(principal, upload, "INTERNAL_ERROR")
            raise

    def _fail(self, principal: Principal, upload, code: str) -> None:
        try:
            self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
        except (StorageError, OSError):
            logger.warning("Could not remove chunks of a failed upload", exc_info=True)
        self.repo.update_upload(upload["id"], status="failed", error_code=code, updated_at=self.ctx.clock.iso())
        self.ctx.audit.record(WEvent.UPLOAD_REJECTED, "failure", actor=principal.user_id, reason=code.lower(), metadata={"project_id": upload["project_id"], "upload_id": upload["id"]})

    def _chunk_stream(self, upload) -> Iterator[bytes]:
        for index in range(upload["total_chunks"]):
            with self.storage.open(keys.chunk_key(upload["id"], index)) as handle:
                while True:
                    block = handle.read(READ_BLOCK)
                    if not block:
                        break
                    yield block

    def _assemble(self, principal: Principal, upload, project) -> Dict[str, Any]:
        ctx = self.ctx
        name, extension = upload["filename"], upload["filename"].rpartition(".")[2].lower() if "." in upload["filename"] else ""
        asset_id, version_id = new_id(), new_id()
        original = keys.original_key(project["id"], asset_id, version_id)
        try:
            stored = self.storage.put(original, self._chunk_stream(upload), overwrite=False, max_bytes=upload["declared_size"])
        except StorageError as error:
            raise bad_request("UPLOAD_CORRUPT", "The uploaded parts did not add up to the expected file. Start the upload again.") from error
        except OSError as error:
            raise AppError("STORAGE_ERROR", "The file could not be saved right now. Please retry in a moment.", 503) from error

        def discard() -> None:
            try:
                self.storage.delete_prefix(keys.asset_prefix(project["id"], asset_id))
            except (StorageError, OSError):
                logger.warning("Could not remove a rejected upload", exc_info=True)

        try:
            if stored.size != upload["declared_size"]:
                raise bad_request("UPLOAD_CORRUPT", "The uploaded parts did not add up to the expected file size. Start the upload again.")
            if upload["declared_checksum"] and stored.sha256 != upload["declared_checksum"]:
                raise bad_request("CHECKSUM_MISMATCH", "The file arrived damaged (its checksum does not match). Upload it again.")
            existing = self.repo.asset_by_checksum(project["id"], stored.sha256)
            if existing is not None:
                return self._duplicate(principal, upload, existing, discard)
            path = self.storage.local_path(original)
            fmt, kind = validate_content(path, extension)
            if kind == AssetKind.DOCUMENT:
                self.quota.ensure_document_slot(principal, project["id"])
                pages = self.processor.pages_for_quota(path, fmt)
                if pages is not None:
                    self.quota.ensure_pages(principal, pages)
            now = ctx.clock.iso()
            try:
                with transaction(ctx.conn):
                    self.repo.create_asset(id=asset_id, project_id=project["id"], owner=principal.user_id, kind=kind, name=name, detected_format=fmt,
                                           status=AssetStatus.PROCESSING, checksum=stored.sha256, size=stored.size, now=now)
                    self.repo.create_version(id=version_id, asset_id=asset_id, project_id=project["id"], owner=principal.user_id, number=1, kind=VersionKind.ORIGINAL,
                                             parent=None, transformation_id=None, storage_key=original, canonical_key=None, checksum=stored.sha256,
                                             size=stored.size, fmt=fmt, rows=None, cols=None, created_by=principal.user_id, now=now)
                    self.repo.update_asset(asset_id, current_version_id=version_id)
                    self.repo.update_upload(upload["id"], status="completed", asset_id=asset_id, updated_at=now)
                    self.projects.record(project["id"], principal, WEvent.ASSET_CREATED, asset_id, {"kind": kind, "format": fmt, "size": stored.size})
            except sqlite3.IntegrityError:  # a concurrent identical upload won the race
                racer = self.repo.asset_by_checksum(project["id"], stored.sha256)
                if racer is None:
                    raise
                return self._duplicate(principal, upload, racer, discard)
        except Exception:
            discard()
            raise
        # The chunks are no longer needed once the original is safely stored.
        try:
            self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
            self.repo.delete_chunks(upload["id"])
        except (StorageError, OSError):
            logger.warning("Could not remove upload chunks", exc_info=True)
        self._process(principal, asset_id, version_id, path)
        return {"duplicate": False, "asset": self._asset_payload(asset_id)}

    def _duplicate(self, principal: Principal, upload, existing, discard) -> Dict[str, Any]:
        ctx = self.ctx
        discard()
        try:
            self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
        except (StorageError, OSError):
            pass
        with transaction(ctx.conn):
            self.repo.delete_chunks(upload["id"])
            self.repo.update_upload(upload["id"], status="completed", asset_id=existing["id"], updated_at=ctx.clock.iso())
            ctx.audit.record(WEvent.ASSET_DUPLICATE, actor=principal.user_id, target=principal.user_id,
                             metadata={"project_id": existing["project_id"], "asset_id": existing["id"], "stage": "complete"})
        return {"duplicate": True, "asset": self._asset_payload(existing["id"])}

    def _process(self, principal: Principal, asset_id: str, version_id: str, path) -> None:
        ctx = self.ctx
        asset = ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        version = self.repo.version(principal.user_id, version_id)
        try:
            fields = self.processor.process(principal, asset, version, path) or {}
            self.repo.update_asset(asset_id, status=AssetStatus.READY, status_code=None, status_detail=None, updated_at=ctx.clock.iso(), **fields)
        except AppError as error:
            self.repo.update_asset(asset_id, status=AssetStatus.FAILED, status_code=error.code, status_detail=error.message[:500], updated_at=ctx.clock.iso())
            ctx.audit.record(WEvent.ASSET_FAILED, "failure", actor=principal.user_id, reason=error.code.lower(), metadata={"asset_id": asset_id})
        except Exception:
            logger.exception("Unexpected error while processing an uploaded file")
            self.repo.update_asset(asset_id, status=AssetStatus.FAILED, status_code="PROCESSING_ERROR",
                                   status_detail="The file could not be processed because of an unexpected problem. It was kept; you can delete it and try again.",
                                   updated_at=ctx.clock.iso())
            ctx.audit.record(WEvent.ASSET_FAILED, "failure", actor=principal.user_id, reason="processing_error", metadata={"asset_id": asset_id})
