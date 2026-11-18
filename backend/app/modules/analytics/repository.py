"""All SQL for the analytics tables (migration 0003). Every read that returns user data is filtered by owner."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

Row = sqlite3.Row


class AnalyticsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Row]:
        return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: Sequence[Any] = ()) -> List[Row]:
        return self.conn.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- runs
    def create_run(self, *, id: str, project_id: str, asset_id: str, version_id: str, owner: str, kind: str, status: str, evidence_status: str, plan: str, plan_hash: str,
                   result_hash: Optional[str], result_key: Optional[str], summary: str, error_code: Optional[str], calc_version: str, rows_used: Optional[int],
                   duration_ms: int, parent_run_id: Optional[str], label: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO analysis_runs (id, project_id, asset_id, version_id, owner_user_id, kind, status, evidence_status, plan, plan_hash, result_hash, result_key, summary, "
            "error_code, calc_version, rows_used, duration_ms, parent_run_id, label, saved, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (id, project_id, asset_id, version_id, owner, kind, status, evidence_status, plan, plan_hash, result_hash, result_key, summary, error_code, calc_version, rows_used,
             duration_ms, parent_run_id, label, now))

    def run(self, owner: str, run_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM analysis_runs WHERE id = ? AND owner_user_id = ?", (run_id, owner))

    def runs_for_asset(self, owner: str, asset_id: str, *, kind: Optional[str], saved_only: bool, limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = "asset_id = ? AND owner_user_id = ?", [asset_id, owner]
        if kind:
            where += " AND kind = ?"
            params.append(kind)
        if saved_only:
            where += " AND saved = 1"
        total = self._one(f"SELECT COUNT(*) AS n FROM analysis_runs WHERE {where}", params)["n"]
        rows = self._all(f"SELECT * FROM analysis_runs WHERE {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?", params + [limit, offset])
        return rows, total

    def count_tests(self, owner: str, version_id: str, since: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM analysis_runs WHERE owner_user_id = ? AND version_id = ? AND kind = 'hypothesis' AND status = 'completed' AND created_at >= ?",
                         (owner, version_id, since))["n"]

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {"saved", "label"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unexpected fields {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE analysis_runs SET {sets} WHERE id = ?", (*fields.values(), run_id))

    def latest_run_by_hash(self, owner: str, version_id: str, kind: str, plan_hash: str) -> Optional[Row]:
        return self._one("SELECT * FROM analysis_runs WHERE owner_user_id = ? AND version_id = ? AND kind = ? AND plan_hash = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
                         (owner, version_id, kind, plan_hash))

    # ---------------------------------------------------------------- insights
    def upsert_insight(self, *, id: str, run_id: str, project_id: str, asset_id: str, version_id: str, owner: str, category: str, statement: str, dedupe_key: str,
                       evidence_status: str, payload: str, now: str) -> Tuple[str, str]:
        """Insert, or refresh an existing insight with the same dedupe key while keeping the person's saved/dismissed choice. Returns (id, state)."""
        existing = self._one("SELECT id, state FROM insights WHERE version_id = ? AND dedupe_key = ? AND owner_user_id = ?", (version_id, dedupe_key, owner))
        if existing:
            self.conn.execute("UPDATE insights SET run_id = ?, statement = ?, evidence_status = ?, payload = ?, updated_at = ? WHERE id = ?",
                              (run_id, statement, evidence_status, payload, now, existing["id"]))
            return existing["id"], existing["state"]
        self.conn.execute(
            "INSERT INTO insights (id, run_id, project_id, asset_id, version_id, owner_user_id, category, statement, dedupe_key, state, evidence_status, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)", (id, run_id, project_id, asset_id, version_id, owner, category, statement, dedupe_key, evidence_status, payload, now, now))
        return id, "new"

    def insight(self, owner: str, insight_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM insights WHERE id = ? AND owner_user_id = ?", (insight_id, owner))

    def insights_for_asset(self, owner: str, asset_id: str, *, state: Optional[str], version_id: Optional[str], limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = "asset_id = ? AND owner_user_id = ?", [asset_id, owner]
        if state:
            where += " AND state = ?"
            params.append(state)
        if version_id:
            where += " AND version_id = ?"
            params.append(version_id)
        total = self._one(f"SELECT COUNT(*) AS n FROM insights WHERE {where}", params)["n"]
        rows = self._all(f"SELECT * FROM insights WHERE {where} ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?", params + [limit, offset])
        return rows, total

    def set_insight_state(self, insight_id: str, state: str, now: str) -> None:
        self.conn.execute("UPDATE insights SET state = ?, updated_at = ? WHERE id = ?", (state, now, insight_id))

    # ---------------------------------------------------------------- conversations
    def create_conversation(self, *, id: str, project_id: str, asset_id: str, owner: str, title: str, now: str) -> None:
        self.conn.execute("INSERT INTO conversations (id, project_id, asset_id, owner_user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (id, project_id, asset_id, owner, title, now, now))

    def conversation(self, owner: str, conversation_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM conversations WHERE id = ? AND owner_user_id = ?", (conversation_id, owner))

    def conversations_for_asset(self, owner: str, asset_id: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM conversations WHERE asset_id = ? AND owner_user_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?", (asset_id, owner, limit))

    def rename_conversation(self, owner: str, conversation_id: str, title: str, now: str) -> None:
        self.conn.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?", (title, now, conversation_id, owner))

    def delete_conversation(self, owner: str, conversation_id: str) -> None:
        self.conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ? AND owner_user_id = ?", (conversation_id, owner))
        self.conn.execute("DELETE FROM conversations WHERE id = ? AND owner_user_id = ?", (conversation_id, owner))

    def touch_conversation(self, conversation_id: str, now: str) -> None:
        self.conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    def add_message(self, *, id: str, conversation_id: str, project_id: str, owner: str, role: str, content: str, run_id: Optional[str], payload: Optional[str], now: str) -> None:
        self.conn.execute("INSERT INTO conversation_messages (id, conversation_id, project_id, owner_user_id, role, content, run_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (id, conversation_id, project_id, owner, role, content, run_id, payload, now))

    def messages(self, owner: str, conversation_id: str, limit: int = 200) -> List[Row]:
        return self._all("SELECT * FROM conversation_messages WHERE conversation_id = ? AND owner_user_id = ? ORDER BY created_at, rowid LIMIT ?", (conversation_id, owner, limit))

    # ---------------------------------------------------------------- purge (called by workspace deletion)
    def purge_asset(self, asset_id: str) -> Dict[str, int]:
        counts = {}
        self.conn.execute("DELETE FROM conversation_messages WHERE conversation_id IN (SELECT id FROM conversations WHERE asset_id = ?)", (asset_id,))
        for table in ("conversations", "insights", "analysis_runs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,)).rowcount
        return counts
