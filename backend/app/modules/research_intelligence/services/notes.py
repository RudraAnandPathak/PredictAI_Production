"""Notes (Phase 8 `rw_notes`) and findings (`ri_findings`: a saved passage, highlight or piece of evidence with the chunk it came from)."""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, Optional

from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...identity.tokens import new_id
from ...p7 import choice
from ...workspace.errors import invalid, not_found


def _text(v: Any, field: str, maximum: int, required: bool = True) -> Optional[str]:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise invalid(field, "This is required.")
        return None
    if not isinstance(v, str) or len(v.strip()) > maximum:
        raise invalid(field, f"Use at most {maximum} characters.")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v.strip())


class NotesService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    def _ws(self, principal: Principal, workspace_id: Any, writable: bool = False) -> str:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self.conn.execute("SELECT id, status FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("research workspace")
        if writable:
            self.ctx.limiter.hit("ri_write:user", principal.user_id)
        return r["id"]

    def _src_ok(self, principal: Principal, workspace_id: str, source_id: Any) -> Optional[str]:
        if source_id in (None, ""):
            return None
        r = self.conn.execute("SELECT id FROM ri_sources WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (source_id if isinstance(source_id, str) else "", workspace_id, principal.user_id)).fetchone()
        if r is None:
            raise not_found("source")
        return r["id"]

    # notes
    def _note(self, r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "source_id": r["source_id"], "body": r["body"], "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def list_notes(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        rows = self.conn.execute("SELECT * FROM rw_notes WHERE workspace_id = ? AND owner_user_id = ? ORDER BY created_at DESC, id", (ws, principal.user_id)).fetchall()
        return {"items": [self._note(r) for r in rows]}

    def create_note(self, principal: Principal, workspace_id: Any, body: Any, source_id: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        text, sid, nid, now = _text(body, "body", 20000), self._src_ok(principal, ws, source_id), new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO rw_notes (id, workspace_id, owner_user_id, source_id, body, created_at, updated_at) VALUES (?,?,?,?,?,?,?)", (nid, ws, principal.user_id, sid, text, now, now))
        return self._note(self.conn.execute("SELECT * FROM rw_notes WHERE id = ?", (nid,)).fetchone())

    def _own_note(self, principal: Principal, ws: str, note_id: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM rw_notes WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (note_id if isinstance(note_id, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("note")
        return r

    def update_note(self, principal: Principal, workspace_id: Any, note_id: Any, body: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        n = self._own_note(principal, ws, note_id)
        self.conn.execute("UPDATE rw_notes SET body = ?, updated_at = ? WHERE id = ?", (_text(body, "body", 20000), self.ctx.clock.iso(), n["id"]))
        return self._note(self._own_note(principal, ws, n["id"]))

    def delete_note(self, principal: Principal, workspace_id: Any, note_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        n = self._own_note(principal, ws, note_id)
        self.conn.execute("DELETE FROM rw_notes WHERE id = ?", (n["id"],))
        return {"deleted": True, "id": n["id"]}

    # findings
    def _finding(self, r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "source_id": r["source_id"], "chunk_id": r["chunk_id"], "kind": r["kind"], "text": r["text"], "comment": r["comment"], "created_at": r["created_at"]}

    def list_findings(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        rows = self.conn.execute("SELECT * FROM ri_findings WHERE workspace_id = ? AND owner_user_id = ? ORDER BY created_at DESC, id", (ws, principal.user_id)).fetchall()
        return {"items": [self._finding(r) for r in rows]}

    def create_finding(self, principal: Principal, workspace_id: Any, source_id: Any, text: Any = None, *, chunk_id: Any = None, kind: Any = "finding", comment: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        sid = self._src_ok(principal, ws, source_id)
        if sid is None:
            raise invalid("source_id", "Choose the source this finding comes from.")
        kind = choice(kind, "kind", ("finding", "highlight", "evidence"))
        cid = None
        if chunk_id:
            c = self.conn.execute("SELECT id, text FROM ri_chunks WHERE id = ? AND source_id = ? AND owner_user_id = ?", (chunk_id if isinstance(chunk_id, str) else "", sid, principal.user_id)).fetchone()
            if c is None:
                raise not_found("passage")
            cid = c["id"]
            if text in (None, ""):
                text = c["text"][:4000]
        fid, now = new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_findings (id, workspace_id, owner_user_id, source_id, chunk_id, kind, text, comment, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (fid, ws, principal.user_id, sid, cid, kind, _text(text, "text", 4000), _text(comment, "comment", 2000, False), now))
        return self._finding(self.conn.execute("SELECT * FROM ri_findings WHERE id = ?", (fid,)).fetchone())

    def delete_finding(self, principal: Principal, workspace_id: Any, finding_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self.conn.execute("SELECT id FROM ri_findings WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (finding_id if isinstance(finding_id, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("finding")
        self.conn.execute("DELETE FROM ri_finding_links WHERE from_id = ? OR to_id = ?", (r["id"], r["id"]))
        self.conn.execute("DELETE FROM ri_finding_group_items WHERE finding_id = ?", (r["id"],))
        self.conn.execute("DELETE FROM ri_findings WHERE id = ?", (r["id"],))
        return {"deleted": True, "id": r["id"]}
