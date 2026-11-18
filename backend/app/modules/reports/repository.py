"""SQL for reports, versions, evidence, exports and templates. Versions and evidence are insert-only."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence


class ReportRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def report(self, report_id: Any):
        return self.conn.execute("SELECT * FROM rep_reports WHERE id = ? AND status = 'active'", (report_id,)).fetchone() if isinstance(report_id, str) else None

    def reports(self, owner: str, project_id: Optional[str], limit: int, offset: int):
        where, args = "owner_user_id = ? AND status = 'active'", [owner]
        if project_id:
            where += " AND project_id = ?"
            args.append(project_id)
        total = self.conn.execute(f"SELECT COUNT(*) FROM rep_reports WHERE {where}", args).fetchone()[0]
        return self.conn.execute(f"SELECT * FROM rep_reports WHERE {where} ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall(), total

    def insert_report(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "title", "report_type", "source_kind", "source_ref", "current_version", "status", "created_at", "updated_at")
        self.conn.execute(f"INSERT INTO rep_reports ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def bump(self, report_id: str, version: int, title: Optional[str], now: str) -> None:
        if title is None:
            self.conn.execute("UPDATE rep_reports SET current_version = ?, updated_at = ? WHERE id = ?", (version, now, report_id))
        else:
            self.conn.execute("UPDATE rep_reports SET current_version = ?, title = ?, updated_at = ? WHERE id = ?", (version, title, now, report_id))

    def mark_deleted(self, report_id: str, now: str) -> None:
        self.conn.execute("UPDATE rep_reports SET status = 'deleted', updated_at = ? WHERE id = ?", (now, report_id))

    def insert_version(self, **v: Any) -> None:
        cols = ("id", "report_id", "version", "content", "settings", "template_key", "template_version", "readiness", "content_sha256", "author_user_id", "note", "created_at")
        self.conn.execute(f"INSERT INTO rep_versions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def version(self, report_id: str, version: int):
        return self.conn.execute("SELECT * FROM rep_versions WHERE report_id = ? AND version = ?", (report_id, version)).fetchone()

    def versions(self, report_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT id, version, template_key, template_version, readiness, content_sha256, author_user_id, note, created_at FROM rep_versions WHERE report_id = ? ORDER BY version DESC", (report_id,)).fetchall()

    def insert_evidence(self, report_id: str, item: Dict[str, Any], eid: str) -> None:
        from ..p7 import jdump
        self.conn.execute("INSERT INTO rep_evidence (id, report_id, ref, source_id, source_type, location, citation, anchor, passage, calculation_ref, dataset_version, model_version, source_date, retrieved_at, verification, uncertainty, limitation, bundle_ref) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (eid, report_id, item["ref"], item.get("source_id"), item["source_type"], item.get("location"), jdump(item["citation"]), item.get("anchor"), item.get("passage"), item.get("calculation_ref"),
                           item.get("dataset_version"), item.get("model_version"), item.get("source_date"), item.get("retrieved_at"), item["verification"], item.get("uncertainty"), item.get("limitation"), item.get("bundle_ref")))

    def evidence(self, report_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM rep_evidence WHERE report_id = ? ORDER BY CAST(SUBSTR(ref, 2) AS INTEGER)", (report_id,)).fetchall()

    def next_ref(self, report_id: str) -> int:
        row = self.conn.execute("SELECT MAX(CAST(SUBSTR(ref, 2) AS INTEGER)) FROM rep_evidence WHERE report_id = ?", (report_id,)).fetchone()
        return (row[0] or 0) + 1

    def insert_export(self, **v: Any) -> None:
        cols = ("id", "report_id", "version", "format", "storage_key", "sha256", "bytes", "readiness", "actor_user_id", "origin", "created_at")
        self.conn.execute(f"INSERT INTO rep_exports ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def export(self, export_id: Any):
        return self.conn.execute("SELECT * FROM rep_exports WHERE id = ?", (export_id,)).fetchone() if isinstance(export_id, str) else None

    def exports(self, report_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM rep_exports WHERE report_id = ? ORDER BY created_at DESC, id", (report_id,)).fetchall()

    def user_templates(self, owner: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM rep_templates WHERE owner_user_id = ? ORDER BY created_at DESC", (owner,)).fetchall()

    def template(self, owner: str, key: str):
        return self.conn.execute("SELECT * FROM rep_templates WHERE owner_user_id = ? AND key = ? ORDER BY version DESC LIMIT 1", (owner, key)).fetchone()

    def insert_template(self, **v: Any) -> None:
        cols = ("id", "owner_user_id", "key", "name", "report_type", "version", "spec", "created_at")
        self.conn.execute(f"INSERT INTO rep_templates ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def bundle_state(self, bundle_id: str):
        return self.conn.execute("SELECT id, session_id, number, owner_user_id FROM evidence_bundles WHERE id = ?", (bundle_id,)).fetchone()

    def newer_bundle(self, session_id: str, number: int):
        return self.conn.execute("SELECT id, number FROM evidence_bundles WHERE session_id = ? AND number > ? ORDER BY number DESC LIMIT 1", (session_id, number)).fetchone()
