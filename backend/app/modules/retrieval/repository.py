"""doc_indexes: one row per document version that has a derived search index. The index is rebuildable; the extraction stays the source of truth."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


class RetrievalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, owner: str, asset_id: str, version_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM doc_indexes WHERE asset_id = ? AND version_id = ? AND owner_user_id = ?", (asset_id, version_id, owner)).fetchone()

    def for_project(self, owner: str, project_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM doc_indexes WHERE project_id = ? AND owner_user_id = ?", (project_id, owner)).fetchall()

    def upsert(self, *, id: str, project_id: str, owner: str, asset_id: str, version_id: str, extraction_id: Optional[str], index_version: str, chunk_count: int, page_count: Optional[int],
               key: str, sha256: str, size_bytes: int, now: str) -> None:
        self.conn.execute("DELETE FROM doc_indexes WHERE asset_id = ? AND version_id = ?", (asset_id, version_id))
        self.conn.execute("INSERT INTO doc_indexes (id, project_id, owner_user_id, asset_id, version_id, extraction_id, index_version, chunk_count, page_count, key, sha256, size_bytes, built_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (id, project_id, owner, asset_id, version_id, extraction_id, index_version, chunk_count, page_count, key, sha256, size_bytes, now))

    def set_dense(self, row_id: str, model: Optional[str], key: Optional[str]) -> None:
        self.conn.execute("UPDATE doc_indexes SET embedding_model = ?, embedding_key = ? WHERE id = ?", (model, key, row_id))

    def delete_for_asset(self, asset_id: str) -> int:
        return self.conn.execute("DELETE FROM doc_indexes WHERE asset_id = ?", (asset_id,)).rowcount

    def delete_for_project(self, project_id: str) -> int:
        return self.conn.execute("DELETE FROM doc_indexes WHERE project_id = ?", (project_id,)).rowcount
