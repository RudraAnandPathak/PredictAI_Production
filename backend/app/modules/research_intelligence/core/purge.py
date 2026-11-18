"""Deletion of Research Intelligence data: one source, one workspace, or everything an account owns. Used by source deletion, workspace deletion (researchspace service), account
deletion and project/file purges (phase7_purge). Order matters because of foreign keys: vectors and citations first, then chunks, then the source rows. Stored files are removed last,
after the database commit has made the rows unreachable; a failed file removal cannot resurrect anything."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ..retrieval.vectorstore import build_store_for
from .common import remove_stored_for


def _vector_cleanup(conn: sqlite3.Connection, settings: Any, iso, workspace_id: str, owner_id: str, source_id: Optional[str]) -> None:
    store = build_store_for(settings, conn, iso)
    try:
        if source_id:
            store.delete_source(workspace_id=workspace_id, owner_id=owner_id, source_id=source_id)
        else:
            store.delete_workspace(workspace_id=workspace_id, owner_id=owner_id)
    except RuntimeError:
        pass                                       # an unreachable external vector store must not block deleting the person's data; the SQL side is always cleared below
    conn.execute("DELETE FROM ri_embeddings WHERE workspace_id = ? AND owner_user_id = ?" + (" AND source_id = ?" if source_id else ""), (workspace_id, owner_id, *([source_id] if source_id else [])))


def purge_source(conn: sqlite3.Connection, settings: Any, iso, workspace_id: str, owner_id: str, source_id: str) -> Dict[str, int]:
    """Caller wraps in a transaction. Returns counts; removes the stored file after the rows are gone (caller commits first when it cares)."""
    row = conn.execute("SELECT stored_name FROM ri_sources WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (source_id, workspace_id, owner_id)).fetchone()
    counts: Dict[str, int] = {}
    if row is None:
        return counts
    _vector_cleanup(conn, settings, iso, workspace_id, owner_id, source_id)
    conn.execute("DELETE FROM ri_library_links WHERE source_id = ? AND owner_user_id = ?", (source_id, owner_id))
    conn.execute("DELETE FROM ri_source_facts WHERE source_id = ? AND owner_user_id = ?", (source_id, owner_id))
    from ..reports.service import redact_source
    counts["reports_redacted"] = redact_source(conn, workspace_id, owner_id, source_id)
    counts["ri_generated"] = conn.execute("DELETE FROM ri_generated WHERE workspace_id = ? AND owner_user_id = ? AND EXISTS (SELECT 1 FROM json_each(ri_generated.source_ids) WHERE value = ?)", (workspace_id, owner_id, source_id)).rowcount
    conn.execute("DELETE FROM ri_finding_links WHERE owner_user_id = ? AND (from_id IN (SELECT id FROM ri_findings WHERE source_id = ?) OR to_id IN (SELECT id FROM ri_findings WHERE source_id = ?))", (owner_id, source_id, source_id))
    conn.execute("DELETE FROM ri_finding_group_items WHERE finding_id IN (SELECT id FROM ri_findings WHERE source_id = ?)", (source_id,))
    # an analysis quotes its sources, so it does not outlive any of them
    counts["ri_analyses"] = conn.execute("DELETE FROM ri_analyses WHERE workspace_id = ? AND owner_user_id = ? AND EXISTS (SELECT 1 FROM json_each(ri_analyses.source_ids) WHERE value = ?)", (workspace_id, owner_id, source_id)).rowcount
    for table, col in (("ri_citations", "source_id"), ("ri_findings", "source_id"), ("ri_conversation_scope", "source_id"), ("ri_chunks", "source_id"), ("ri_source_versions", "source_id"), ("ri_jobs", "source_id")):
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE {col} = ? AND owner_user_id = ?", (source_id, owner_id)).rowcount
    conn.execute("UPDATE rw_notes SET source_id = NULL WHERE source_id = ? AND owner_user_id = ?", (source_id, owner_id))
    counts["ri_sources"] = conn.execute("DELETE FROM ri_sources WHERE id = ? AND owner_user_id = ?", (source_id, owner_id)).rowcount
    counts["rw_sources"] = conn.execute("DELETE FROM rw_sources WHERE id = ? AND owner_user_id = ?", (source_id, owner_id)).rowcount
    remove_stored_for(settings, row["stored_name"])
    return counts


def purge_workspace(conn: sqlite3.Connection, settings: Any, iso, workspace_id: str, owner_id: str) -> Dict[str, int]:
    """Remove every ri_* row of a workspace (rw_* rows are removed by the caller). Safe to call for a workspace that has none."""
    names: List[Optional[str]] = [r["stored_name"] for r in conn.execute("SELECT stored_name FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).fetchall()]
    _vector_cleanup(conn, settings, iso, workspace_id, owner_id, None)
    counts: Dict[str, int] = {}
    counts["nb_outputs"] = conn.execute("DELETE FROM nb_outputs WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount      # Phase 10C notebook outputs
    counts["nb_imports"] = conn.execute("DELETE FROM nb_imports WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount      # Phase 10D import records
    conn.execute("DELETE FROM ri_library_links WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id))
    conn.execute("DELETE FROM ri_source_facts WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id))
    conn.execute("DELETE FROM ri_report_versions WHERE report_id IN (SELECT id FROM ri_reports WHERE workspace_id = ? AND owner_user_id = ?)", (workspace_id, owner_id))
    conn.execute("DELETE FROM ri_finding_group_items WHERE group_id IN (SELECT id FROM ri_finding_groups WHERE workspace_id = ? AND owner_user_id = ?)", (workspace_id, owner_id))
    for t in ("ri_reports", "ri_generated", "ri_outlines", "ri_finding_links", "ri_finding_groups"):
        counts[t] = conn.execute(f"DELETE FROM {t} WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount
    counts["ri_analyses"] = conn.execute("DELETE FROM ri_analyses WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount
    counts["ri_discovery_searches"] = conn.execute("DELETE FROM ri_discovery_searches WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount
    for table in ("ri_citations", "ri_message_meta", "ri_findings", "ri_jobs", "ri_chunks", "ri_source_versions", "ri_usage", "ri_workspace_settings"):
        col = "workspace_id"
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE {col} = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount
    conn.execute("DELETE FROM ri_conversation_scope WHERE owner_user_id = ? AND conversation_id IN (SELECT id FROM rw_conversations WHERE workspace_id = ?)", (owner_id, workspace_id))
    counts["ri_sources"] = conn.execute("DELETE FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).rowcount
    for n in names:
        remove_stored_for(settings, n)
    return counts


def purge_user(conn: sqlite3.Connection, settings: Any, iso, owner_id: str) -> Dict[str, int]:
    """Account deletion: every workspace of the person. rw_* rows are removed by the existing account purge; this clears the ri_* side first."""
    total: Dict[str, int] = {}
    ids = [r[0] for r in conn.execute("SELECT DISTINCT workspace_id FROM ri_sources WHERE owner_user_id = ? UNION SELECT DISTINCT workspace_id FROM ri_jobs WHERE owner_user_id = ?", (owner_id, owner_id)).fetchall()]
    ids += [r[0] for r in conn.execute("SELECT id FROM rw_workspaces WHERE owner_user_id = ?", (owner_id,)).fetchall() if r[0] not in ids]
    for wid in ids:
        for k, v in purge_workspace(conn, settings, iso, wid, owner_id).items():
            total[k] = total.get(k, 0) + v
    total["ri_analyses"] = total.get("ri_analyses", 0) + conn.execute("DELETE FROM ri_analyses WHERE owner_user_id = ?", (owner_id,)).rowcount
    conn.execute("DELETE FROM ri_source_facts WHERE owner_user_id = ?", (owner_id,))
    total["ri_library_links"] = conn.execute("DELETE FROM ri_library_links WHERE owner_user_id = ?", (owner_id,)).rowcount
    total["ri_library"] = conn.execute("DELETE FROM ri_library WHERE owner_user_id = ?", (owner_id,)).rowcount
    total["ri_discovery_searches"] = total.get("ri_discovery_searches", 0) + conn.execute("DELETE FROM ri_discovery_searches WHERE owner_user_id = ?", (owner_id,)).rowcount
    total["ri_usage"] = total.get("ri_usage", 0) + conn.execute("DELETE FROM ri_usage WHERE owner_user_id = ?", (owner_id,)).rowcount
    return total
