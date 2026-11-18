"""Whether a person's documents may go to an external AI provider.

One rule for the whole platform: the person's own setting decides (default ON, see ``PREDICTAI_AI_EXTERNAL_DEFAULT``), and turning it OFF affects only that person.
A workspace may still be set to ``local_only`` explicitly; that can only narrow, never widen, what the person allowed."""
from __future__ import annotations

import sqlite3
from typing import Any


def person_allows_external(conn: sqlite3.Connection, settings: Any, owner_id: str) -> bool:
    row = conn.execute("SELECT external_ai FROM research_settings WHERE owner_user_id = ? AND project_id IS NULL", (owner_id,)).fetchone()
    return bool(row["external_ai"]) if row else bool(getattr(settings, "ai_external_default", False))


def external_allowed(conn: sqlite3.Connection, settings: Any, owner_id: str, workspace_id: str) -> bool:
    if not person_allows_external(conn, settings, owner_id):
        return False
    row = conn.execute("SELECT ai_data_policy FROM ri_workspace_settings WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).fetchone()
    return True if row is None else row["ai_data_policy"] == "external_allowed"
