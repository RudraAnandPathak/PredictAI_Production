"""Search and page cache. Per person and project (so deleting a project deletes it), with an expiry, a size cap and a recorded invalidation reason.
A cached answer is never treated as current: callers receive `fetched_at` and `cache_hit` and show them."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta
from typing import Any, Dict, Optional

from ...core.clock import Clock, from_iso, to_iso
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from ..workspace import keys


def cache_key(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def normalise_query(query: str) -> str:
    return " ".join(query.lower().split())


class WebCache:
    def __init__(self, conn: sqlite3.Connection, storage: StorageBackend, clock: Clock, *, ttl_hours: int, max_bytes: int) -> None:
        self.conn, self.storage, self.clock, self.ttl_hours, self.max_bytes = conn, storage, clock, ttl_hours, max_bytes

    def get(self, owner: str, project: str, kind: str, key: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM web_cache WHERE project_id = ? AND owner_user_id = ? AND cache_key = ? AND kind = ?", (project, owner, key, kind)).fetchone()
        if row is None:
            return None
        now = self.clock.now()
        if from_iso(row["expires_at"]) <= now:
            self._drop(row, "expired")
            return None
        try:
            body = json.loads(self.storage.read_bytes(row["body_key"]).decode("utf-8"))
        except (StorageError, ValueError):
            self._drop(row, "unreadable")
            return None
        if hashlib.sha256(json.dumps(body["payload"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != row["content_hash"]:
            self._drop(row, "integrity")
            return None
        self.conn.execute("UPDATE web_cache SET hits = hits + 1 WHERE id = ?", (row["id"],))
        return {"payload": body["payload"], "fetched_at": row["fetched_at"], "expires_at": row["expires_at"], "hits": row["hits"] + 1}

    def put(self, owner: str, project: str, kind: str, key: str, provider: str, label: str, payload: Dict[str, Any], status_code: Optional[str] = None) -> str:
        now = self.clock.now()
        old = self.conn.execute("SELECT * FROM web_cache WHERE project_id = ? AND owner_user_id = ? AND cache_key = ?", (project, owner, key)).fetchone()
        if old is not None:
            self._drop(old, "replaced")
        blob = json.dumps({"payload": payload}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(blob) > 3_000_000:
            return ""
        cid = new_id()
        bkey = keys.web_cache_key(project, cid)
        self.storage.put(bkey, [blob], overwrite=True)
        content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        self.conn.execute("INSERT INTO web_cache (id, project_id, owner_user_id, kind, cache_key, provider, label, body_key, content_hash, status_code, size_bytes, fetched_at, expires_at, hits) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                          (cid, project, owner, kind, key, provider, label[:300], bkey, content_hash, status_code, len(blob), to_iso(now), to_iso(now + timedelta(hours=self.ttl_hours))))
        self._enforce_cap(owner, project)
        return cid

    def _drop(self, row: sqlite3.Row, reason: str) -> None:
        try:
            self.storage.delete(row["body_key"])
        except StorageError:
            pass
        self.conn.execute("DELETE FROM web_cache WHERE id = ?", (row["id"],))

    def _enforce_cap(self, owner: str, project: str) -> None:
        total = self.conn.execute("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM web_cache WHERE project_id = ? AND owner_user_id = ?", (project, owner)).fetchone()["n"]
        if total <= self.max_bytes:
            return
        for row in self.conn.execute("SELECT * FROM web_cache WHERE project_id = ? AND owner_user_id = ? ORDER BY fetched_at", (project, owner)).fetchall():
            self._drop(row, "size_cap")
            total -= row["size_bytes"]
            if total <= self.max_bytes * 0.8:
                break

    def purge_expired(self, owner: str, project: str) -> int:
        n = 0
        for row in self.conn.execute("SELECT * FROM web_cache WHERE project_id = ? AND owner_user_id = ? AND expires_at <= ?", (project, owner, to_iso(self.clock.now()))).fetchall():
            self._drop(row, "expired")
            n += 1
        return n

    def purge_all_expired(self, limit: int = 500) -> int:
        """Housekeeping across every person and project: expired entries and their stored bodies go."""
        rows = self.conn.execute("SELECT * FROM web_cache WHERE expires_at <= ? LIMIT ?", (to_iso(self.clock.now()), limit)).fetchall()
        for row in rows:
            self._drop(row, "expired")
        return len(rows)

    def clear(self, owner: str, project: str) -> int:
        rows = self.conn.execute("SELECT * FROM web_cache WHERE project_id = ? AND owner_user_id = ?", (project, owner)).fetchall()
        for row in rows:
            self._drop(row, "cleared")
        return len(rows)

    def stats(self, owner: str, project: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b, COALESCE(SUM(hits), 0) AS h, MIN(fetched_at) AS oldest, MAX(expires_at) AS latest FROM web_cache WHERE project_id = ? AND owner_user_id = ?",
                              (project, owner)).fetchone()
        return {"entries": r["n"], "bytes": r["b"], "hits": r["h"], "oldest": r["oldest"], "latest_expiry": r["latest"], "limit_bytes": self.max_bytes, "lifetime_hours": self.ttl_hours}
