"""Upload quotas. Standard accounts: a fixed number of documents and pages per document. Special and Owner accounts
are unlimited through the existing `uploads.unlimited` permission, which is recomputed from the database on every
request, so revoking Special access takes effect on the very next upload."""
from __future__ import annotations

from typing import Any, Dict

from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..identity.context import Context
from .constants import AssetKind, WEvent
from .errors import quota
from .repository import WorkspaceRepository


class QuotaService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository) -> None:
        self.ctx, self.repo = ctx, repo

    def _limit(self, key: str, configured: int) -> int:
        """The configured default, unless the platform owner set a policy value (Phase 7 owner command center). Databases without the policy table keep the configured value."""
        try:
            row = self.ctx.conn.execute("SELECT value FROM own_quota_policy WHERE key = ?", (key,)).fetchone()
        except Exception:  # noqa: BLE001 - table not present on a database that has not run migration 0006
            return configured
        return int(row["value"]) if row is not None else configured

    def unlimited(self, principal: Principal) -> bool:
        return principal.can(Perm.UPLOADS_UNLIMITED)

    def summary(self, principal: Principal) -> Dict[str, Any]:
        s = self.ctx.settings
        unlimited = self.unlimited(principal)
        return {
            "unlimited": unlimited,
            "documents_used": self.repo.count_documents(principal.user_id),
            "max_documents": None if unlimited else self._limit("standard_max_documents", s.standard_max_documents),
            "max_pages_per_document": None if unlimited else self._limit("standard_max_pages", s.standard_max_pages),
            "max_file_mb": round(s.upload_max_bytes / (1024 * 1024), 1),
        }

    def ensure_document_slot(self, principal: Principal, project_id: str) -> None:
        if self.unlimited(principal):
            return
        limit = self._limit("standard_max_documents", self.ctx.settings.standard_max_documents)
        if self.repo.count_documents(principal.user_id) >= limit:
            self.ctx.audit.record(WEvent.QUOTA_DENIED, "denied", actor=principal.user_id, reason="documents", metadata={"limit": limit})
            raise quota("DOCUMENT_LIMIT_REACHED",
                        f"Standard accounts can keep {limit} documents. Delete a document to free a slot, or activate Special access for unlimited uploads.")

    def ensure_pages(self, principal: Principal, pages: int) -> None:
        if self.unlimited(principal):
            return
        limit = self._limit("standard_max_pages", self.ctx.settings.standard_max_pages)
        if pages > limit:
            self.ctx.audit.record(WEvent.QUOTA_DENIED, "denied", actor=principal.user_id, reason="pages", metadata={"limit": limit, "pages": pages})
            raise quota("PAGE_LIMIT_EXCEEDED",
                        f"This document has {pages} pages. Standard accounts can upload documents of up to {limit} pages. Split the document, or activate Special access.")

    def applies_to(self, kind: str) -> bool:
        return kind == AssetKind.DOCUMENT
