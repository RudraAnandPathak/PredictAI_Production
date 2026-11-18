"""Removing Phase 7 and Phase 8 rows, and the stored objects only a row can name, when a file, a project or an account is deleted (added in Phase 8 to close a Phase 7 gap:
Phase 7 shipped without these rules, so deleting a project left its registry models, reports, exports, exam material, schedules and shares behind).
Plain SQL on purpose, like research_purge: the workspace module imports nothing from the Phase 7 packages.

What a deletion guarantees:
  * a project's deletion removes every registry model/version/event, exam subject/document/question/analysis/blueprint/assessment, advanced-intelligence run, report (versions, evidence,
    exports), schedule (runs, deliveries) and revokes every share that pointed at any of them; the stored model copies and report exports (which live under registry/ and reports/,
    outside the project prefix) are deleted too;
  * a file's deletion removes the exam documents and advanced-intelligence runs built from it, blanks quoted passages in report evidence that came from it, and removes research-workspace
    sources that point at it; its shares are revoked;
  * an account's deletion removes the person's research workspaces (sources, conversations, messages, notes), custom report templates and collaboration memberships, and revokes shares
    they gave. Audit and owner-event rows are kept: they hold identifiers and outcomes only.
Share rows are never deleted (they are the permission history); they are revoked."""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List

REVOKED_BY_DELETION = "deletion"


def registry_prefix(project_id: str) -> str:
    return f"registry/{project_id}/"


def reports_prefix(project_id: str) -> str:
    return f"reports/{project_id}/"


def project_object_keys(conn: sqlite3.Connection, project_id: str) -> List[str]:
    """Exact keys named by rows; used to prove nothing is left even if an object was written outside the usual prefix."""
    keys = [r[0] for r in conn.execute("SELECT v.artifact_key FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE m.project_id = ?", (project_id,)).fetchall()]
    keys += [r[0] for r in conn.execute("SELECT e.storage_key FROM rep_exports e JOIN rep_reports r ON r.id = e.report_id WHERE r.project_id = ?", (project_id,)).fetchall()]
    return [k for k in keys if k]


def _in(conn: sqlite3.Connection, sql: str, args: Iterable = ()) -> List[str]:
    return [r[0] for r in conn.execute(sql, tuple(args)).fetchall()]


def _revoke(conn: sqlite3.Connection, ids: List[str], now: str) -> int:
    n = 0
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        n += conn.execute(f"UPDATE ws_resources SET revoked_at = ?, revoked_by = ? WHERE revoked_at IS NULL AND resource_id IN ({','.join('?' * len(chunk))})",
                          (now, REVOKED_BY_DELETION, *chunk)).rowcount
    return n


def _delete_in(conn: sqlite3.Connection, table: str, column: str, ids: List[str]) -> int:
    n = 0
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        n += conn.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' * len(chunk))})", chunk).rowcount
    return n


