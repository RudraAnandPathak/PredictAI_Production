"""All SQL for the workspace module. Every read of user data takes the owner id, so a row that belongs to
someone else can never be returned: isolation is enforced here as well as in the services."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import phase7_purge, research_purge

Row = sqlite3.Row

_PROJECT_FIELDS = {"name", "description", "status", "metadata", "updated_at", "archived_at", "deleted_at", "retention_class"}
_ASSET_FIELDS = {"status", "status_code", "status_detail", "current_version_id", "page_count", "page_count_estimated",
                 "row_count", "column_count", "retain_until", "retention_class", "updated_at", "name", "detected_format"}
_UPLOAD_FIELDS = {"status", "error_code", "asset_id", "received_bytes", "updated_at"}
_TRANSFORM_FIELDS = {"status", "result_version_id", "summary", "applied_by", "applied_at", "preview"}


def _set(table: str, allowed: set, row_id: str, fields: Dict[str, Any], conn: sqlite3.Connection, extra: str = "") -> None:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown {table} fields: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?{extra}", (*fields.values(), row_id))


class WorkspaceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Row]:
        return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: Sequence[Any] = ()) -> List[Row]:
        return self.conn.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- projects
    def create_project(self, *, id: str, owner: str, name: str, description: Optional[str], metadata: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO projects (id, owner_user_id, name, description, status, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)", (id, owner, name, description, metadata, now, now))

    def project(self, owner: str, project_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM projects WHERE id = ? AND owner_user_id = ? AND status IN ('active', 'archived')", (project_id, owner))

    def project_any_state(self, owner: str, project_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM projects WHERE id = ? AND owner_user_id = ?", (project_id, owner))

    def count_projects(self, owner: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM projects WHERE owner_user_id = ? AND status IN ('active', 'archived')", (owner,))["n"]

    def list_projects(self, owner: str, *, status: Optional[str], query: Optional[str], limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = ["owner_user_id = ?", "status IN ('active', 'archived')"], [owner]  # type: List[str], List[Any]
        if status:
            where.append("status = ?")
            params.append(status)
        if query:
            escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(LOWER(name) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(description, '')) LIKE ? ESCAPE '\\')")
            params += [f"%{escaped}%", f"%{escaped}%"]
        clause = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) AS n FROM projects WHERE {clause}", params)["n"]
        rows = self._all(f"SELECT * FROM projects WHERE {clause} ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", (*params, limit, offset))
        return rows, total

    def update_project(self, project_id: str, **fields: Any) -> None:
        _set("projects", _PROJECT_FIELDS, project_id, fields, self.conn)

    def project_asset_counts(self, project_id: str) -> Dict[str, int]:
        rows = self._all("SELECT kind, COUNT(*) AS n FROM assets WHERE project_id = ? AND status IN ('processing', 'ready', 'failed') GROUP BY kind", (project_id,))
        counts = {"dataset": 0, "document": 0}
        counts.update({r["kind"]: r["n"] for r in rows})
        return counts

    def projects_in_state(self, status: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM projects WHERE status = ? ORDER BY updated_at LIMIT ?", (status, limit))

    def projects_of_owner_all_states(self, owner: str) -> List[Row]:
        return self._all("SELECT * FROM projects WHERE owner_user_id = ? AND status != 'deleted'", (owner,))

    # ---------------------------------------------------------------- activity
    def add_event(self, *, project_id: str, actor: str, event_type: str, subject_id: Optional[str], detail: Optional[str], now: str) -> None:
        self.conn.execute("INSERT INTO project_events (project_id, actor_user_id, event_type, subject_id, detail, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (project_id, actor, event_type, subject_id, detail, now))

    def events(self, project_id: str, *, before_id: Optional[int], limit: int) -> List[Row]:
        if before_id is not None:
            return self._all("SELECT * FROM project_events WHERE project_id = ? AND id < ? ORDER BY id DESC LIMIT ?", (project_id, before_id, limit))
        return self._all("SELECT * FROM project_events WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit))

    # ---------------------------------------------------------------- assets
    def create_asset(self, *, id: str, project_id: str, owner: str, kind: str, name: str, detected_format: str, status: str,
                     checksum: str, size: int, now: str) -> None:
        self.conn.execute(
            "INSERT INTO assets (id, project_id, owner_user_id, kind, name, detected_format, status, source_checksum, size_bytes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (id, project_id, owner, kind, name, detected_format, status, checksum, size, now, now))

    def asset(self, owner: str, asset_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM assets WHERE id = ? AND owner_user_id = ? AND status != 'deleting'", (asset_id, owner))

    def asset_in_project(self, owner: str, project_id: str, asset_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM assets WHERE id = ? AND project_id = ? AND owner_user_id = ? AND status != 'deleting'", (asset_id, project_id, owner))

    def assets_for_project(self, owner: str, project_id: str, *, kind: Optional[str], limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = "project_id = ? AND owner_user_id = ? AND status != 'deleting'", [project_id, owner]  # type: str, List[Any]
        if kind:
            where += " AND kind = ?"
            params.append(kind)
        total = self._one(f"SELECT COUNT(*) AS n FROM assets WHERE {where}", params)["n"]
        rows = self._all(f"SELECT * FROM assets WHERE {where} ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (*params, limit, offset))
        return rows, total

    def all_assets_in_project(self, project_id: str) -> List[Row]:
        return self._all("SELECT * FROM assets WHERE project_id = ?", (project_id,))

    def asset_by_checksum(self, project_id: str, checksum: str) -> Optional[Row]:
        return self._one("SELECT * FROM assets WHERE project_id = ? AND source_checksum = ? AND status IN ('processing', 'ready')", (project_id, checksum))

    def count_documents(self, owner: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM assets WHERE owner_user_id = ? AND kind = 'document' AND status IN ('processing', 'ready')", (owner,))["n"]

    def count_assets(self, project_id: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM assets WHERE project_id = ? AND status IN ('processing', 'ready', 'failed')", (project_id,))["n"]

    def update_asset(self, asset_id: str, **fields: Any) -> None:
        _set("assets", _ASSET_FIELDS, asset_id, fields, self.conn)

    def stale_processing(self, before: str, limit: int) -> List[Row]:
        return self._all("SELECT id, owner_user_id FROM assets WHERE status = 'processing' AND updated_at <= ? LIMIT ?", (before, limit))

    def upload_exists(self, upload_id: str) -> bool:
        return self._one("SELECT 1 AS x FROM uploads WHERE id = ?", (upload_id,)) is not None

    def asset_any_state(self, asset_id: str) -> Optional[Row]:
        return self._one("SELECT id, project_id, status FROM assets WHERE id = ?", (asset_id,))

    def inventory(self, owner: str) -> Dict[str, Any]:
        row = self._one(
            "SELECT COUNT(*) AS assets, COALESCE(SUM(CASE WHEN kind = 'dataset' THEN 1 ELSE 0 END), 0) AS datasets, "
            "COALESCE(SUM(CASE WHEN kind = 'document' THEN 1 ELSE 0 END), 0) AS documents, "
            "COALESCE(SUM(CASE WHEN retain_until IS NOT NULL THEN 1 ELSE 0 END), 0) AS with_expiry "
            "FROM assets WHERE owner_user_id = ? AND status IN ('processing', 'ready', 'failed')", (owner,))
        versions = self._one(
            "SELECT COUNT(*) AS versions, COALESCE(SUM(CASE WHEN kind = 'original' THEN size_bytes ELSE 0 END), 0) AS original_bytes, "
            "COALESCE(SUM(CASE WHEN kind = 'derived' THEN size_bytes ELSE 0 END), 0) AS derived_bytes, "
            "COALESCE(SUM(CASE WHEN kind = 'derived' THEN 1 ELSE 0 END), 0) AS derived_versions "
            "FROM asset_versions WHERE owner_user_id = ? AND asset_id IN (SELECT id FROM assets WHERE status != 'deleting')", (owner,))
        projects = self._one("SELECT COUNT(*) AS n FROM projects WHERE owner_user_id = ? AND status IN ('active', 'archived')", (owner,))["n"]
        return {"projects": projects, **{k: row[k] for k in row.keys()}, **{k: versions[k] for k in versions.keys()}}

    def upcoming_expiries(self, owner: str, until: str, limit: int) -> List[Row]:
        return self._all("SELECT id, project_id, name, retain_until FROM assets WHERE owner_user_id = ? AND retain_until IS NOT NULL AND retain_until <= ? "
                         "AND status IN ('ready', 'failed') ORDER BY retain_until LIMIT ?", (owner, until, limit))

    def expired_assets(self, now: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM assets WHERE retain_until IS NOT NULL AND retain_until <= ? AND status IN ('ready', 'failed') LIMIT ?", (now, limit))

    def derived_asset_ids(self, asset_id: str) -> List[str]:
        return [r["id"] for r in self._all("SELECT id FROM asset_versions WHERE asset_id = ?", (asset_id,))]

    # ---------------------------------------------------------------- versions
    def create_version(self, *, id: str, asset_id: str, project_id: str, owner: str, number: int, kind: str, parent: Optional[str],
                       transformation_id: Optional[str], storage_key: str, canonical_key: Optional[str], checksum: str, size: int,
                       fmt: str, rows: Optional[int], cols: Optional[int], created_by: str, now: str) -> None:
        self.conn.execute(
            "INSERT INTO asset_versions (id, asset_id, project_id, owner_user_id, version_number, kind, parent_version_id, transformation_id, "
            "storage_key, canonical_key, checksum_sha256, size_bytes, format, row_count, column_count, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, asset_id, project_id, owner, number, kind, parent, transformation_id, storage_key, canonical_key, checksum, size, fmt, rows, cols, created_by, now))

    def version(self, owner: str, version_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM asset_versions WHERE id = ? AND owner_user_id = ?", (version_id, owner))

    def versions(self, asset_id: str) -> List[Row]:
        return self._all("SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_number", (asset_id,))

    def next_version_number(self, asset_id: str) -> int:
        return (self._one("SELECT COALESCE(MAX(version_number), 0) + 1 AS n FROM asset_versions WHERE asset_id = ?", (asset_id,))["n"])

    def update_version_canonical(self, version_id: str, canonical_key: str, rows: Optional[int], cols: Optional[int]) -> None:
        self.conn.execute("UPDATE asset_versions SET canonical_key = ?, row_count = ?, column_count = ? WHERE id = ?", (canonical_key, rows, cols, version_id))

    # ---------------------------------------------------------------- uploads
    def create_upload(self, *, id: str, project_id: str, owner: str, filename: str, size: int, checksum: Optional[str], chunk_size: int,
                      total_chunks: int, now: str, expires_at: str) -> None:
        self.conn.execute(
            "INSERT INTO uploads (id, project_id, owner_user_id, filename, declared_size, declared_checksum, chunk_size, total_chunks, status, "
            "created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (id, project_id, owner, filename, size, checksum, chunk_size, total_chunks, now, now, expires_at))

    def upload(self, owner: str, upload_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM uploads WHERE id = ? AND owner_user_id = ?", (upload_id, owner))

    def uploads_for_project(self, owner: str, project_id: str) -> List[Row]:
        return self._all("SELECT * FROM uploads WHERE project_id = ? AND owner_user_id = ? AND status = 'open' ORDER BY created_at DESC", (project_id, owner))

    def open_upload_match(self, owner: str, project_id: str, filename: str, size: int, checksum: Optional[str]) -> Optional[Row]:
        return self._one("SELECT * FROM uploads WHERE owner_user_id = ? AND project_id = ? AND filename = ? AND declared_size = ? "
                         "AND COALESCE(declared_checksum, '') = ? AND status = 'open'", (owner, project_id, filename, size, checksum or ""))

    def count_open_uploads(self, owner: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM uploads WHERE owner_user_id = ? AND status IN ('open', 'assembling')", (owner,))["n"]

    def update_upload(self, upload_id: str, **fields: Any) -> None:
        _set("uploads", _UPLOAD_FIELDS, upload_id, fields, self.conn)

    def claim_upload(self, upload_id: str, now: str) -> bool:
        """Only one request may assemble an upload; a second concurrent 'complete' loses and re-reads the result."""
        cur = self.conn.execute("UPDATE uploads SET status = 'assembling', updated_at = ? WHERE id = ? AND status = 'open'", (now, upload_id))
        return cur.rowcount == 1

    def put_chunk(self, upload_id: str, index: int, size: int, sha256: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO upload_chunks (upload_id, chunk_index, size_bytes, sha256) VALUES (?, ?, ?, ?)", (upload_id, index, size, sha256))

    def chunks(self, upload_id: str) -> List[Row]:
        return self._all("SELECT * FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index", (upload_id,))

    def delete_chunks(self, upload_id: str) -> None:
        self.conn.execute("DELETE FROM upload_chunks WHERE upload_id = ?", (upload_id,))

    def stale_uploads(self, now: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM uploads WHERE status IN ('open', 'assembling', 'failed', 'cancelled') AND expires_at <= ? LIMIT ?", (now, limit))

    def delete_upload(self, upload_id: str) -> None:
        self.conn.execute("DELETE FROM upload_chunks WHERE upload_id = ?", (upload_id,))
        self.conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))

    def uploads_of_project(self, project_id: str) -> List[Row]:
        return self._all("SELECT id FROM uploads WHERE project_id = ?", (project_id,))

    # ---------------------------------------------------------------- extraction / profile
    def create_extraction(self, *, id: str, asset_id: str, version_id: str, project_id: str, owner: str, status: str, parser: str,
                          parser_version: str, summary: str, warnings: str, text_key: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO extractions (id, asset_id, version_id, project_id, owner_user_id, status, parser, parser_version, summary, warnings, text_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (id, asset_id, version_id, project_id, owner, status, parser, parser_version, summary, warnings, text_key, now))

    def extraction_for_version(self, owner: str, version_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM extractions WHERE version_id = ? AND owner_user_id = ? ORDER BY created_at DESC LIMIT 1", (version_id, owner))

    def extractions_for_asset(self, asset_id: str) -> List[Row]:
        return self._all("SELECT * FROM extractions WHERE asset_id = ?", (asset_id,))

    def create_profile(self, *, id: str, asset_id: str, version_id: str, project_id: str, owner: str, profiler_version: str, checksum: str,
                      result_key: str, summary: str, now: str) -> None:
        self.conn.execute(
            "INSERT INTO profiles (id, asset_id, version_id, project_id, owner_user_id, profiler_version, source_checksum, result_key, summary, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (id, asset_id, version_id, project_id, owner, profiler_version, checksum, result_key, summary, now))

    def latest_profile(self, owner: str, version_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM profiles WHERE version_id = ? AND owner_user_id = ? ORDER BY generated_at DESC, rowid DESC LIMIT 1", (version_id, owner))

    def profiles_for_asset(self, asset_id: str) -> List[Row]:
        return self._all("SELECT * FROM profiles WHERE asset_id = ?", (asset_id,))

    # ---------------------------------------------------------------- transformations
    def create_transformation(self, *, id: str, asset_id: str, project_id: str, owner: str, kind: str, status: str, source_version_id: str,
                              operations: str, explanation: Optional[str], preview: Optional[str], created_by: str, now: str,
                              expires_at: Optional[str], result_version_id: Optional[str] = None, summary: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO transformations (id, asset_id, project_id, owner_user_id, kind, status, source_version_id, result_version_id, operations, "
            "explanation, preview, summary, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, asset_id, project_id, owner, kind, status, source_version_id, result_version_id, operations, explanation, preview, summary, created_by, now, expires_at))

    def transformation(self, owner: str, transformation_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM transformations WHERE id = ? AND owner_user_id = ?", (transformation_id, owner))

    def transformations_for_asset(self, owner: str, asset_id: str) -> List[Row]:
        return self._all("SELECT * FROM transformations WHERE asset_id = ? AND owner_user_id = ? ORDER BY created_at, rowid", (asset_id, owner))

    def update_transformation(self, transformation_id: str, **fields: Any) -> None:
        _set("transformations", _TRANSFORM_FIELDS, transformation_id, fields, self.conn)

    def claim_transformation(self, transformation_id: str) -> bool:
        """A preview can be confirmed once: the first confirm flips it to 'applied' and the rest are refused."""
        cur = self.conn.execute("UPDATE transformations SET status = 'applied' WHERE id = ? AND status = 'previewed'", (transformation_id,))
        return cur.rowcount == 1

    def expired_previews(self, now: str) -> List[Row]:
        return self._all("SELECT id, asset_id, project_id FROM transformations WHERE status = 'previewed' AND expires_at <= ?", (now,))

    def expire_previews(self, now: str) -> int:
        return self.conn.execute("UPDATE transformations SET status = 'expired', preview = NULL WHERE status = 'previewed' AND expires_at <= ?", (now,)).rowcount

    # ---------------------------------------------------------------- deletion evidence
    def create_deletion(self, *, id: str, scope: str, resource_id: str, owner: str, requested_by: str, now: str) -> None:
        self.conn.execute("INSERT INTO deletion_records (id, scope, resource_id, owner_user_id, requested_by, status, requested_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                          (id, scope, resource_id, owner, requested_by, now))

    def finish_deletion(self, deletion_id: str, *, status: str, counts: Optional[str], error_code: Optional[str], now: str) -> None:
        self.conn.execute("UPDATE deletion_records SET status = ?, counts = ?, error_code = ?, completed_at = ? WHERE id = ?", (status, counts, error_code, now, deletion_id))

    def deletion_records(self, owner: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM deletion_records WHERE owner_user_id = ? ORDER BY requested_at DESC, id LIMIT ?", (owner, limit))

    def pending_deletions(self, limit: int) -> List[Row]:
        return self._all("SELECT * FROM deletion_records WHERE status IN ('pending', 'failed') ORDER BY requested_at LIMIT ?", (limit,))

    # ---------------------------------------------------------------- purge (rows only; storage is handled by governance)
    def purge_asset_rows(self, asset_id: str, now: str = "") -> Dict[str, int]:
        counts = dict(phase7_purge.asset_rows(self.conn, asset_id, now))                    # Phase 7/8: exam documents, runs, report passages, research sources and shares that depend on the file
        counts.update(research_purge.asset_rows(self.conn, asset_id))                       # Phase 6: bundles quoting the file, retrieval indexes and source records go first
        # Phase 5 modelling rows reference the asset and its versions, so they go first (predictions -> models -> jobs)
        for table in ("ml_predictions", "ml_models", "ml_jobs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,)).rowcount
        # Phase 4 analytics rows reference the asset and its versions, so they go first
        self.conn.execute("DELETE FROM conversation_messages WHERE conversation_id IN (SELECT id FROM conversations WHERE asset_id = ?)", (asset_id,))
        for table in ("conversations", "insights", "analysis_runs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,)).rowcount
        for table in ("extractions", "profiles", "transformations", "asset_versions"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,)).rowcount
        counts["assets"] = self.conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,)).rowcount
        return counts

    def purge_project_rows(self, project_id: str, now: str = "") -> Dict[str, int]:
        counts = dict(phase7_purge.project_rows(self.conn, project_id, now))                # Phase 7/8 rows reference the project and its assets, so they go first
        counts.update(research_purge.project_rows(self.conn, project_id))                   # Phase 6 rows reference the project and its assets
        self.conn.execute("DELETE FROM upload_chunks WHERE upload_id IN (SELECT id FROM uploads WHERE project_id = ?)", (project_id,))
        counts["uploads"] = self.conn.execute("DELETE FROM uploads WHERE project_id = ?", (project_id,)).rowcount
        for table in ("ml_predictions", "ml_models", "ml_jobs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
        self.conn.execute("DELETE FROM conversation_messages WHERE project_id = ?", (project_id,))
        for table in ("conversations", "insights", "analysis_runs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
        for table in ("extractions", "profiles", "transformations", "asset_versions", "assets", "project_events"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
        return counts
