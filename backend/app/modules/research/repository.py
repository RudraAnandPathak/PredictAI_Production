"""SQL for research sessions, jobs, sources, bundles and notes. Every read filters by the owner; nothing here decides what a person may do (the service does)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Sequence

SESSION_COLUMNS = {"title", "question", "level", "purpose", "status", "options", "plan", "plan_hash", "approved_plan_hash", "latest_bundle_id", "run_count", "saved", "search_text", "updated_at", "last_run_at"}
JOB_COLUMNS = {"status", "progress", "error_code", "error_message", "cancel_requested", "attempts", "heartbeat_at", "result_id", "started_at", "finished_at", "duration_ms"}
TERMINAL = ("completed", "failed", "cancelled")


class ResearchRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------ sessions
    def create_session(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "title", "question", "level", "purpose", "status", "options", "plan", "plan_hash", "approved_plan_hash", "latest_bundle_id", "run_count", "saved", "search_text", "created_at", "updated_at", "last_run_at")
        row = {c: v.get(c) for c in cols}
        row["run_count"] = row["run_count"] or 0
        row["saved"] = row["saved"] or 0
        self.conn.execute(f"INSERT INTO research_sessions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])

    def session(self, owner: str, session_id: str):
        return self.conn.execute("SELECT * FROM research_sessions WHERE id = ? AND owner_user_id = ?", (session_id, owner)).fetchone()

    def update_session(self, session_id: str, **fields: Any) -> None:
        bad = set(fields) - SESSION_COLUMNS
        if bad:
            raise ValueError(f"unknown session columns {sorted(bad)}")
        if fields:
            self.conn.execute(f"UPDATE research_sessions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), session_id])

    def sessions(self, owner: str, project_id: str, *, status: Optional[str] = None, saved: Optional[bool] = None, limit: int = 50, offset: int = 0) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM research_sessions WHERE owner_user_id = ? AND project_id = ?", [owner, project_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        if saved is not None:
            sql += " AND saved = ?"
            args.append(int(saved))
        return self.conn.execute(sql + " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()

    def count_sessions(self, owner: str, project_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM research_sessions WHERE owner_user_id = ? AND project_id = ?", (owner, project_id)).fetchone()[0]

    def search_sessions(self, owner: str, project_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT id, title, question, level, status, saved, search_text, updated_at, latest_bundle_id, run_count FROM research_sessions WHERE owner_user_id = ? AND project_id = ? ORDER BY updated_at DESC LIMIT 500",
                                 (owner, project_id)).fetchall()

    # ------------------------------------------------------------------ jobs
    def create_job(self, **v: Any) -> None:
        cols = ("id", "kind", "owner_user_id", "project_id", "session_id", "subject_id", "status", "params", "extended", "progress", "created_at")
        self.conn.execute(f"INSERT INTO research_jobs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def job(self, owner: str, job_id: str):
        return self.conn.execute("SELECT * FROM research_jobs WHERE id = ? AND owner_user_id = ?", (job_id, owner)).fetchone()

    def job_internal(self, job_id: str):
        return self.conn.execute("SELECT * FROM research_jobs WHERE id = ?", (job_id,)).fetchone()

    def jobs(self, owner: str, project_id: str, *, session_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 50) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM research_jobs WHERE owner_user_id = ? AND project_id = ?", [owner, project_id]
        if session_id:
            sql += " AND session_id = ?"
            args.append(session_id)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return self.conn.execute(sql + " ORDER BY created_at DESC, id LIMIT ?", [*args, limit]).fetchall()

    def active_jobs(self, owner: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM research_jobs WHERE owner_user_id = ? AND status IN ('queued','running')", (owner,)).fetchone()[0]

    def active_job_for_session(self, owner: str, session_id: str):
        return self.conn.execute("SELECT * FROM research_jobs WHERE owner_user_id = ? AND session_id = ? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (owner, session_id)).fetchone()

    def set_job(self, job_id: str, **fields: Any) -> None:
        bad = set(fields) - JOB_COLUMNS
        if bad:
            raise ValueError(f"unknown job columns {sorted(bad)}")
        if fields:
            self.conn.execute(f"UPDATE research_jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), job_id])

    def claim_job(self, job_id: str, now: str) -> bool:
        cur = self.conn.execute("UPDATE research_jobs SET status = 'running', started_at = ?, heartbeat_at = ?, attempts = attempts + 1 WHERE id = ? AND status = 'queued' AND cancel_requested = 0", (now, now, job_id))
        return cur.rowcount == 1

    def cancel_state(self, job_id: str) -> Optional[bool]:
        r = self.conn.execute("SELECT cancel_requested FROM research_jobs WHERE id = ?", (job_id,)).fetchone()
        return None if r is None else bool(r[0])

    def request_cancel(self, owner: str, job_id: str) -> bool:
        return self.conn.execute("UPDATE research_jobs SET cancel_requested = 1 WHERE id = ? AND owner_user_id = ? AND status IN ('queued','running')", (job_id, owner)).rowcount == 1

    def finish_job(self, job_id: str, *, status: str, now: str, **fields: Any) -> bool:
        bad = set(fields) - JOB_COLUMNS
        if bad:
            raise ValueError(f"unknown job columns {sorted(bad)}")
        fields = {**fields, "status": status, "finished_at": now}
        cur = self.conn.execute(f"UPDATE research_jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND status IN ('queued','running')", [*fields.values(), job_id])
        return cur.rowcount == 1

    def stale_jobs(self, *, running_before: str, queued_before: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM research_jobs WHERE (status = 'running' AND COALESCE(heartbeat_at, started_at, created_at) < ?) OR (status = 'queued' AND created_at < ?)", (running_before, queued_before)).fetchall()

    # ------------------------------------------------------------------ sources
    def upsert_source(self, **v: Any) -> str:
        existing = self.conn.execute("SELECT id, created_at, saved FROM research_sources WHERE session_id = ? AND source_key = ?", (v["session_id"], v["source_key"])).fetchone()
        cols = ("project_id", "owner_user_id", "session_id", "bundle_id", "source_key", "kind", "origin_class", "title", "url", "domain", "publisher", "ref_asset_id", "ref_id", "content_hash", "text_key",
                "retrieved_at", "published_at", "freshness", "reliability", "status", "status_code", "provider", "updated_at")
        if existing:
            sets = [c for c in cols if c not in ("project_id", "owner_user_id", "session_id", "source_key")]
            self.conn.execute(f"UPDATE research_sources SET {', '.join(f'{c} = ?' for c in sets)} WHERE id = ?", [v.get(c) for c in sets] + [existing["id"]])
            return existing["id"]
        self.conn.execute(f"INSERT INTO research_sources (id, {', '.join(cols)}, created_at) VALUES ({', '.join('?' for _ in range(len(cols) + 2))})", [v["id"], *[v.get(c) for c in cols], v["updated_at"]])
        return v["id"]

    def source(self, owner: str, source_id: str):
        return self.conn.execute("SELECT * FROM research_sources WHERE id = ? AND owner_user_id = ?", (source_id, owner)).fetchone()

    def sources_of_session(self, owner: str, session_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM research_sources WHERE owner_user_id = ? AND session_id = ? ORDER BY created_at, id", (owner, session_id)).fetchall()

    def sources(self, owner: str, project_id: str, *, kind: Optional[str] = None, status: Optional[str] = None, saved: Optional[bool] = None, limit: int = 100, offset: int = 0) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM research_sources WHERE owner_user_id = ? AND project_id = ?", [owner, project_id]
        for col, val in (("kind", kind), ("status", status)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        if saved is not None:
            sql += " AND saved = ?"
            args.append(int(saved))
        return self.conn.execute(sql + " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()

    def set_source_saved(self, owner: str, source_id: str, saved: bool) -> bool:
        return self.conn.execute("UPDATE research_sources SET saved = ? WHERE id = ? AND owner_user_id = ?", (int(saved), source_id, owner)).rowcount == 1

    # ------------------------------------------------------------------ bundles
    def create_bundle(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "session_id", "job_id", "number", "question", "level", "status", "bundle_key", "bundle_sha256", "asset_ids", "source_count", "evidence_count",
                "finding_count", "conflict_count", "quality_verdict", "used_web", "label", "saved", "created_at")
        row = {c: v.get(c) for c in cols}
        row["saved"] = row["saved"] or 0
        self.conn.execute(f"INSERT INTO evidence_bundles ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])

    def bundle(self, owner: str, bundle_id: str):
        return self.conn.execute("SELECT * FROM evidence_bundles WHERE id = ? AND owner_user_id = ?", (bundle_id, owner)).fetchone()

    def bundles_of_session(self, owner: str, session_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM evidence_bundles WHERE owner_user_id = ? AND session_id = ? ORDER BY number DESC", (owner, session_id)).fetchall()

    def bundles(self, owner: str, project_id: str, limit: int = 100) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM evidence_bundles WHERE owner_user_id = ? AND project_id = ? ORDER BY created_at DESC, id LIMIT ?", (owner, project_id, limit)).fetchall()

    def next_bundle_number(self, session_id: str) -> int:
        return (self.conn.execute("SELECT COALESCE(MAX(number), 0) FROM evidence_bundles WHERE session_id = ?", (session_id,)).fetchone()[0] or 0) + 1

    def set_bundle(self, owner: str, bundle_id: str, **fields: Any) -> bool:
        if set(fields) - {"saved", "label"}:
            raise ValueError("unknown bundle columns")
        return self.conn.execute(f"UPDATE evidence_bundles SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND owner_user_id = ?", [*fields.values(), bundle_id, owner]).rowcount == 1

    def delete_bundle(self, bundle_id: str) -> None:
        self.conn.execute("DELETE FROM evidence_bundles WHERE id = ?", (bundle_id,))

    # ------------------------------------------------------------------ notes
    def create_note(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "session_id", "target_type", "target_id", "body", "created_at", "updated_at")
        self.conn.execute(f"INSERT INTO research_notes ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def note(self, owner: str, note_id: str):
        return self.conn.execute("SELECT * FROM research_notes WHERE id = ? AND owner_user_id = ?", (note_id, owner)).fetchone()

    def notes(self, owner: str, session_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM research_notes WHERE owner_user_id = ? AND session_id = ? ORDER BY created_at, id", (owner, session_id)).fetchall()

    def update_note(self, owner: str, note_id: str, body: str, now: str) -> bool:
        return self.conn.execute("UPDATE research_notes SET body = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?", (body, now, note_id, owner)).rowcount == 1

    def delete_note(self, owner: str, note_id: str) -> bool:
        return self.conn.execute("DELETE FROM research_notes WHERE id = ? AND owner_user_id = ?", (note_id, owner)).rowcount == 1

    def count_notes(self, owner: str, session_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM research_notes WHERE owner_user_id = ? AND session_id = ?", (owner, session_id)).fetchone()[0]

    # ------------------------------------------------------------------ deletion
    def delete_session_rows(self, session_id: str) -> Dict[str, int]:
        counts = {}
        for table in ("research_notes", "research_sources", "research_jobs", "evidence_bundles"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,)).rowcount
        counts["research_sessions"] = self.conn.execute("DELETE FROM research_sessions WHERE id = ?", (session_id,)).rowcount
        return counts
