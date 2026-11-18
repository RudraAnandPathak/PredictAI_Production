"""Removing Phase 6 rows (and the stored objects only a row can name) when a file, a project or an account is deleted.
Plain SQL on purpose: the workspace module must not import the research package, so this file depends on nothing but the database schema.

What a deletion guarantees:
  * a file's deletion also deletes every evidence bundle that quotes it (and that bundle's sources), every retrieval index row, and any source record that points at it,
    and removes derived search text for the sessions those bundles belonged to, so a deleted document's words do not linger in research history;
  * a dataset's deletion also deletes every influence analysis computed from it (the saved result holds summaries of the dataset);
  * a project's deletion removes every research, retrieval, news and influence row of the project (its stored objects live under the project prefix and go with it);
  * an account's deletion also removes the person's own settings and AI usage rows.
Notes are the person's own writing and are kept when a file is deleted; they never contain file text unless the person typed it."""
from __future__ import annotations

import sqlite3
from typing import Dict, List

PROJECT_TABLES = ("research_notes", "research_sources", "evidence_bundles", "research_jobs", "research_sessions", "web_cache", "doc_indexes", "news_articles", "news_events", "news_sources", "influence_analyses")


def bundles_citing(conn: sqlite3.Connection, asset_id: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT b.id, b.bundle_key, b.session_id FROM evidence_bundles b, json_each(b.asset_ids) j WHERE j.value = ?", (asset_id,)).fetchall()


def asset_bundle_keys(conn: sqlite3.Connection, asset_id: str) -> List[str]:
    return [r["bundle_key"] for r in bundles_citing(conn, asset_id)]


def asset_influence_keys(conn: sqlite3.Connection, asset_id: str) -> List[str]:
    return [r["result_key"] for r in conn.execute("SELECT result_key FROM influence_analyses WHERE json_extract(config, '$.asset_id') = ?", (asset_id,)).fetchall()]


def asset_rows(conn: sqlite3.Connection, asset_id: str) -> Dict[str, int]:
    counts = {"evidence_bundles": 0, "research_sources": 0, "doc_indexes": 0, "influence_analyses": 0}
    counts["influence_analyses"] = conn.execute("DELETE FROM influence_analyses WHERE json_extract(config, '$.asset_id') = ?", (asset_id,)).rowcount
    rows = bundles_citing(conn, asset_id)
    sessions = {r["session_id"] for r in rows}
    for r in rows:
        conn.execute("DELETE FROM research_sources WHERE bundle_id = ?", (r["id"],))
        counts["evidence_bundles"] += conn.execute("DELETE FROM evidence_bundles WHERE id = ?", (r["id"],)).rowcount
    counts["research_sources"] += conn.execute("DELETE FROM research_sources WHERE ref_asset_id = ?", (asset_id,)).rowcount
    counts["doc_indexes"] = conn.execute("DELETE FROM doc_indexes WHERE asset_id = ?", (asset_id,)).rowcount
    for sid in sessions:
        latest = conn.execute("SELECT id FROM evidence_bundles WHERE session_id = ? ORDER BY number DESC LIMIT 1", (sid,)).fetchone()
        notes = " \n".join(n["body"] for n in conn.execute("SELECT body FROM research_notes WHERE session_id = ? ORDER BY created_at", (sid,)).fetchall())
        s = conn.execute("SELECT title, question FROM research_sessions WHERE id = ?", (sid,)).fetchone()
        if s is not None:                                                    # derived text is rebuilt without any conclusion: it is refreshed again on the next run or note
            conn.execute("UPDATE research_sessions SET latest_bundle_id = ?, search_text = ? WHERE id = ?", (latest["id"] if latest else None, (f"{s['title']} \n{s['question']} \n{notes}")[:20000], sid))
    return counts


def project_rows(conn: sqlite3.Connection, project_id: str) -> Dict[str, int]:
    counts = {}
    for table in PROJECT_TABLES:
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
    counts["research_settings"] = conn.execute("DELETE FROM research_settings WHERE project_id = ?", (project_id,)).rowcount
    counts["ai_usage"] = conn.execute("DELETE FROM ai_usage WHERE project_id = ?", (project_id,)).rowcount
    return counts


def user_rows(conn: sqlite3.Connection, user_id: str) -> Dict[str, int]:
    return {"research_settings": conn.execute("DELETE FROM research_settings WHERE owner_user_id = ?", (user_id,)).rowcount,
            "ai_usage": conn.execute("DELETE FROM ai_usage WHERE owner_user_id = ?", (user_id,)).rowcount}