def _exam_document_rows(conn: sqlite3.Connection, doc_ids: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    q_ids = []
    for i in range(0, len(doc_ids), 400):
        chunk = doc_ids[i:i + 400]
        q_ids += _in(conn, f"SELECT id FROM exam_questions WHERE document_id IN ({','.join('?' * len(chunk))})", chunk)
    counts["exam_mappings"] = _delete_in(conn, "exam_mappings", "question_id", q_ids)
    counts["exam_questions"] = _delete_in(conn, "exam_questions", "document_id", doc_ids)
    counts["exam_curriculum"] = _delete_in(conn, "exam_curriculum", "document_id", doc_ids)
    counts["exam_documents"] = _delete_in(conn, "exam_documents", "id", doc_ids)
    return counts


def asset_rows(conn: sqlite3.Connection, asset_id: str, now: str) -> Dict[str, int]:
    counts = dict(_exam_document_rows(conn, _in(conn, "SELECT id FROM exam_documents WHERE asset_id = ?", (asset_id,))))
    counts["adv_runs"] = conn.execute("DELETE FROM adv_runs WHERE asset_id = ?", (asset_id,)).rowcount
    counts["rep_evidence_passages_blanked"] = conn.execute("UPDATE rep_evidence SET passage = NULL WHERE source_id = ? AND passage IS NOT NULL", (asset_id,)).rowcount
    source_ids = _in(conn, "SELECT id FROM rw_sources WHERE ref_asset_id = ?", (asset_id,))
    conn.execute("UPDATE rw_notes SET source_id = NULL WHERE source_id IN (SELECT id FROM rw_sources WHERE ref_asset_id = ?)", (asset_id,))
    counts["rw_sources"] = _delete_in(conn, "rw_sources", "id", source_ids)
    counts["shares_revoked"] = _revoke(conn, [asset_id], now)
    return counts


def dev_project_dir(settings, project_id: str) -> Path:
    """Where the Development Workspace keeps a project's working tree, objects, environments and job folders (Phase 9)."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", project_id or ""):
        raise ValueError("unsafe project id")
    return Path(getattr(settings, "dev_root", "") or (Path(settings.storage_dir) / "dev")) / project_id


def dev_files_removed(settings, project_id: str) -> int:
    """Deletes the project's Development Workspace folder (working tree, content store, environments, job folders). Returns 1 if a folder existed."""
    folder = dev_project_dir(settings, project_id)
    existed = folder.exists()
    shutil.rmtree(folder, ignore_errors=True)
    return int(existed)


def dev_project_rows(conn: sqlite3.Connection, project_id: str) -> Dict[str, int]:
    """Phase 9: every dv_* row of the project, children before parents. Experiments go before registry rows because they point at registry versions. Plain SQL (no Phase 9 imports)."""
    c: Dict[str, int] = {}
    one = lambda sql: conn.execute(sql, (project_id,)).rowcount  # noqa: E731
    c["dv_webhook_deliveries"] = one("DELETE FROM dv_webhook_deliveries WHERE repo_id IN (SELECT id FROM dv_repos WHERE project_id = ?)")
    c["dv_repos"] = one("DELETE FROM dv_repos WHERE project_id = ?")
    c["dv_secrets"] = one("DELETE FROM dv_secrets WHERE project_id = ?")
    c["dv_pipeline_stages"] = one("DELETE FROM dv_pipeline_stages WHERE run_id IN (SELECT id FROM dv_pipeline_runs WHERE project_id = ?)")
    c["dv_pipeline_runs"] = one("DELETE FROM dv_pipeline_runs WHERE project_id = ?")
    c["dv_pipelines"] = one("DELETE FROM dv_pipelines WHERE project_id = ?")
    c["dv_experiments"] = one("DELETE FROM dv_experiments WHERE project_id = ?")
    c["dv_artifacts"] = one("DELETE FROM dv_artifacts WHERE project_id = ?")
    c["dv_job_output"] = one("DELETE FROM dv_job_output WHERE job_id IN (SELECT id FROM dv_jobs WHERE project_id = ?)")
    c["dv_jobs"] = one("DELETE FROM dv_jobs WHERE project_id = ?")
    c["dv_env_versions"] = one("DELETE FROM dv_env_versions WHERE env_id IN (SELECT id FROM dv_envs WHERE project_id = ?)")
    c["dv_envs"] = one("DELETE FROM dv_envs WHERE project_id = ?")
    c["dv_file_versions"] = one("DELETE FROM dv_file_versions WHERE file_id IN (SELECT id FROM dv_files WHERE project_id = ?)")
    c["dv_files"] = one("DELETE FROM dv_files WHERE project_id = ?")
    c["dv_sessions"] = one("DELETE FROM dv_sessions WHERE project_id = ?")
    c["dv_workspaces"] = one("DELETE FROM dv_workspaces WHERE project_id = ?")
    c["dv_quota_overrides"] = conn.execute("DELETE FROM dv_quota_overrides WHERE scope = 'project' AND scope_id = ?", (project_id,)).rowcount
    c["dv_events"] = one("DELETE FROM dv_events WHERE project_id = ?")
    return c


def project_rows(conn: sqlite3.Connection, project_id: str, now: str) -> Dict[str, int]:
    counts: Dict[str, int] = dev_project_rows(conn, project_id)
    model_ids = _in(conn, "SELECT id FROM reg_models WHERE project_id = ?", (project_id,))
    report_ids = _in(conn, "SELECT id FROM rep_reports WHERE project_id = ?", (project_id,))
    schedule_ids = _in(conn, "SELECT id FROM sch_schedules WHERE project_id = ?", (project_id,))
    subject_ids = _in(conn, "SELECT id FROM exam_subjects WHERE project_id = ?", (project_id,))
    version_ids = _in(conn, "SELECT v.id FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE m.project_id = ?", (project_id,))
    assessment_ids = _in(conn, "SELECT id FROM exam_assessments WHERE project_id = ?", (project_id,))
    blueprint_ids = _in(conn, "SELECT id FROM exam_blueprints WHERE project_id = ?", (project_id,))
    asset_ids = _in(conn, "SELECT id FROM assets WHERE project_id = ?", (project_id,))
    # shares first (so a revoked share is recorded even if a later statement fails inside the caller's transaction)
    counts["shares_revoked"] = _revoke(conn, [project_id, *model_ids, *version_ids, *report_ids, *schedule_ids, *subject_ids, *assessment_ids, *blueprint_ids, *asset_ids], now)
    # schedules reference reports
    run_ids = _in(conn, f"SELECT id FROM sch_runs WHERE schedule_id IN ({','.join('?' * len(schedule_ids))})", schedule_ids) if schedule_ids else []
    counts["sch_deliveries"] = _delete_in(conn, "sch_deliveries", "run_id", run_ids)
    counts["sch_runs"] = _delete_in(conn, "sch_runs", "schedule_id", schedule_ids)
    counts["sch_schedules"] = _delete_in(conn, "sch_schedules", "id", schedule_ids)
    for table in ("rep_exports", "rep_evidence", "rep_versions"):
        counts[table] = _delete_in(conn, table, "report_id", report_ids)
    counts["rep_reports"] = _delete_in(conn, "rep_reports", "id", report_ids)
    counts["adv_runs"] = conn.execute("DELETE FROM adv_runs WHERE project_id = ?", (project_id,)).rowcount
    counts["exam_predictions"] = conn.execute("DELETE FROM exam_predictions WHERE project_id = ?", (project_id,)).rowcount              # Phase 10B, before the subjects it points at
    counts["exam_assessment_questions"] = _delete_in(conn, "exam_assessment_questions", "assessment_id", assessment_ids)
    counts["exam_assessments"] = _delete_in(conn, "exam_assessments", "id", assessment_ids)
    counts["exam_blueprints"] = _delete_in(conn, "exam_blueprints", "id", blueprint_ids)
    counts["exam_analyses"] = conn.execute("DELETE FROM exam_analyses WHERE project_id = ?", (project_id,)).rowcount
    counts.update(_exam_document_rows(conn, _in(conn, "SELECT id FROM exam_documents WHERE project_id = ?", (project_id,))))
    counts["exam_subjects"] = _delete_in(conn, "exam_subjects", "id", subject_ids)
    counts["reg_events"] = _delete_in(conn, "reg_events", "model_id", model_ids)
    counts["reg_versions"] = _delete_in(conn, "reg_versions", "model_id", model_ids)
    counts["reg_models"] = _delete_in(conn, "reg_models", "id", model_ids)
    # research-workspace sources that point at one of this project's files
    conn.execute("UPDATE rw_notes SET source_id = NULL WHERE source_id IN (SELECT id FROM rw_sources WHERE ref_asset_id IN (SELECT id FROM assets WHERE project_id = ?))", (project_id,))
    counts["rw_sources"] = sum(conn.execute("DELETE FROM rw_sources WHERE ref_asset_id = ?", (a,)).rowcount for a in asset_ids)
    return counts


def user_rows(conn: sqlite3.Connection, user_id: str, now: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    counts["exam_predictions"] = conn.execute("DELETE FROM exam_predictions WHERE owner_user_id = ?", (user_id,)).rowcount
    for table in ("rw_notes", "rw_messages", "rw_conversations", "rw_sources", "rw_workspaces"):
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE owner_user_id = ?", (user_id,)).rowcount
    counts["dv_quota_overrides"] = conn.execute("DELETE FROM dv_quota_overrides WHERE scope = 'user' AND scope_id = ?", (user_id,)).rowcount
    counts["rep_templates"] = conn.execute("DELETE FROM rep_templates WHERE owner_user_id = ?", (user_id,)).rowcount
    counts["ws_members_removed"] = conn.execute("UPDATE ws_members SET status = 'removed', updated_at = ? WHERE user_id = ? AND status != 'removed'", (now, user_id)).rowcount
    counts["shares_revoked"] = conn.execute("UPDATE ws_resources SET revoked_at = ?, revoked_by = ? WHERE revoked_at IS NULL AND (resource_owner_id = ? OR shared_by = ?)",
                                            (now, REVOKED_BY_DELETION, user_id, user_id)).rowcount
    counts["ws_invitations_revoked"] = conn.execute("UPDATE ws_invitations SET status = 'revoked', resolved_at = ? WHERE invited_by = ? AND status = 'pending'", (now, user_id)).rowcount
    return counts
