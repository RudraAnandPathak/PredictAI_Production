"""SQL for the Model Registry. Reads by id only; who may read is decided by the service through the access resolver."""
from __future__ import annotations

import sqlite3
from typing import Any, List, Optional


class RegistryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def model(self, model_id: Any):
        return self.conn.execute("SELECT * FROM reg_models WHERE id = ?", (model_id,)).fetchone() if isinstance(model_id, str) else None

    def model_by_name(self, owner: str, project_id: str, name: str):
        return self.conn.execute("SELECT * FROM reg_models WHERE owner_user_id = ? AND project_id = ? AND name = ?", (owner, project_id, name)).fetchone()

    def models(self, owner: str, project_id: Optional[str], limit: int, offset: int):
        where, args = "owner_user_id = ?", [owner]
        if project_id:
            where += " AND project_id = ?"
            args.append(project_id)
        total = self.conn.execute(f"SELECT COUNT(*) FROM reg_models WHERE {where}", args).fetchone()[0]
        rows = self.conn.execute(f"SELECT * FROM reg_models WHERE {where} ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return rows, total

    def insert_model(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "name", "description", "model_type", "active_version_id", "created_at", "updated_at")
        self.conn.execute(f"INSERT INTO reg_models ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def touch_model(self, model_id: str, now: str, active_version_id: Any = ...) -> None:
        if active_version_id is ...:
            self.conn.execute("UPDATE reg_models SET updated_at = ? WHERE id = ?", (now, model_id))
        else:
            self.conn.execute("UPDATE reg_models SET updated_at = ?, active_version_id = ? WHERE id = ?", (now, active_version_id, model_id))

    def versions(self, model_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM reg_versions WHERE model_id = ? ORDER BY version", (model_id,)).fetchall()

    def version(self, version_id: Any):
        return self.conn.execute("SELECT * FROM reg_versions WHERE id = ?", (version_id,)).fetchone() if isinstance(version_id, str) else None

    def next_version(self, model_id: str) -> int:
        return (self.conn.execute("SELECT COALESCE(MAX(version), 0) FROM reg_versions WHERE model_id = ?", (model_id,)).fetchone()[0]) + 1

    def version_for_source(self, model_id: str, source_model_id: str):
        return self.conn.execute("SELECT * FROM reg_versions WHERE model_id = ? AND source_model_id = ?", (model_id, source_model_id)).fetchone()

    def insert_version(self, **v: Any) -> None:
        cols = ("id", "model_id", "version", "source_model_id", "status", "metadata", "evaluation", "artifact_key", "artifact_sha256", "artifact_bytes", "last_verified_at", "last_verified_ok", "created_by", "created_at")
        self.conn.execute(f"INSERT INTO reg_versions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def set_status(self, version_id: str, status: str) -> None:
        self.conn.execute("UPDATE reg_versions SET status = ? WHERE id = ?", (status, version_id))

    def set_verified(self, version_id: str, when: str, ok: bool) -> None:
        self.conn.execute("UPDATE reg_versions SET last_verified_at = ?, last_verified_ok = ? WHERE id = ?", (when, int(ok), version_id))

    def add_event(self, model_id: str, version_id: Optional[str], actor: str, event: str, detail: str, now: str) -> None:
        self.conn.execute("INSERT INTO reg_events (model_id, version_id, actor_user_id, event, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)", (model_id, version_id, actor, event, detail, now))

    def events(self, model_id: str, limit: int, offset: int) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM reg_events WHERE model_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (model_id, limit, offset)).fetchall()

    def counts_for_owner(self, owner: str):
        return self.conn.execute("SELECT v.status AS status, COUNT(*) AS n FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE m.owner_user_id = ? GROUP BY v.status", (owner,)).fetchall()
