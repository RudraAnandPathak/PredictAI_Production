"""Reading, downloading and deleting data assets. Every lookup is scoped to the signed-in owner."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

from ...core.clock import to_iso

from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..storage import StorageBackend, StorageError
from . import keys
from .constants import AssetStatus, MAX_RETENTION_DAYS, WEvent
from .errors import invalid, not_found
from .governance import Governance
from .presenters import present_asset
from .projects import ProjectService
from .repository import WorkspaceRepository

BLOCK = 1024 * 1024


class AssetService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, projects: ProjectService, governance: Governance) -> None:
        self.ctx, self.repo, self.storage, self.projects, self.governance = ctx, repo, storage, projects, governance

    def get_owned(self, principal: Principal, asset_id: Any, *, writable: bool = False):
        """The single place that decides whether an asset may be used (missing, foreign and deleting look identical)."""
        require(principal, Perm.DATA_MANAGE_OWN)
        row = self.repo.asset(principal.user_id, asset_id) if isinstance(asset_id, str) and 0 < len(asset_id) <= 64 else None
        if row is None:
            raise not_found("file")
        self.projects.get_owned(principal, row["project_id"], writable=writable)  # also blocks writes to archived projects
        return row

    def list(self, principal: Principal, project_id: str, *, kind: Optional[str] = None, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        project = self.projects.get_owned(principal, project_id)
        if kind not in (None, "dataset", "document"):
            raise invalid("kind", "Use 'dataset' or 'document'.")
        rows, total = self.repo.assets_for_project(principal.user_id, project["id"], kind=kind, limit=max(1, min(int(limit), 200)), offset=max(0, int(offset)))
        return {"items": [present_asset(r) for r in rows], "total": total}

    def get(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        row = self.get_owned(principal, asset_id)
        versions = self.repo.versions(row["id"])
        data = present_asset(row, len(versions))
        data["version_list"] = [
            {"id": v["id"], "number": v["version_number"], "kind": v["kind"], "parent_version_id": v["parent_version_id"],
             "transformation_id": v["transformation_id"], "checksum_sha256": v["checksum_sha256"], "size_bytes": v["size_bytes"],
             "rows": v["row_count"], "columns": v["column_count"], "created_at": v["created_at"],
             "current": v["id"] == row["current_version_id"]} for v in versions]
        return data

    def delete(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        row = self.get_owned(principal, asset_id, writable=True)
        return self.governance.delete_asset(principal, row)

    def open_original(self, principal: Principal, asset_id: str) -> Tuple[str, int, Iterator[bytes]]:
        row = self.get_owned(principal, asset_id)
        if row["status"] == AssetStatus.DELETING:
            raise not_found("file")
        version = self.repo.versions(row["id"])[0]  # version 1 is always the untouched original
        try:
            size = self.storage.size(version["storage_key"])
            handle = self.storage.open(version["storage_key"])
        except StorageError as error:
            raise not_found("file") from error
        self.ctx.audit.record(WEvent.ASSET_DOWNLOADED, actor=principal.user_id, target=principal.user_id, metadata={"asset_id": row["id"]})

        def stream() -> Iterator[bytes]:
            with handle:
                while True:
                    block = handle.read(BLOCK)
                    if not block:
                        return
                    yield block

        return row["name"], size, stream()

    # ------------------------------------------------------------------ governance views
    def set_retention(self, principal: Principal, asset_id: str, retain_until: Optional[str]) -> Dict[str, Any]:
        """Set (or clear, with None) the date after which a file is deleted automatically."""
        row = self.get_owned(principal, asset_id, writable=True)
        value = None
        if retain_until not in (None, ""):
            value = self._parse_retention(retain_until)
        now = self.ctx.clock.iso()
        self.repo.update_asset(row["id"], retain_until=value, retention_class="expiring" if value else "source", updated_at=now)
        self.repo.add_event(project_id=row["project_id"], actor=principal.user_id, event_type=WEvent.ASSET_RETENTION_SET,
                            subject_id=row["id"], detail=None, now=now)
        self.ctx.audit.record(WEvent.ASSET_RETENTION_SET, actor=principal.user_id, target=principal.user_id,
                              metadata={"asset_id": row["id"], "cleared": value is None})
        return {"asset_id": row["id"], "retain_until": value}

    def _parse_retention(self, raw: Any) -> str:
        if not isinstance(raw, str) or len(raw) > 40:
            raise invalid("retain_until", "Use a date such as 2027-06-30.")
        text = raw.strip()
        try:
            if len(text) == 10:
                moment = datetime.combine(date.fromisoformat(text), datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
            else:
                moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
        except ValueError:
            raise invalid("retain_until", "Use a date such as 2027-06-30.") from None
        now = self.ctx.clock.now()
        if moment <= now:
            raise invalid("retain_until", "Choose a date in the future.", "too_early")
        if moment > now + timedelta(days=MAX_RETENTION_DAYS):
            raise invalid("retain_until", "Choose a date within the next ten years.", "too_late")
        return to_iso(moment)

    def footprint(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        """Everything that deleting this file would remove, so the person can see it before confirming."""
        row = self.get_owned(principal, asset_id)
        prefix = keys.asset_prefix(row["project_id"], row["id"])
        groups = {"original": [0, 0], "table": [0, 0], "extracted_text": [0, 0], "profiles": [0, 0], "previews": [0, 0], "analyses": [0, 0], "models": [0, 0]}
        try:
            listing = self.storage.list_prefix(prefix)
        except StorageError:
            listing = []
        for key in listing:
            part = key[len(prefix) + 1:].split("/")
            kind = {"v": "table" if part[-1] == "table.csv" else "original", "x": "extracted_text", "f": "profiles", "pv": "previews", "n": "analyses", "m": "models"}.get(part[0])
            if kind is None:
                continue
            try:
                size = self.storage.size(key)
            except StorageError:
                size = 0
            groups[kind][0] += 1
            groups[kind][1] += size
        versions = self.repo.versions(row["id"])
        return {"asset_id": row["id"], "versions": len(versions), "derived_versions": sum(1 for v in versions if v["kind"] == "derived"),
                "objects": sum(g[0] for g in groups.values()), "bytes": sum(g[1] for g in groups.values()),
                "items": [{"kind": k, "objects": v[0], "bytes": v[1]} for k, v in groups.items() if v[0]],
                "retain_until": row["retain_until"]}

    def inventory(self, principal: Principal, horizon_days: int = 30) -> Dict[str, Any]:
        require(principal, Perm.DATA_MANAGE_OWN)
        until = self.ctx.clock.iso(timedelta(days=max(1, min(int(horizon_days), MAX_RETENTION_DAYS))))
        data = self.repo.inventory(principal.user_id)
        data["upcoming_deletions"] = [{"asset_id": r["id"], "project_id": r["project_id"], "name": r["name"], "retain_until": r["retain_until"]}
                                      for r in self.repo.upcoming_expiries(principal.user_id, until, 50)]
        data["stored_bytes"] = data["original_bytes"] + data["derived_bytes"]
        return data
