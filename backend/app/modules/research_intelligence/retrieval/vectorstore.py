"""Vector storage behind one interface. SQLite (default) keeps vectors in ri_embeddings and scores them with numpy (exact cosine, adequate for tens of thousands of chunks). Qdrant is an
optional adapter over its REST API; it is UNVERIFIED against a live server in this delivery (tested only against a local fake that follows the documented request shapes)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..embeddings.provider import cosine_top

Item = Dict[str, Any]       # chunk_seq, source_id, workspace_id, owner_user_id, model, provider, vector


class VectorStore:
    name = ""

    def upsert(self, items: Sequence[Item]) -> None:                       # pragma: no cover - interface
        raise NotImplementedError

    def search(self, *, workspace_id: str, owner_id: str, model: str, vector: Sequence[float], k: int, source_ids: Optional[Sequence[str]]) -> List[Tuple[int, float]]:   # pragma: no cover
        raise NotImplementedError

    def delete_source(self, *, workspace_id: str, owner_id: str, source_id: str) -> None:               # pragma: no cover
        raise NotImplementedError

    def delete_workspace(self, *, workspace_id: str, owner_id: str) -> None:                            # pragma: no cover
        raise NotImplementedError

    def models(self, *, workspace_id: str, owner_id: str) -> List[str]:                                 # pragma: no cover
        raise NotImplementedError


class SqliteVectorStore(VectorStore):
    name = "sqlite"

    def __init__(self, conn: sqlite3.Connection, clock_iso: Callable[[], str]) -> None:
        self.conn, self.iso = conn, clock_iso

    def upsert(self, items: Sequence[Item]) -> None:
        now = self.iso()
        self.conn.executemany(
            "INSERT OR REPLACE INTO ri_embeddings (chunk_seq, model, provider, workspace_id, source_id, owner_user_id, dims, vector, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(i["chunk_seq"], i["model"], i["provider"], i["workspace_id"], i["source_id"], i["owner_user_id"], len(i["vector"]), np.asarray(i["vector"], dtype=np.float32).tobytes(), now) for i in items])

    def search(self, *, workspace_id, owner_id, model, vector, k, source_ids):
        sql = "SELECT chunk_seq, dims, vector FROM ri_embeddings WHERE workspace_id = ? AND owner_user_id = ? AND model = ?"
        args: List[Any] = [workspace_id, owner_id, model]
        if source_ids is not None:
            if not source_ids:
                return []
            sql += f" AND source_id IN ({','.join('?' * len(source_ids))})"
            args += list(source_ids)
        rows = self.conn.execute(sql, args).fetchall()
        q = np.asarray(vector, dtype=np.float32)
        rows = [r for r in rows if r["dims"] == len(q)]
        if not rows:
            return []
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        return [(int(rows[i]["chunk_seq"]), s) for i, s in cosine_top(matrix, q, k)]

    def delete_source(self, *, workspace_id, owner_id, source_id):
        self.conn.execute("DELETE FROM ri_embeddings WHERE source_id = ? AND workspace_id = ? AND owner_user_id = ?", (source_id, workspace_id, owner_id))

    def delete_workspace(self, *, workspace_id, owner_id):
        self.conn.execute("DELETE FROM ri_embeddings WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id))

    def models(self, *, workspace_id, owner_id):
        return [r[0] for r in self.conn.execute("SELECT DISTINCT model FROM ri_embeddings WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).fetchall()]


HttpJson = Callable[[str, str, Optional[dict], Dict[str, str], float], Tuple[int, Any]]


def _urllib_json(method: str, url: str, body: Optional[dict], headers: Dict[str, str], timeout: float) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                   # noqa: S310 - the URL is operator configuration (PREDICTAI_RI_QDRANT_URL), never user input
            raw = r.read(20_000_000)
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(1_000_000) or b"null")
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError("QDRANT_UNREACHABLE") from e


class QdrantVectorStore(VectorStore):
    """REST adapter. One collection per embedding model (`<base>__<model-slug>`), point id = chunk_seq, payload carries workspace/owner/source for filtering. Every query filters by
    workspace_id AND owner_user_id. UNVERIFIED against a live Qdrant."""
    name = "qdrant"

    def __init__(self, base_url: str, collection: str, *, ledger: "SqliteVectorStore", api_key: Optional[str] = None, http: HttpJson = _urllib_json, timeout: float = 20.0) -> None:
        # `ledger` is the SQLite side: it records WHICH chunks/models were sent (marker rows with an empty vector), so deletion and reindex know what to remove across requests.
        self.base, self.collection, self.http, self.timeout, self.ledger = base_url.rstrip("/"), collection, http, timeout, ledger
        self.headers = {"api-key": api_key} if api_key else {}
        self._known: set = set()

    def _coll(self, model: str) -> str:
        return f"{self.collection}__{re.sub(r'[^A-Za-z0-9_-]+', '_', model)[:60]}"

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        status, data = self.http(method, f"{self.base}{path}", body, self.headers, self.timeout)
        if status >= 300 and not (status == 404 and method == "POST"):
            raise RuntimeError(f"QDRANT_HTTP_{status}")
        return data

    def _ensure(self, model: str, dims: int) -> str:
        name = self._coll(model)
        if name not in self._known:
            status, _ = self.http("GET", f"{self.base}/collections/{name}", None, self.headers, self.timeout)
            if status == 404:
                self._call("PUT", f"/collections/{name}", {"vectors": {"size": dims, "distance": "Cosine"}})
            elif status >= 300:
                raise RuntimeError(f"QDRANT_HTTP_{status}")
            self._known.add(name)
        return name

    @staticmethod
    def _flt(workspace_id: str, owner_id: str, source_id: Optional[str] = None, source_ids: Optional[Sequence[str]] = None) -> dict:
        must = [{"key": "workspace_id", "match": {"value": workspace_id}}, {"key": "owner_user_id", "match": {"value": owner_id}}]
        if source_id:
            must.append({"key": "source_id", "match": {"value": source_id}})
        if source_ids is not None:
            must.append({"key": "source_id", "match": {"any": list(source_ids)}})
        return {"must": must}

    def upsert(self, items: Sequence[Item]) -> None:
        by_model: Dict[str, List[Item]] = {}
        for i in items:
            by_model.setdefault(i["model"], []).append(i)
        for model, group in by_model.items():
            name = self._ensure(model, len(group[0]["vector"]))
            for start in range(0, len(group), 64):
                pts = [{"id": g["chunk_seq"], "vector": [float(x) for x in g["vector"]],
                        "payload": {"workspace_id": g["workspace_id"], "owner_user_id": g["owner_user_id"], "source_id": g["source_id"]}} for g in group[start:start + 64]]
                self._call("PUT", f"/collections/{name}/points?wait=true", {"points": pts})
        self.ledger.upsert([{**i, "vector": []} for i in items])

    def search(self, *, workspace_id, owner_id, model, vector, k, source_ids):
        if source_ids is not None and not source_ids:
            return []
        name = self._coll(model)
        data = self._call("POST", f"/collections/{name}/points/search", {"vector": [float(x) for x in vector], "limit": int(k), "with_payload": False,
                                                                       "filter": self._flt(workspace_id, owner_id, source_ids=source_ids)})
        return [(int(r["id"]), float(r["score"])) for r in ((data or {}).get("result") or [])]

    def _delete(self, flt: dict, workspace_id: str, owner_id: str) -> None:
        for model in self.ledger.models(workspace_id=workspace_id, owner_id=owner_id):
            self._call("POST", f"/collections/{self._coll(model)}/points/delete?wait=true", {"filter": flt})

    def delete_source(self, *, workspace_id, owner_id, source_id):
        self._delete(self._flt(workspace_id, owner_id, source_id=source_id), workspace_id, owner_id)
        self.ledger.delete_source(workspace_id=workspace_id, owner_id=owner_id, source_id=source_id)

    def delete_workspace(self, *, workspace_id, owner_id):
        self._delete(self._flt(workspace_id, owner_id), workspace_id, owner_id)
        self.ledger.delete_workspace(workspace_id=workspace_id, owner_id=owner_id)

    def models(self, *, workspace_id, owner_id):
        return sorted(self.ledger.models(workspace_id=workspace_id, owner_id=owner_id))


def build_store(ctx: Any) -> VectorStore:
    return build_store_for(ctx.settings, ctx.conn, ctx.clock.iso)


def build_store_for(s: Any, conn: sqlite3.Connection, iso: Callable[[], str]) -> VectorStore:
    local = SqliteVectorStore(conn, iso)
    if s.ri_vector_backend == "qdrant" and s.ri_qdrant_url:
        return QdrantVectorStore(s.ri_qdrant_url, s.ri_qdrant_collection, ledger=local, api_key=os.environ.get("PREDICTAI_RI_QDRANT_API_KEY") or None)
    return local
