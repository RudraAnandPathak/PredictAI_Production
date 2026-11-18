"""Conversations hub: one list and one way to talk to PredictAI, over the two assistants that already exist.

* kind `data`     - a conversation about one dataset (the Analytics assistant: validated plans, computed and verified figures).
* kind `research` - a conversation about one research workspace (the Research Intelligence assistant: answers whose sentences cite stored passages).

The hub stores nothing of its own. It reads both conversation tables for the signed-in person only, and every action (ask, rename, delete) is delegated to the
assistant that owns the conversation, so ownership checks, rate limits, daily AI limits, verification and citations are the ones those assistants already enforce.
Deleting a dataset or a workspace still removes its conversations through their existing purge paths."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...core.errors import AppError
from ..analytics.assistant_service import AssistantService
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.context import Context
from ..research_intelligence.reasoning.assistant import Assistant
from ..workspace.errors import invalid, not_found

KINDS = ("data", "research")
MAX_LIST = 200
MAX_Q = 100
_LIKE_ESCAPE = re.compile(r"([\\%_])")


def _like(q: str) -> str:
    return "%" + _LIKE_ESCAPE.sub(r"\\\1", q) + "%"


class ConversationHub:
    def __init__(self, ctx: Context, data: AssistantService, research: Assistant) -> None:
        self.ctx, self.conn, self.data, self.research = ctx, ctx.conn, data, research

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _kind(kind: Any) -> str:
        if kind not in KINDS:
            raise invalid("kind", "Use data or research.")
        return kind

    @staticmethod
    def _id(value: Any) -> str:
        return value if isinstance(value, str) and 0 < len(value) <= 80 else ""

    def _row(self, principal: Principal, kind: str, conversation_id: Any):
        cid = self._id(conversation_id)
        table = "conversations" if kind == "data" else "rw_conversations"
        row = self.conn.execute(f"SELECT * FROM {table} WHERE id = ? AND owner_user_id = ?", (cid, principal.user_id)).fetchone()
        if row is None:
            raise not_found("conversation")
        return row

    # ------------------------------------------------------------------ what can be talked to
    def targets(self, principal: Principal) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        datasets = self.conn.execute(
            "SELECT a.id, a.name, a.row_count, p.name AS project FROM assets a JOIN projects p ON p.id = a.project_id "
            "WHERE a.owner_user_id = ? AND a.kind = 'dataset' AND a.status = 'ready' AND p.status = 'active' ORDER BY a.updated_at DESC LIMIT ?",
            (principal.user_id, MAX_LIST)).fetchall()
        spaces = self.conn.execute("SELECT id, name, status FROM rw_workspaces WHERE owner_user_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT ?",
                                   (principal.user_id, MAX_LIST)).fetchall()
        return {"datasets": [{"id": r["id"], "name": r["name"], "rows": r["row_count"], "project": r["project"]} for r in datasets],
                "workspaces": [{"id": r["id"], "name": r["name"]} for r in spaces]}

    # ------------------------------------------------------------------ list and search
    def list(self, principal: Principal, q: Any = None, kind: Any = None, limit: Any = 50) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        if kind not in (None, ""):
            self._kind(kind)
        if q is not None and not isinstance(q, str):
            raise invalid("q", "Search text must be text.")
        q = re.sub(r"[\x00-\x1f\x7f]", " ", q or "").strip()[:MAX_Q]
        try:
            limit = max(1, min(int(limit), MAX_LIST))
        except (TypeError, ValueError):
            raise invalid("limit", "Use a number from 1 to 200.")
        items: List[Dict[str, Any]] = []
        like = _like(q) if q else None
        if kind in (None, "", "data"):
            sql = ("SELECT c.id, c.title, c.created_at, c.updated_at, c.asset_id AS target_id, a.name AS target, "
                   "(SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS n, "
                   "(SELECT content FROM conversation_messages m WHERE m.conversation_id = c.id ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) AS last "
                   "FROM conversations c JOIN assets a ON a.id = c.asset_id WHERE c.owner_user_id = ? AND a.status != 'deleting'")
            args: List[Any] = [principal.user_id]
            if like:
                sql += (" AND (c.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM conversation_messages m WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'))")
                args += [like, like]
            sql += " ORDER BY c.updated_at DESC LIMIT ?"
            for r in self.conn.execute(sql, args + [limit]).fetchall():
                items.append(self._item("data", r))
        if kind in (None, "", "research"):
            sql = ("SELECT c.id, c.title, c.created_at, c.updated_at, c.workspace_id AS target_id, w.name AS target, "
                   "(SELECT COUNT(*) FROM rw_messages m WHERE m.conversation_id = c.id) AS n, "
                   "(SELECT body FROM rw_messages m WHERE m.conversation_id = c.id ORDER BY m.seq DESC LIMIT 1) AS last "
                   "FROM rw_conversations c JOIN rw_workspaces w ON w.id = c.workspace_id WHERE c.owner_user_id = ?")
            args = [principal.user_id]
            if like:
                sql += (" AND (c.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM rw_messages m WHERE m.conversation_id = c.id AND m.body LIKE ? ESCAPE '\\'))")
                args += [like, like]
            sql += " ORDER BY c.updated_at DESC LIMIT ?"
            for r in self.conn.execute(sql, args + [limit]).fetchall():
                items.append(self._item("research", r))
        items.sort(key=lambda i: i["updated_at"], reverse=True)
        return {"items": items[:limit], "query": q or None}

    @staticmethod
    def _item(kind: str, r) -> Dict[str, Any]:
        last = (r["last"] or "").strip()
        return {"kind": kind, "id": r["id"], "title": r["title"], "target_id": r["target_id"], "target": r["target"], "messages": r["n"],
                "preview": (last[:140] + "…") if len(last) > 140 else last, "created_at": r["created_at"], "updated_at": r["updated_at"]}

    # ------------------------------------------------------------------ one conversation
    def get(self, principal: Principal, kind: Any, conversation_id: Any) -> Dict[str, Any]:
        kind = self._kind(kind)
        row = self._row(principal, kind, conversation_id)
        if kind == "data":
            full = self.data.conversation(principal, row["id"])
            msgs = [{"id": m["id"], "role": m["role"], "text": m["content"], "created_at": m["created_at"], "run_id": m["run_id"], "details": m["payload"]} for m in full["messages"]]
            target = {"id": row["asset_id"], "type": "dataset"}
        else:
            full = self.research.messages(principal, row["workspace_id"], row["id"])
            msgs = [{"id": m["id"], "role": m["role"], "text": m["body"], "created_at": m["created_at"], "citations": m.get("citations", []), "answer": m.get("answer")}
                    for m in full["messages"]]
            target = {"id": row["workspace_id"], "type": "workspace"}
        return {"kind": kind, "id": row["id"], "title": row["title"], "target": target, "created_at": row["created_at"], "messages": msgs}

    def create(self, principal: Principal, kind: Any, target_id: Any, title: Any = None) -> Dict[str, Any]:
        kind = self._kind(kind)
        if kind == "data":
            made = self.data.create_conversation(principal, self._id(target_id), title)
        else:
            made = self.research.create_conversation(principal, self._id(target_id), title)
        return self.get(principal, kind, made["id"])

    def send(self, principal: Principal, kind: Any, conversation_id: Any, text: Any, *, source_ids: Optional[List[str]] = None, use_ai: bool = False) -> Dict[str, Any]:
        kind = self._kind(kind)
        row = self._row(principal, kind, conversation_id)
        if kind == "data":
            result = self.data.ask(principal, row["asset_id"], text, conversation_id=row["id"], use_ai=use_ai)
        else:
            result = self.research.ask(principal, row["workspace_id"], text, conversation_id=row["id"], source_ids=source_ids)
        return {"result": result, "conversation": self.get(principal, kind, row["id"])}

    def rename(self, principal: Principal, kind: Any, conversation_id: Any, title: Any) -> Dict[str, Any]:
        kind = self._kind(kind)
        row = self._row(principal, kind, conversation_id)
        if kind == "data":
            self.data.rename_conversation(principal, row["id"], title)
        else:
            self.research.rename(principal, row["workspace_id"], row["id"], title)
        return self.get(principal, kind, row["id"])

    def delete(self, principal: Principal, kind: Any, conversation_id: Any) -> Dict[str, Any]:
        kind = self._kind(kind)
        row = self._row(principal, kind, conversation_id)
        if kind == "data":
            return self.data.delete_conversation(principal, row["id"])
        return self.research.delete_conversation(principal, row["workspace_id"], row["id"])

    # ------------------------------------------------------------------ export
    def export_markdown(self, principal: Principal, kind: Any, conversation_id: Any) -> Dict[str, Any]:
        conv = self.get(principal, kind, conversation_id)
        lines = [f"# {conv['title']}", "", f"Conversation type: {'about a dataset' if conv['kind'] == 'data' else 'about a research workspace'}. Exported {self.ctx.clock.iso()}.", ""]
        notes: List[str] = []
        for m in conv["messages"]:
            lines += [f"**{'You' if m['role'] == 'user' else 'PredictAI'}** · {m['created_at']}", "", m["text"], ""]
            for c in m.get("citations") or []:
                quote = (c.get("quote") or "").strip().replace("\n", " ")
                notes.append(f"- [{c.get('label')}] {c.get('source_title')}{', page ' + str(c['page']) if c.get('page') else ''}: “{quote}”" + ("" if c.get("verified") else " (not verified)"))
        if notes:
            lines += ["## Sources cited", ""] + notes + [""]
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", conv["title"]).strip("_")[:60] or "conversation"
        return {"filename": f"{name}.md", "content": "\n".join(lines)}
