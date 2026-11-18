"""Project storage buckets: `data` (the existing Data Workspace, reused, read-only from here), `outputs` / `derived` / `temporary` (job results). Content is stored once by checksum, every read re-verifies
the checksum, versions are append-only, originals are immutable, and an artifact that backs an approved experiment cannot be deleted."""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from .common import DevService, dev_error, get_object_at, invalid, kind_of, not_found, sha256_bytes, record_event
from .compute import effective_limit

BUCKETS = ("outputs", "derived", "temporary")
TEXT_KINDS = {"code", "text", "config", "manifest", "notebook"}


def present(r) -> Dict[str, Any]:
    import json
    return {"id": r["id"], "name": r["name"], "bucket": r["bucket"], "version": r["version"], "kind": r["kind"], "sha256": r["sha256"], "bytes": r["bytes"], "job_id": r["job_id"], "experiment_id": r["experiment_id"],
            "provenance": json.loads(r["provenance"]), "retention_until": r["retention_until"], "created_at": r["created_at"]}


def purge_expired(conn, clock) -> int:
    now = clock.iso()
    n = conn.execute("UPDATE dv_artifacts SET deleted_at = ? WHERE deleted_at IS NULL AND retention_until IS NOT NULL AND retention_until < ?", (now, now)).rowcount
    return n


class ArtifactService(DevService):
    def summary(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        out = {}
        for b in BUCKETS:
            r = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(bytes),0) s FROM dv_artifacts WHERE project_id = ? AND bucket = ? AND deleted_at IS NULL", (project["id"], b)).fetchone()
            out[b] = {"items": r["c"], "bytes": r["s"]}
        a = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) s FROM assets WHERE project_id = ? AND owner_user_id = ? AND status = 'ready'", (project["id"], project["owner_user_id"])).fetchone()
        out["data"] = {"items": a["c"], "bytes": a["s"], "note": "Managed in the Data Workspace. Originals are never modified; jobs see read-only copies."}
        files = self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(bytes),0) s FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (project["id"],)).fetchone()
        cap = effective_limit(self.conn, self.ctx.settings, project["owner_user_id"], project["id"], "project_storage_mb") * 1024 * 1024
        used = files["s"] + sum(out[b]["bytes"] for b in BUCKETS)
        return {"buckets": out, "code": {"items": files["c"], "bytes": files["s"]}, "used_bytes": used, "limit_bytes": cap}

    def list(self, principal, project_id: Any, bucket: Any = None, name: Any = None, limit: int = 100) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        sql, args = "SELECT * FROM dv_artifacts WHERE project_id = ? AND deleted_at IS NULL", [project["id"]]
        if bucket is not None:
            if bucket not in BUCKETS:
                raise invalid("bucket", "Choose outputs, derived or temporary.")
            sql, args = sql + " AND bucket = ?", args + [bucket]
        if isinstance(name, str):
            sql, args = sql + " AND name = ?", args + [name]
        return {"items": [present(r) for r in self.conn.execute(sql + " ORDER BY created_at DESC, name LIMIT ?", args + [max(1, min(int(limit), 500))]).fetchall()]}

    def _row(self, project_id: str, aid: Any):
        r = self.conn.execute("SELECT * FROM dv_artifacts WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (aid if isinstance(aid, str) else "", project_id)).fetchone()
        if r is None:
            raise not_found("artifact")
        return r

    def read(self, principal, project_id: Any, aid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        r = self._row(project["id"], aid)
        data = get_object_at(self.ctx.settings, project["id"], r["sha256"])
        if data is None or sha256_bytes(data) != r["sha256"]:
            self.event(project["id"], principal, "artifact.integrity_failed", "artifact", r["id"], "failure")
            raise dev_error("ARTIFACT_CORRUPT", "The stored file no longer matches its checksum, so it was not returned.", 500)
        d = present(r)
        if r["kind"] in TEXT_KINDS or r["name"].endswith((".json", ".csv", ".txt", ".md", ".log")):
            d.update({"encoding": "utf-8", "content": data.decode("utf-8", "replace")})
        else:
            d.update({"encoding": "base64", "content": base64.b64encode(data).decode()})
        return d

    def delete(self, principal, project_id: Any, aid: Any, confirm: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        r = self._row(project["id"], aid)
        if confirm != r["name"]:
            raise invalid("confirm", "Type the artifact's name to confirm.")
        if r["experiment_id"] and self.conn.execute("SELECT 1 FROM dv_experiments WHERE id = ? AND approval_state = 'approved'", (r["experiment_id"],)).fetchone():
            raise dev_error("ARTIFACT_PROTECTED", "This artifact backs an approved experiment. Revoke the approval first.", 409)
        self.conn.execute("UPDATE dv_artifacts SET deleted_at = ? WHERE id = ?", (self.now(), r["id"]))
        self.event(project["id"], principal, "artifact.deleted", "artifact", r["id"], name=r["name"], version=r["version"])
        return {"deleted": True}

    def set_retention(self, principal, project_id: Any, aid: Any, days: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        r = self._row(project["id"], aid)
        if days is None:
            until = None
        elif isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise invalid("days", "Use 1 to 3650 days, or null to keep it until you delete it.")
        else:
            from datetime import timedelta
            until = self.clock.iso(timedelta(days=days))
        self.conn.execute("UPDATE dv_artifacts SET retention_until = ? WHERE id = ?", (until, r["id"]))
        self.event(project["id"], principal, "artifact.retention_set", "artifact", r["id"], days=days)
        return present(self._row(project["id"], aid))
