"""Search over the sources of one workspace (hybrid, keyword or vector) and the owner's aggregate usage view (counts only, never content)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence

from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...p7 import choice
from ...workspace.errors import invalid, not_found
from ..core.common import record_usage
from ..embeddings.provider import Embedder
from ..retrieval.search import Retriever
from ..retrieval.vectorstore import VectorStore, build_store


class SearchService:
    def __init__(self, ctx: Context, *, router_factory: Optional[Callable[[], Any]] = None, store: Optional[VectorStore] = None) -> None:
        self.ctx, self.conn, self.router_factory = ctx, ctx.conn, router_factory
        self.store = store or build_store(ctx)

    def search(self, principal: Principal, workspace_id: Any, query: Any, *, mode: Any = "hybrid", source_ids: Optional[Sequence[str]] = None, k: Any = 10) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("ri_search:user", principal.user_id)
        ws = self.conn.execute("SELECT id FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if ws is None:
            raise not_found("research workspace")
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise invalid("query", "Enter a search of up to 1000 characters.")
        mode = choice(mode, "mode", ("hybrid", "keyword", "semantic"))
        k = max(1, min(int(k) if isinstance(k, int) else 10, 30))
        from ..policy import external_allowed
        emb = Embedder(self.router_factory, principal.user_id, allow_external=external_allowed(self.conn, self.ctx.settings, principal.user_id, ws["id"]))
        out = Retriever(self.conn, self.store, emb).search(query.strip(), ws["id"], principal.user_id, only=source_ids, k=k, mode=mode)
        record_usage(self.ctx, principal.user_id, ws["id"], "retrieval", 1)
        titles = {r["id"]: r["title"] for r in self.conn.execute("SELECT id, title FROM ri_sources WHERE workspace_id = ? AND owner_user_id = ?", (ws["id"], principal.user_id)).fetchall()}
        items = []
        for h in out["hits"]:
            d = h.public()
            d["source_title"] = titles.get(h.source_id)
            items.append(d)
        return {"items": items, "info": out["info"]}


def owner_usage(ctx: Context) -> Dict[str, Any]:
    """Platform-owner view: aggregate counts only. No titles, text, questions or answers are ever returned."""
    c = ctx.conn
    one = lambda sql, *a: int(c.execute(sql, a).fetchone()[0])  # noqa: E731
    from ..core.common import day_start
    today = day_start(ctx)
    return {
        "accounts_with_sources": one("SELECT COUNT(DISTINCT owner_user_id) FROM ri_sources"),
        "workspaces_with_sources": one("SELECT COUNT(DISTINCT workspace_id) FROM ri_sources"),
        "sources": {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM ri_sources GROUP BY status")},
        "sources_by_format": {r["format"]: r["n"] for r in c.execute("SELECT format, COUNT(*) n FROM ri_sources GROUP BY format")},
        "chunks": one("SELECT COUNT(*) FROM ri_chunks"),
        "stored_bytes": one("SELECT COALESCE(SUM(byte_size),0) FROM ri_sources"),
        "embedding_models": {r["model"]: r["n"] for r in c.execute("SELECT model, COUNT(*) n FROM ri_embeddings GROUP BY model")},
        "jobs": {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM ri_jobs GROUP BY status")},
        "usage_today": {r["kind"]: r["u"] for r in c.execute("SELECT kind, SUM(units) u FROM ri_usage WHERE created_at >= ? GROUP BY kind", (today,))},
        "answers_by_mode": {r["mode"]: r["n"] for r in c.execute("SELECT mode, COUNT(*) n FROM ri_message_meta GROUP BY mode")},
        "external_answers": one("SELECT COUNT(*) FROM ri_message_meta WHERE external = 1"),
        "vector_backend": ctx.settings.ri_vector_backend,
        "malware_scanner": "clamav" if __import__("shutil").which("clamdscan") or __import__("shutil").which("clamscan") else "none",
    }
