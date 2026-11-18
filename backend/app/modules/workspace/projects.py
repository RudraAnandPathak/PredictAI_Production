"""Project use cases. Every method takes the acting Principal and only ever touches that user's projects."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ...db import transaction
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from .constants import MAX_PROJECTS_PER_USER, ProjectStatus, WEvent
from .errors import conflict, invalid, not_found, project_not_active
from .repository import WorkspaceRepository
from .validation import clean_description, clean_metadata, clean_project_name


class ProjectService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, purger) -> None:
        self.ctx, self.repo, self.purger = ctx, repo, purger

    # ---------------------------------------------------------------- shared guards
    def get_owned(self, principal: Principal, project_id: str, *, writable: bool = False):
        """The single place that decides whether a project may be used. Missing, foreign and deleted look identical."""
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        row = self.repo.project(principal.user_id, project_id) if isinstance(project_id, str) and 0 < len(project_id) <= 64 else None
        if row is None:
            raise not_found("project")
        if writable and row["status"] != ProjectStatus.ACTIVE:
            raise project_not_active()
        return row

    def record(self, project_id: str, principal: Principal, event: str, subject_id: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
        """Activity feed (user-visible) plus the security audit trail; both hold identifiers and counts only."""
        self.repo.add_event(project_id=project_id, actor=principal.user_id, event_type=event, subject_id=subject_id,
                            detail=json.dumps(detail, ensure_ascii=False) if detail else None, now=self.ctx.clock.iso())
        self.ctx.audit.record(event, actor=principal.user_id, target=principal.user_id,
                              metadata={"project_id": project_id, "subject_id": subject_id, **(detail or {})})

    def present(self, row, *, counts: bool = True) -> Dict[str, Any]:
        data = {
            "id": row["id"], "name": row["name"], "description": row["description"], "status": row["status"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {}, "retention_class": row["retention_class"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "archived_at": row["archived_at"],
        }
        if counts:
            c = self.repo.project_asset_counts(row["id"])
            data["datasets"], data["documents"] = c["dataset"], c["document"]
        return data

    # ---------------------------------------------------------------- use cases
    def create(self, principal: Principal, name: Any, description: Any = None, metadata: Any = None) -> Dict[str, Any]:
        ctx = self.ctx
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        ctx.limiter.hit("projects:user", principal.user_id)
        clean_name, clean_desc, clean_meta = clean_project_name(name), clean_description(description), clean_metadata(metadata)
        project_id, now = new_id(), ctx.clock.iso()
        with transaction(ctx.conn):
            if self.repo.count_projects(principal.user_id) >= MAX_PROJECTS_PER_USER:
                raise conflict("PROJECT_LIMIT", f"You can have up to {MAX_PROJECTS_PER_USER} projects. Delete one you no longer need.")
            self.repo.create_project(id=project_id, owner=principal.user_id, name=clean_name, description=clean_desc, metadata=clean_meta, now=now)
            self.record(project_id, principal, WEvent.PROJECT_CREATED)
        return self.present(self.repo.project(principal.user_id, project_id))

    def list(self, principal: Principal, *, status: Optional[str] = None, query: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        if status not in (None, ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED):
            raise invalid("status", "Use 'active' or 'archived'.")
        rows, total = self.repo.list_projects(principal.user_id, status=status, query=(query or "").strip()[:100] or None,
                                              limit=max(1, min(int(limit), 100)), offset=max(0, int(offset)))
        return {"items": [self.present(r) for r in rows], "total": total}

    def get(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        return self.present(self.get_owned(principal, project_id))

    def update(self, principal: Principal, project_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.ctx
        row = self.get_owned(principal, project_id)  # renaming an archived project is allowed; data changes are not
        fields: Dict[str, Any] = {}
        if "name" in changes:
            fields["name"] = clean_project_name(changes["name"])
        if "description" in changes:
            fields["description"] = clean_description(changes["description"])
        if "metadata" in changes:
            fields["metadata"] = clean_metadata(changes["metadata"])
        if not fields:
            raise invalid("body", "Nothing to change.", "empty")
        with transaction(ctx.conn):
            self.repo.update_project(row["id"], updated_at=ctx.clock.iso(), **fields)
            self.record(row["id"], principal, WEvent.PROJECT_UPDATED, detail={"fields": ",".join(sorted(fields))})
        return self.present(self.repo.project(principal.user_id, row["id"]))

    def archive(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        ctx = self.ctx
        row = self.get_owned(principal, project_id)
        if row["status"] != ProjectStatus.ARCHIVED:
            now = ctx.clock.iso()
            with transaction(ctx.conn):
                self.repo.update_project(row["id"], status=ProjectStatus.ARCHIVED, archived_at=now, updated_at=now)
                self.record(row["id"], principal, WEvent.PROJECT_ARCHIVED)
        return self.present(self.repo.project(principal.user_id, row["id"]))

    def restore(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        ctx = self.ctx
        row = self.get_owned(principal, project_id)
        if row["status"] != ProjectStatus.ACTIVE:
            with transaction(ctx.conn):
                self.repo.update_project(row["id"], status=ProjectStatus.ACTIVE, archived_at=None, updated_at=ctx.clock.iso())
                self.record(row["id"], principal, WEvent.PROJECT_RESTORED)
        return self.present(self.repo.project(principal.user_id, row["id"]))

    def delete(self, principal: Principal, project_id: str, confirm_name: Any) -> Dict[str, Any]:
        """Permanent. The caller must type the project's name, so a stray request cannot destroy a project."""
        row = self.get_owned(principal, project_id)
        if not isinstance(confirm_name, str) or confirm_name.strip() != row["name"]:
            raise invalid("confirm_name", "Type the project's exact name to confirm deletion.", "mismatch")
        return self.purger.delete_project(principal, row)

    def activity(self, principal: Principal, project_id: str, *, before_id: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
        row = self.get_owned(principal, project_id)
        limit = max(1, min(int(limit), 100))
        rows = self.repo.events(row["id"], before_id=before_id, limit=limit + 1)
        page = rows[:limit]
        return {"items": [{"id": e["id"], "event_type": e["event_type"], "subject_id": e["subject_id"],
                           "detail": json.loads(e["detail"]) if e["detail"] else {}, "occurred_at": e["occurred_at"]} for e in page],
                "next_before_id": page[-1]["id"] if len(rows) > limit else None}
