"""Account-owned research workspaces: create, list, rename, archive, restore, delete, and the per-role limits that Phase 10 will enforce when it adds sources.

Ownership: every query filters by owner_user_id, and another person's workspace answers 404 exactly like a missing one. The owner role gets no read access to anyone's workspace
content here; platform owners see counts only through the Phase 7 owner overview.
Limits: standard accounts use settings defaults that the platform owner can override (own_quota_policy). Special and Owner accounts are not limited by those, but a fixed
safeguard ceiling still applies so no account can exhaust the database."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ...db import transaction
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from ..p7 import choice, clean
from ..workspace.errors import conflict, invalid, not_found, quota

SOURCE_KINDS = ("pdf", "docx", "web_article", "academic_paper", "csv", "xlsx")
STATUSES = ("active", "archived")
EV_CREATED, EV_CHANGED, EV_DELETED, EV_DENIED = "rw.workspace.created", "rw.workspace.changed", "rw.workspace.deleted", "rw.quota.denied"
CHILD_TABLES = ("rw_notes", "rw_messages", "rw_conversations", "rw_sources")   # deletion order (children first); account deletion is in workspace/phase7_purge.user_rows


class ResearchSpaceService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    # ------------------------------------------------------------------ limits
    def _policy(self, key: str, configured: int) -> int:
        try:
            row = self.conn.execute("SELECT value FROM own_quota_policy WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return configured
        return int(row["value"]) if row is not None else configured

    def limits(self, principal: Principal) -> Dict[str, Any]:
        s = self.ctx.settings
        unlimited = principal.can(Perm.UPLOADS_UNLIMITED)
        if unlimited:
            max_ws, max_src = s.safeguard_max_research_workspaces, s.safeguard_max_research_sources
        else:
            max_ws = self._policy("standard_max_research_workspaces", s.standard_max_research_workspaces)
            max_src = self._policy("standard_max_research_sources", s.standard_max_research_sources)
        return {"tier": "owner" if principal.is_owner else ("special" if unlimited else "standard"), "max_workspaces": max_ws, "max_sources_per_workspace": max_src,
                "workspaces_used": self._count(principal.user_id), "supported_source_kinds": list(SOURCE_KINDS), "safeguard_applies": unlimited}

    def _count(self, user_id: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM rw_workspaces WHERE owner_user_id = ?", (user_id,)).fetchone()[0])

    def ensure_source_slot(self, principal: Principal, workspace_id: str) -> None:
        """For Phase 10: call before adding a source. Raises 403 SOURCE_LIMIT_REACHED at the account's limit."""
        self.get(principal, workspace_id)
        used = int(self.conn.execute("SELECT COUNT(*) FROM rw_sources WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, principal.user_id)).fetchone()[0])
        limit = self.limits(principal)["max_sources_per_workspace"]
        if used >= limit:
            self.ctx.audit.record(EV_DENIED, "denied", actor=principal.user_id, reason="sources", metadata={"limit": limit})
            raise quota("SOURCE_LIMIT_REACHED", f"This workspace holds {limit} sources, the most your account allows. Remove a source or activate Special access.")

    # ------------------------------------------------------------------ workspaces
    def _row(self, principal: Principal, workspace_id: Any) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if row is None:
            raise not_found("research workspace")
        return row

    def _present(self, row: sqlite3.Row) -> Dict[str, Any]:
        n = lambda t: int(self.conn.execute(f"SELECT COUNT(*) FROM {t} WHERE workspace_id = ?", (row["id"],)).fetchone()[0])  # noqa: E731 - table names are constants below
        return {"id": row["id"], "name": row["name"], "description": row["description"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                "counts": {"sources": n("rw_sources"), "conversations": n("rw_conversations"), "notes": n("rw_notes")}}

    def create(self, principal: Principal, name: Any, description: Any = None) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("rw_write:user", principal.user_id)
        name = clean(name, "name", maximum=120)
        description = clean(description, "description", maximum=2000, required=False)
        limit = self.limits(principal)["max_workspaces"]
        if self._count(principal.user_id) >= limit:
            self.ctx.audit.record(EV_DENIED, "denied", actor=principal.user_id, reason="workspaces", metadata={"limit": limit})
            raise quota("WORKSPACE_LIMIT_REACHED", f"Your account can keep {limit} research workspaces. Delete one to free a slot, or activate Special access.")
        wid, now = new_id(), self.ctx.clock.iso()
        try:
            self.conn.execute("INSERT INTO rw_workspaces (id, owner_user_id, name, description, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                              (wid, principal.user_id, name, description, "active", now, now))
        except sqlite3.IntegrityError:
            raise conflict("WORKSPACE_NAME_TAKEN", "You already have a research workspace with that name.") from None
        self.ctx.audit.record(EV_CREATED, "success", actor=principal.user_id, metadata={"workspace_id": wid})
        return self._present(self._row(principal, wid))

    def list(self, principal: Principal, status: Optional[str] = None) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        sql, args = "SELECT * FROM rw_workspaces WHERE owner_user_id = ?", [principal.user_id]
        if status is not None:
            sql, args = sql + " AND status = ?", args + [choice(status, "status", STATUSES)]
        rows = self.conn.execute(sql + " ORDER BY updated_at DESC, id", args).fetchall()
        return {"items": [self._present(r) for r in rows], "limits": self.limits(principal)}

    def get(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        return self._present(self._row(principal, workspace_id))

    def update(self, principal: Principal, workspace_id: Any, *, name: Any = None, description: Any = None, status: Any = None) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("rw_write:user", principal.user_id)
        row = self._row(principal, workspace_id)
        new_name = clean(name, "name", maximum=120) if name is not None else row["name"]
        new_desc = clean(description, "description", maximum=2000, required=False) if description is not None else row["description"]
        new_status = choice(status, "status", STATUSES) if status is not None else row["status"]
        try:
            self.conn.execute("UPDATE rw_workspaces SET name = ?, description = ?, status = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?",
                              (new_name, new_desc, new_status, self.ctx.clock.iso(), row["id"], principal.user_id))
        except sqlite3.IntegrityError:
            raise conflict("WORKSPACE_NAME_TAKEN", "You already have a research workspace with that name.") from None
        self.ctx.audit.record(EV_CHANGED, "success", actor=principal.user_id, metadata={"workspace_id": row["id"], "status": new_status})
        return self._present(self._row(principal, row["id"]))

    def delete(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("rw_write:user", principal.user_id)
        row = self._row(principal, workspace_id)
        from ..research_intelligence.core.purge import purge_workspace          # lazy: Phase 10 data (sources, passages, vectors, stored files) goes first
        with transaction(self.conn):
            purge_workspace(self.conn, self.ctx.settings, self.ctx.clock.iso, row["id"], principal.user_id)
            for table in CHILD_TABLES:
                self.conn.execute(f"DELETE FROM {table} WHERE workspace_id = ? AND owner_user_id = ?", (row["id"], principal.user_id))
            self.conn.execute("DELETE FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (row["id"], principal.user_id))
        self.ctx.audit.record(EV_DELETED, "success", actor=principal.user_id, metadata={"workspace_id": row["id"]})
        return {"deleted": True, "id": row["id"]}
