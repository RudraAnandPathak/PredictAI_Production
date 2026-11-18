"""Data governance: deletion workflows with completion evidence, storage clean-up and retention.

A deletion is a small state machine so a crash can never leave half-deleted data unnoticed:
  1. record a 'pending' deletion and hide the resource ('deleting' status: invisible to its owner),
  2. remove every stored object (originals, derived versions, extracted text, profiles, temporary chunks),
  3. remove the database rows and mark the deletion 'completed' with counts.
If step 2 fails the record stays 'pending'/'failed' and the retention job retries it. Records hold ids and
counts only, never names or contents."""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from ...db import transaction
from ..identity.authorization import Principal
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from .safelog import get_logger
from . import keys, phase7_purge, research_purge
from .constants import AssetStatus, ORPHAN_MIN_AGE_SECONDS, ProjectStatus, STALE_PROCESSING_MINUTES, WEvent
from .repository import WorkspaceRepository

logger = get_logger()


class Governance:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend) -> None:
        self.ctx, self.repo, self.storage = ctx, repo, storage

    # ---------------------------------------------------------------- assets
    def delete_asset(self, principal: Principal, asset) -> Dict[str, Any]:
        ctx = self.ctx
        deletion_id, now = new_id(), ctx.clock.iso()
        with transaction(ctx.conn):
            self.repo.create_deletion(id=deletion_id, scope="asset", resource_id=asset["id"], owner=asset["owner_user_id"], requested_by=principal.user_id, now=now)
            self.repo.update_asset(asset["id"], status=AssetStatus.DELETING, updated_at=now)
        return self._finish_asset(deletion_id, asset["id"], asset["project_id"], asset["owner_user_id"], principal.user_id)

    def _finish_asset(self, deletion_id: str, asset_id: str, project_id: str, owner: str, actor: str) -> Dict[str, Any]:
        ctx = self.ctx
        try:
            for bundle_key in [*research_purge.asset_bundle_keys(ctx.conn, asset_id), *research_purge.asset_influence_keys(ctx.conn, asset_id)]:   # bundles that quote this file and influence results computed from it go with it
                self.storage.delete(bundle_key)
            objects = self.storage.delete_prefix(keys.asset_prefix(project_id, asset_id))
        except (StorageError, OSError):
            logger.warning("Storage purge failed for an asset", exc_info=True)
            return self._fail(deletion_id, "asset", asset_id, owner, actor)
        with transaction(ctx.conn):
            counts = self.repo.purge_asset_rows(asset_id, ctx.clock.iso())
            counts["objects"] = objects
            self.repo.finish_deletion(deletion_id, status="completed", counts=json.dumps(counts), error_code=None, now=ctx.clock.iso())
            if self.repo.project_any_state(owner, project_id) is not None:
                self.repo.add_event(project_id=project_id, actor=actor, event_type=WEvent.ASSET_DELETED, subject_id=asset_id, detail=None, now=ctx.clock.iso())
            ctx.audit.record(WEvent.ASSET_DELETED, actor=actor, target=owner, metadata={"asset_id": asset_id, "objects": objects})
        return {"deleted": True, "deletion_id": deletion_id, "counts": counts}

    # ---------------------------------------------------------------- projects
    def delete_project(self, principal: Principal, project) -> Dict[str, Any]:
        ctx = self.ctx
        deletion_id, now = new_id(), ctx.clock.iso()
        with transaction(ctx.conn):
            self.repo.create_deletion(id=deletion_id, scope="project", resource_id=project["id"], owner=project["owner_user_id"], requested_by=principal.user_id, now=now)
            self.repo.update_project(project["id"], status=ProjectStatus.DELETING, updated_at=now)
        return self._finish_project(deletion_id, project["id"], project["owner_user_id"], principal.user_id)

    def _finish_project(self, deletion_id: str, project_id: str, owner: str, actor: str) -> Dict[str, Any]:
        ctx = self.ctx
        try:
            for upload in self.repo.uploads_of_project(project_id):
                self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
            objects = self.storage.delete_prefix(keys.project_prefix(project_id))
            for key in phase7_purge.project_object_keys(ctx.conn, project_id):          # Phase 7 objects live outside the project prefix
                self.storage.delete(key)
            objects += self.storage.delete_prefix(phase7_purge.registry_prefix(project_id)) + self.storage.delete_prefix(phase7_purge.reports_prefix(project_id))
            self._stop_dev_processes(project_id)
            objects += phase7_purge.dev_files_removed(ctx.settings, project_id)       # Phase 9 working tree, content store, environments, job folders
        except (StorageError, OSError):
            logger.warning("Storage purge failed for a project", exc_info=True)
            return self._fail(deletion_id, "project", project_id, owner, actor)
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            counts = self.repo.purge_project_rows(project_id, now)
            counts["objects"] = objects
            # The row stays as an anonymous tombstone (no name, description or metadata) so references and audit stay consistent.
            self.repo.update_project(project_id, name=None, description=None, metadata=None, status=ProjectStatus.DELETED, deleted_at=now, updated_at=now)
            self.repo.finish_deletion(deletion_id, status="completed", counts=json.dumps(counts), error_code=None, now=now)
            ctx.audit.record(WEvent.PROJECT_DELETED, actor=actor, target=owner, metadata={"project_id": project_id, "objects": objects})
        return {"deleted": True, "deletion_id": deletion_id, "counts": counts}

    def _stop_dev_processes(self, project_id: str) -> None:
        """Before the folder goes: end the project's notebook/IDE servers and flag its jobs so a running worker kills them (the worker also notices the missing rows)."""
        conn = self.ctx.conn
        try:
            import os
            import signal
            for r in conn.execute("SELECT pid FROM dv_sessions WHERE project_id = ? AND status IN ('starting','running') AND pid IS NOT NULL", (project_id,)).fetchall():
                try:
                    os.killpg(r["pid"], signal.SIGKILL)
                except OSError:
                    pass
            for r in conn.execute("SELECT worker_pid FROM dv_jobs WHERE project_id = ? AND status IN ('starting','running','cancelling') AND worker_pid IS NOT NULL", (project_id,)).fetchall():
                try:
                    os.killpg(r["worker_pid"], signal.SIGKILL)           # the job runs in its own session/process group on this host
                except OSError:
                    pass
            conn.execute("UPDATE dv_jobs SET cancel_requested = 1 WHERE project_id = ? AND status IN ('queued','starting','running')", (project_id,))
        except Exception:  # noqa: BLE001 - a database without the Phase 9 tables has nothing to stop
            logger.debug("No development workspace processes to stop", exc_info=True)

    def _fail(self, deletion_id: str, scope: str, resource_id: str, owner: str, actor: str) -> Dict[str, Any]:
        ctx = self.ctx
        self.repo.finish_deletion(deletion_id, status="failed", counts=None, error_code="STORAGE_ERROR", now=ctx.clock.iso())
        ctx.audit.record(WEvent.PURGE_FAILED, "failure", actor=actor, target=owner, reason="storage_error", metadata={"scope": scope, "resource_id": resource_id})
        return {"deleted": False, "deletion_id": deletion_id, "pending": True,
                "message": "The data is hidden and will finish deleting automatically. You do not need to do anything."}

    # ---------------------------------------------------------------- whole account
    def delete_all_for_user(self, user_id: str, requested_by: str) -> Dict[str, int]:
        """Used when an account is deleted: every project, asset, upload and generated object of that user goes."""
        ctx = self.ctx
        totals = {"projects": 0}
        for project in self.repo.projects_of_owner_all_states(user_id):
            deletion_id, now = new_id(), ctx.clock.iso()
            with transaction(ctx.conn):
                self.repo.create_deletion(id=deletion_id, scope="account", resource_id=project["id"], owner=user_id, requested_by=requested_by, now=now)
                self.repo.update_project(project["id"], status=ProjectStatus.DELETING, updated_at=now)
            result = self._finish_project(deletion_id, project["id"], user_id, requested_by)
            if not result.get("deleted"):
                raise StorageError("account data could not be fully removed yet")
            totals["projects"] += 1
        with transaction(ctx.conn):
            from ..reviews.service import anonymize_for_user                  # reviews stay, without the person (runs while the user row still has the name to blank out)
            totals["reviews_anonymized"] = anonymize_for_user(ctx.conn, user_id, ctx.clock.iso())
            totals.update({f"person_{k}": v for k, v in research_purge.user_rows(ctx.conn, user_id).items()})
            from ..research_intelligence.core.purge import purge_user         # Phase 10: sources, passages, vectors, stored files, usage (before the rw_* rows they depend on)
            totals.update({f"person_ri_{k}": v for k, v in purge_user(ctx.conn, ctx.settings, ctx.clock.iso, user_id).items()})
            totals.update({f"person_{k}": v for k, v in phase7_purge.user_rows(ctx.conn, user_id, ctx.clock.iso()).items()})
        return totals

    # ---------------------------------------------------------------- housekeeping / retention
    def run_retention(self, limit: int = 200) -> Dict[str, int]:
        ctx = self.ctx
        now = ctx.clock.iso()
        done = {"uploads": 0, "previews": 0, "expired_assets": 0, "retried_deletions": 0, "interrupted": 0, "orphans": 0}
        for upload in self.repo.stale_uploads(now, limit):
            try:
                self.storage.delete_prefix(keys.upload_prefix(upload["id"]))
            except (StorageError, OSError):
                continue
            self.repo.delete_upload(upload["id"])
            done["uploads"] += 1
        done["interrupted"] = self.mark_interrupted(limit)
        for stale in self.repo.expired_previews(now):
            try:
                self.storage.delete(keys.preview_key(stale["project_id"], stale["asset_id"], stale["id"]))
            except (StorageError, OSError):
                logger.warning("Could not remove an expired preview file", exc_info=True)
        done["previews"] = self.repo.expire_previews(now)
        for asset in self.repo.expired_assets(now, limit):
            actor = asset["owner_user_id"]
            deletion_id = new_id()
            with transaction(ctx.conn):
                self.repo.create_deletion(id=deletion_id, scope="retention", resource_id=asset["id"], owner=actor, requested_by="system", now=now)
                self.repo.update_asset(asset["id"], status=AssetStatus.DELETING, updated_at=now)
            if self._finish_asset(deletion_id, asset["id"], asset["project_id"], actor, "system").get("deleted"):
                done["expired_assets"] += 1
        for record in self.repo.pending_deletions(limit):
            if self._retry(record):
                done["retried_deletions"] += 1
        done["orphans"] = self.sweep_orphans()
        if any(done.values()):
            ctx.audit.record(WEvent.RETENTION_PURGED, actor="system", metadata=dict(done))
        return done

    def mark_interrupted(self, limit: int = 200) -> int:
        """Assets stuck in 'processing' (the server stopped mid-way) become 'failed' with a clear, retryable reason."""
        cutoff = self.ctx.clock.iso(-timedelta(minutes=STALE_PROCESSING_MINUTES))
        count = 0
        for row in self.repo.stale_processing(cutoff, limit):
            self.repo.update_asset(row["id"], status=AssetStatus.FAILED, status_code="PROCESSING_INTERRUPTED",
                                   status_detail="Processing was interrupted before it finished. The original file was kept; delete it and upload again.",
                                   updated_at=self.ctx.clock.iso())
            self.ctx.audit.record(WEvent.ASSET_FAILED, "failure", actor="system", target=row["owner_user_id"],
                                  reason="processing_interrupted", metadata={"asset_id": row["id"]})
            count += 1
        return count

    def sweep_orphans(self, min_age_seconds: int = ORPHAN_MIN_AGE_SECONDS) -> int:
        """Remove stored objects that no database row refers to (left by a crash between 'write' and 'record').
        Only objects older than `min_age_seconds` are touched, so an upload in flight is never harmed."""
        removed, cutoff = 0, time.time() - min_age_seconds
        try:
            listing = self.storage.list_prefix("p") + self.storage.list_prefix("t")
        except (StorageError, OSError):
            return 0
        for key in listing:
            parts = key.split("/")
            try:
                if parts[0] == "p" and len(parts) >= 5 and parts[2] == "a":
                    if self.repo.asset_any_state(parts[3]) is not None:
                        continue
                elif parts[0] == "t" and len(parts) >= 2:
                    if self.repo.upload_exists(parts[1]):
                        continue
                else:
                    continue
                if self.storage.modified_at(key) > cutoff:
                    continue
                if self.storage.delete(key):
                    removed += 1
            except (StorageError, OSError):
                logger.warning("Could not remove an orphaned object", exc_info=True)
        return removed

    def _retry(self, record) -> bool:
        owner, rid = record["owner_user_id"], record["resource_id"]
        if record["scope"] in ("asset", "retention"):
            row = self.ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (rid,)).fetchone()
            if row is None:  # rows already gone: only the evidence record was left open
                self.repo.finish_deletion(record["id"], status="completed", counts=None, error_code=None, now=self.ctx.clock.iso())
                return True
            return bool(self._finish_asset(record["id"], rid, row["project_id"], owner, record["requested_by"]).get("deleted"))
        row = self.repo.project_any_state(owner, rid)
        if row is None or row["status"] == ProjectStatus.DELETED:
            self.repo.finish_deletion(record["id"], status="completed", counts=None, error_code=None, now=self.ctx.clock.iso())
            return True
        return bool(self._finish_project(record["id"], rid, owner, record["requested_by"]).get("deleted"))

    def list_deletions(self, principal: Principal, limit: int = 50) -> Dict[str, Any]:
        rows = self.repo.deletion_records(principal.user_id, max(1, min(limit, 200)))
        return {"items": [{"id": r["id"], "scope": r["scope"], "status": r["status"], "requested_at": r["requested_at"],
                           "completed_at": r["completed_at"], "counts": json.loads(r["counts"]) if r["counts"] else None} for r in rows]}
