"""Research assistant: ask questions of the sources in one workspace and get answers whose every statement points at a stored passage.

Pipeline: retrieve (hybrid) -> select context (per-source cap) -> answer (a provider when the workspace policy and the platform's routing allow it, otherwise a deterministic extractive
answer made of exact quotes) -> verify every sentence against the passages it cites -> store the message, its citations and an honest record of HOW it was answered.
There are three answer modes, always stated: `model` (a provider wrote it; unsupported sentences were removed), `extractive` (quotes only; no model involved) and
`insufficient_evidence` (nothing relevant enough was found, so nothing is asserted). Text inside sources is data: it is delimited, neutralised, and anything it tries to instruct can
neither reach the answer (unsupported sentences are dropped) nor trigger an action (the assistant has none)."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence

from ....db import transaction
from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...identity.tokens import new_id
from ...p7 import jload
from ...providers.base import Consent, build_messages, estimate_units
from ...workspace.errors import conflict, invalid, not_found
from ..citations.verify import LABEL, best_quote, verify_answer
from ..core.common import EV_ASK, EV_EXTERNAL, check_daily, record_usage
from ..embeddings.provider import Embedder
from ..ingestion.intelligence import tokens
from ..retrieval.search import Hit, Retriever, select_context
from ..retrieval.vectorstore import VectorStore, build_store

MAX_QUESTION = 2000
INSTRUCTION = ("Answer the question using only the sources below. Write short sentences. Put the source id in square brackets, like [S1], at the end of every sentence. "
               "If the sources do not contain the answer, reply exactly: INSUFFICIENT")
FOLLOW_UP = re.compile(r"\b(it|its|they|them|their|this|that|these|those|he|she|there|the same|above|former|latter|also|more|else|why|how about)\b|^(and|but|so|what about)\b", re.I)


class Assistant:
    def __init__(self, ctx: Context, *, router_factory: Optional[Callable[[], Any]] = None, store: Optional[VectorStore] = None) -> None:
        self.ctx, self.conn, self.router_factory = ctx, ctx.conn, router_factory
        self.store = store or build_store(ctx)

    # ------------------------------------------------------------------ conversations
    def _workspace(self, principal: Principal, workspace_id: Any, writable: bool = False) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        row = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if row is None:
            raise not_found("research workspace")
        if writable and row["status"] != "active":
            raise conflict("WORKSPACE_ARCHIVED", "This workspace is archived. Restore it to ask questions.")
        return row

    def _conv(self, principal: Principal, workspace_id: str, conversation_id: Any) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM rw_conversations WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (conversation_id if isinstance(conversation_id, str) else "", workspace_id, principal.user_id)).fetchone()
        if row is None:
            raise not_found("conversation")
        return row

    def create_conversation(self, principal: Principal, workspace_id: Any, title: Any = None) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id, True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        title = self._title(title) if title else "New conversation"
        cid, now = new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO rw_conversations (id, workspace_id, owner_user_id, title, created_at, updated_at) VALUES (?,?,?,?,?,?)", (cid, ws["id"], principal.user_id, title, now, now))
        return self._present_conv(self._conv(principal, ws["id"], cid))

    @staticmethod
    def _title(t: Any) -> str:
        if not isinstance(t, str) or not t.strip():
            raise invalid("title", "Enter a title.")
        return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", t)).strip()[:120]

    def _present_conv(self, r: sqlite3.Row) -> Dict[str, Any]:
        n = self.conn.execute("SELECT COUNT(*) FROM rw_messages WHERE conversation_id = ?", (r["id"],)).fetchone()[0]
        scope = {s["source_id"]: s["mode"] for s in self.conn.execute("SELECT source_id, mode FROM ri_conversation_scope WHERE conversation_id = ?", (r["id"],)).fetchall()}
        return {"id": r["id"], "workspace_id": r["workspace_id"], "title": r["title"], "messages": n, "scope": scope, "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def list_conversations(self, principal: Principal, workspace_id: Any) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id)
        rows = self.conn.execute("SELECT * FROM rw_conversations WHERE workspace_id = ? AND owner_user_id = ? ORDER BY updated_at DESC, id", (ws["id"], principal.user_id)).fetchall()
        return {"items": [self._present_conv(r) for r in rows]}

    def rename(self, principal: Principal, workspace_id: Any, conversation_id: Any, title: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id, True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        c = self._conv(principal, workspace_id, conversation_id)
        self.conn.execute("UPDATE rw_conversations SET title = ?, updated_at = ? WHERE id = ?", (self._title(title), self.ctx.clock.iso(), c["id"]))
        return self._present_conv(self._conv(principal, workspace_id, c["id"]))

    def delete_conversation(self, principal: Principal, workspace_id: Any, conversation_id: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        c = self._conv(principal, workspace_id, conversation_id)
        with transaction(self.conn):
            for table in ("ri_citations", "ri_message_meta", "ri_conversation_scope"):
                self.conn.execute(f"DELETE FROM {table} WHERE conversation_id = ? AND owner_user_id = ?", (c["id"], principal.user_id))
            self.conn.execute("DELETE FROM rw_messages WHERE conversation_id = ? AND owner_user_id = ?", (c["id"], principal.user_id))
            self.conn.execute("DELETE FROM rw_conversations WHERE id = ? AND owner_user_id = ?", (c["id"], principal.user_id))
        return {"deleted": True, "id": c["id"]}

    def set_scope(self, principal: Principal, workspace_id: Any, conversation_id: Any, source_id: Any, mode: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id, True)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        c = self._conv(principal, workspace_id, conversation_id)
        src = self.conn.execute("SELECT id FROM ri_sources WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (source_id if isinstance(source_id, str) else "", workspace_id, principal.user_id)).fetchone()
        if src is None:
            raise not_found("source")
        if mode in (None, "default"):
            self.conn.execute("DELETE FROM ri_conversation_scope WHERE conversation_id = ? AND source_id = ?", (c["id"], src["id"]))
        elif mode in ("include", "exclude"):
            self.conn.execute("INSERT OR REPLACE INTO ri_conversation_scope (conversation_id, source_id, owner_user_id, mode) VALUES (?,?,?,?)", (c["id"], src["id"], principal.user_id, mode))
        else:
            raise invalid("mode", "Use include, exclude or default.")
        return self._present_conv(c)

    def messages(self, principal: Principal, workspace_id: Any, conversation_id: Any) -> Dict[str, Any]:
        self._workspace(principal, workspace_id)
        c = self._conv(principal, workspace_id, conversation_id)
        rows = self.conn.execute("SELECT * FROM rw_messages WHERE conversation_id = ? AND owner_user_id = ? ORDER BY seq", (c["id"], principal.user_id)).fetchall()
        return {"conversation": self._present_conv(c), "messages": [self._present_message(r) for r in rows]}

    def _present_message(self, r: sqlite3.Row) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": r["id"], "role": r["role"], "body": r["body"], "created_at": r["created_at"]}
        if r["role"] == "assistant":
            meta = self.conn.execute("SELECT * FROM ri_message_meta WHERE message_id = ?", (r["id"],)).fetchone()
            cites = self.conn.execute("SELECT c.*, s.title AS source_title FROM ri_citations c JOIN ri_sources s ON s.id = c.source_id WHERE c.message_id = ? AND c.owner_user_id = ? ORDER BY c.label",
                                      (r["id"], r["owner_user_id"])).fetchall()
            out["citations"] = [{"id": c["id"], "label": c["label"], "source_id": c["source_id"], "source_title": c["source_title"], "chunk_id": c["chunk_id"], "page": c["page"],
                                 "locator": c["locator"], "quote": c["quote"], "verified": bool(c["verified"])} for c in cites]
            if meta:
                out["answer"] = {"mode": meta["mode"], "provider": meta["provider"], "model": meta["model"], "external": bool(meta["external"]), "removed_unsupported": meta["removed_unsupported"],
                                 "retrieval": jload(meta["retrieval"], {})}
        return out

    # ------------------------------------------------------------------ ask
    def ask(self, principal: Principal, workspace_id: Any, question: Any, *, conversation_id: Any = None, source_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        ws = self._workspace(principal, workspace_id, True)
        self.ctx.limiter.hit("ri_ask:user", principal.user_id)
        if not isinstance(question, str) or not question.strip():
            raise invalid("question", "Type a question.")
        question = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", question).strip()
        if len(question) > MAX_QUESTION:
            raise invalid("question", f"Keep the question under {MAX_QUESTION} characters.")
        check_daily(self.ctx, principal, "ai_request", "ai_requests_per_day", "You have reached today's limit of {limit} questions. It resets tomorrow, or activate Special access for a higher limit.")
        if conversation_id:
            conv = self._conv(principal, ws["id"], conversation_id)
        else:
            cid, now = new_id(), self.ctx.clock.iso()
            self.conn.execute("INSERT INTO rw_conversations (id, workspace_id, owner_user_id, title, created_at, updated_at) VALUES (?,?,?,?,?,?)", (cid, ws["id"], principal.user_id, self._title(question[:100]), now, now))
            conv = self._conv(principal, ws["id"], cid)
        from ..policy import external_allowed
        allow_external = external_allowed(self.conn, self.ctx.settings, principal.user_id, ws["id"])
        history = self.conn.execute("SELECT role, body FROM rw_messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT 6", (conv["id"],)).fetchall()[::-1]
        query = self._condense(question, history)
        scope = {r["source_id"]: r["mode"] for r in self.conn.execute("SELECT source_id, mode FROM ri_conversation_scope WHERE conversation_id = ?", (conv["id"],)).fetchall()}
        embedder = Embedder(self.router_factory, principal.user_id, allow_external=allow_external)
        found = Retriever(self.conn, self.store, embedder).search(query, ws["id"], principal.user_id, scope=scope, only=source_ids, k=16)
        record_usage(self.ctx, principal.user_id, ws["id"], "retrieval", 1)
        hits: List[Hit] = found["hits"]
        info = found["info"]
        context = select_context(hits)
        labels = {f"S{i + 1}": h for i, h in enumerate(context)}
        insufficient = (not context) or (max(h.coverage for h in context) < 0.25 and not info["semantic"])
        mode, provider, model, external, removed = "insufficient_evidence", None, None, False, 0
        body, used_labels = "", []
        kept: List[Any] = []
        if not insufficient:
            gen = self._generate(principal, question, history, labels, allow_external)
            if gen is not None:
                text, provider, model, external = gen
                v = verify_answer(text, {l: h.text for l, h in labels.items()})
                removed = v.removed
                if v.kept and "INSUFFICIENT" not in text.upper()[:30]:
                    mode, body, used_labels, kept = "model", v.text, v.labels, v.kept
            if mode != "model":
                body, used_labels, kept = self._extractive(query, labels)
                mode, provider, model, external = ("extractive", None, None, False) if body else ("insufficient_evidence", None, None, False)
        if mode == "insufficient_evidence":
            body = ("I could not find enough in the selected sources to answer this. "
                    + ("There are no ready sources to search yet." if info["sources_searched"] == 0 else f"I searched {info['sources_searched']} source(s) and found nothing relevant enough to quote. "
                                                                                                              "Try rephrasing, adding a source, or including a source you excluded."))
        now = self.ctx.clock.iso()
        uid, mid = new_id(), new_id()
        with transaction(self.conn):
            self.conn.execute("INSERT INTO rw_messages (id, conversation_id, workspace_id, owner_user_id, role, body, source_refs, created_at) VALUES (?,?,?,?,?,?,?,?)", (uid, conv["id"], ws["id"], principal.user_id, "user", question, "[]", now))
            refs = []
            cites = []
            for n, label in enumerate(used_labels, 1):
                h = labels[label]
                first = next((s for s, ls in kept if label in ls), body)
                quote = best_quote(h.text, first)
                if quote not in h.text:                                                      # safety net: a quote must always be an exact substring of the stored passage
                    quote = h.text[:300]
                cites.append((new_id(), mid, conv["id"], ws["id"], principal.user_id, h.source_id, h.chunk_id, str(n), h.page, h.locator, quote, 1, now))
                refs.append({"source_id": h.source_id, "chunk_id": h.chunk_id, "locator": h.locator, "label": str(n)})
            # renumber [S#] markers in the body to the stored citation numbers [n]
            numbering = {label: str(n) for n, label in enumerate(used_labels, 1)}
            body = LABEL.sub(lambda m: f"[{numbering[m.group(1)]}]" if m.group(1) in numbering else "", body)
            self.conn.execute("INSERT INTO rw_messages (id, conversation_id, workspace_id, owner_user_id, role, body, source_refs, created_at) VALUES (?,?,?,?,?,?,?,?)", (mid, conv["id"], ws["id"], principal.user_id, "assistant", body, json.dumps(refs), now))
            self.conn.executemany("INSERT INTO ri_citations (id, message_id, conversation_id, workspace_id, owner_user_id, source_id, chunk_id, label, page, locator, quote, verified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cites)
            retrieval = {"mode": info["mode"], "sources_searched": info["sources_searched"], "passages_considered": len(hits), "passages_used": len(context), "semantic": info["semantic"],
                         "vector_model": info["vector_model"], "notes": info["notes"], "flagged_passages": sum(1 for h in context if h.flagged)}
            self.conn.execute("INSERT INTO ri_message_meta (message_id, conversation_id, workspace_id, owner_user_id, mode, provider, model, external, removed_unsupported, retrieval, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (mid, conv["id"], ws["id"], principal.user_id, mode, provider, model, int(external), removed, json.dumps(retrieval), now))
            self.conn.execute("UPDATE rw_conversations SET updated_at = ? WHERE id = ?", (now, conv["id"]))
            record_usage(self.ctx, principal.user_id, ws["id"], "ai_request", 1, mode)
        self.ctx.audit.record(EV_ASK, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "conversation_id": conv["id"], "mode": mode, "passages": len(context), "removed_unsupported": removed})
        if external:
            self.ctx.audit.record(EV_EXTERNAL, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "provider": provider})
        row = self.conn.execute("SELECT * FROM rw_messages WHERE id = ?", (mid,)).fetchone()
        return {"conversation": self._present_conv(self._conv(principal, ws["id"], conv["id"])), "message": self._present_message(row)}

    # ------------------------------------------------------------------ pieces
    @staticmethod
    def _condense(question: str, history: Sequence[sqlite3.Row]) -> str:
        """Follow-up handling without a model: a short question, or one that leans on pronouns, borrows the previous user question's wording for retrieval only."""
        prior = [h["body"] for h in history if h["role"] == "user"]
        if prior and (len(tokens(question)) < 4 or FOLLOW_UP.search(question)):
            return f"{prior[-1][:300]} {question}"
        return question

    def _generate(self, principal: Principal, question: str, history: Sequence[sqlite3.Row], labels: Dict[str, Hit], allow_external: bool):
        if self.router_factory is None:
            return None
        try:
            router = self.router_factory()
            recent = "\n".join(f"{'Q' if h['role'] == 'user' else 'A'}: {h['body'][:300]}" for h in history[-4:])
            instruction = INSTRUCTION + (f"\n\nEarlier in this conversation (context only, not a source):\n{recent}" if recent else "")
            messages = build_messages(instruction, [(l, h.text) for l, h in labels.items()], question)
            out = router.chat(principal.user_id, None, "synthesize", messages, data_class="private", consent=Consent(external=allow_external), max_tokens=700, temperature=0.0)
        except Exception:                                                                    # noqa: BLE001 - a provider fault means the extractive path, never an error page
            return None
        if not out.ok or not out.text:
            return None
        info = next((t for t in out.trace if t.get("outcome") == "ok"), {})
        ext = bool(out.provider and self._is_external(out.provider))
        return out.text, out.provider, out.model, ext

    def _is_external(self, key: str) -> bool:
        try:
            row = self.conn.execute("SELECT privacy_class FROM ai_providers WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return False
        return bool(row and row["privacy_class"] == "external")

    @staticmethod
    def _extractive(query: str, labels: Dict[str, Hit]):
        want = set(tokens(query))
        picks = []
        for label, h in labels.items():
            best, score = None, 0.0
            for s in re.split(r"(?<=[.!?])\s+", h.text.replace("\n", " ")):
                s = s.strip()
                if len(s) < 30 or len(s) > 400:
                    continue
                overlap = len(want & set(tokens(s))) / (len(want) or 1)
                if overlap > score:
                    best, score = s, overlap
            if best and score >= 0.2:
                picks.append((score, label, best))
        picks.sort(key=lambda t: -t[0])
        picks = picks[:4]
        if not picks:
            return "", [], []
        lines = [f"“{s}” [{l}]" for _, l, s in picks]
        header = "Here is what the sources say. These are direct quotes; no AI model wrote this answer."
        used = [l for _, l, _ in picks]
        return header + " " + " ".join(lines), used, [(f"“{s}” [{l}]", [l]) for _, l, s in picks]
