"""SQL for news sources, articles, events and influence analyses. Every read filters by the owner; nothing here decides what a person may do (the service does)."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SOURCE_COLUMNS = {"name", "publisher", "language", "policy", "status", "last_fetched_at", "last_status", "last_error_code", "article_count", "updated_at"}
ARTICLE_COLUMNS = {"status", "quarantine_reason", "duplicate_of", "derived", "derived_version", "event_id", "expires_at", "title", "text_key"}


class WorldRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------ sources
    def create_source(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "name", "feed_url", "publisher", "language", "policy", "status", "article_count", "created_at", "updated_at")
        row = {c: v.get(c) for c in cols}
        row["status"], row["article_count"] = row["status"] or "active", row["article_count"] or 0
        self.conn.execute(f"INSERT INTO news_sources ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])

    def source(self, owner: str, source_id: str):
        return self.conn.execute("SELECT * FROM news_sources WHERE id = ? AND owner_user_id = ?", (source_id, owner)).fetchone()

    def sources(self, owner: str, project_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM news_sources WHERE owner_user_id = ? AND project_id = ? ORDER BY name COLLATE NOCASE, id", (owner, project_id)).fetchall()

    def count_sources(self, owner: str, project_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM news_sources WHERE owner_user_id = ? AND project_id = ?", (owner, project_id)).fetchone()[0]

    def source_by_url(self, owner: str, project_id: str, url: str):
        return self.conn.execute("SELECT * FROM news_sources WHERE owner_user_id = ? AND project_id = ? AND feed_url = ?", (owner, project_id, url)).fetchone()

    def update_source(self, source_id: str, **fields: Any) -> None:
        bad = set(fields) - SOURCE_COLUMNS
        if bad:
            raise ValueError(f"unknown source columns {sorted(bad)}")
        if fields:
            self.conn.execute(f"UPDATE news_sources SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), source_id])

    def delete_source(self, source_id: str) -> None:
        self.conn.execute("DELETE FROM news_articles WHERE source_id = ?", (source_id,))
        self.conn.execute("DELETE FROM news_sources WHERE id = ?", (source_id,))

    # ------------------------------------------------------------------ articles
    def insert_article(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "source_id", "url", "guid_hash", "title", "publisher", "domain", "author", "published_at", "retrieved_at", "language", "content_hash", "text_key",
                "status", "quarantine_reason", "duplicate_of", "derived", "derived_version", "event_id", "expires_at")
        self.conn.execute(f"INSERT INTO news_articles ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def article(self, owner: str, article_id: str):
        return self.conn.execute("SELECT * FROM news_articles WHERE id = ? AND owner_user_id = ?", (article_id, owner)).fetchone()

    def update_article(self, article_id: str, **fields: Any) -> None:
        bad = set(fields) - ARTICLE_COLUMNS
        if bad:
            raise ValueError(f"unknown article columns {sorted(bad)}")
        if fields:
            self.conn.execute(f"UPDATE news_articles SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), article_id])

    def delete_article(self, article_id: str) -> None:
        self.conn.execute("DELETE FROM news_articles WHERE id = ?", (article_id,))

    def known_guids(self, owner: str, project_id: str, hashes: Sequence[str]) -> set:
        found: set = set()
        for i in range(0, len(hashes), 500):
            chunk = list(hashes[i:i + 500])
            q = ",".join("?" for _ in chunk)
            found |= {r[0] for r in self.conn.execute(f"SELECT guid_hash FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND guid_hash IN ({q})", [owner, project_id, *chunk])}
        return found

    def first_with_content(self, owner: str, project_id: str, content_hash: str):
        return self.conn.execute("SELECT id FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND content_hash = ? AND duplicate_of IS NULL ORDER BY retrieved_at, id LIMIT 1",
                                 (owner, project_id, content_hash)).fetchone()

    def articles(self, owner: str, project_id: str, *, status: Optional[str] = None, source_id: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                 event_id: Optional[str] = None, q: Optional[str] = None, include_duplicates: bool = False, limit: int = 50, offset: int = 0) -> Tuple[List[sqlite3.Row], int]:
        where, args = ["owner_user_id = ?", "project_id = ?"], [owner, project_id]
        if status:
            where.append("status = ?")
            args.append(status)
        else:
            where.append("status IN ('active', 'quarantined')")
        if source_id:
            where.append("source_id = ?")
            args.append(source_id)
        if since:
            where.append("COALESCE(published_at, retrieved_at) >= ?")
            args.append(since)
        if until:
            where.append("COALESCE(published_at, retrieved_at) <= ?")
            args.append(until)
        if event_id:
            where.append("event_id = ?")
            args.append(event_id)
        if q:
            where.append("title LIKE ? ESCAPE '\\'")
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if not include_duplicates:
            where.append("duplicate_of IS NULL")
        clause = " AND ".join(where)
        total = self.conn.execute(f"SELECT COUNT(*) FROM news_articles WHERE {clause}", args).fetchone()[0]
        rows = self.conn.execute(f"SELECT * FROM news_articles WHERE {clause} ORDER BY COALESCE(published_at, retrieved_at) DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return rows, total

    def active_for_analysis(self, owner: str, project_id: str, since: Optional[str], limit: int) -> List[sqlite3.Row]:
        sql = ("SELECT * FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND status = 'active' AND duplicate_of IS NULL AND derived IS NOT NULL "
               "AND (COALESCE(published_at, retrieved_at) >= ? OR ? IS NULL) ORDER BY COALESCE(published_at, retrieved_at) DESC, id LIMIT ?")
        return self.conn.execute(sql, (owner, project_id, since, since, limit)).fetchall()

    def count_articles(self, owner: str, project_id: str, status: str = "active") -> int:
        return self.conn.execute("SELECT COUNT(*) FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND status = ?", (owner, project_id, status)).fetchone()[0]

    def due_for_expiry(self, owner: str, project_id: str, now: str, limit: int = 500) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND status IN ('active','quarantined') AND expires_at IS NOT NULL AND expires_at <= ? "
                                 "ORDER BY expires_at LIMIT ?", (owner, project_id, now, limit)).fetchall()

    def projects_due_for_expiry(self, now: str, limit: int = 200) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT DISTINCT owner_user_id, project_id FROM news_articles WHERE status IN ('active','quarantined') AND expires_at IS NOT NULL AND expires_at <= ? LIMIT ?",
                                 (now, limit)).fetchall()

    def oldest_active(self, owner: str, project_id: str, limit: int) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND status IN ('active','quarantined') "
                                 "ORDER BY COALESCE(published_at, retrieved_at), id LIMIT ?", (owner, project_id, limit)).fetchall()

    def article_keys_for_source(self, source_id: str) -> List[str]:
        return [r[0] for r in self.conn.execute("SELECT text_key FROM news_articles WHERE source_id = ? AND text_key IS NOT NULL", (source_id,))]

    def source_counts(self, owner: str, project_id: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.conn.execute("SELECT source_id, status, COUNT(*) AS n, MAX(COALESCE(published_at, retrieved_at)) AS newest FROM news_articles WHERE owner_user_id = ? AND project_id = ? GROUP BY source_id, status", (owner, project_id)):
            d = out.setdefault(r["source_id"], {"active": 0, "quarantined": 0, "expired": 0, "newest": None})
            d[r["status"]] = r["n"]
            d["newest"] = max(filter(None, [d["newest"], r["newest"]]), default=None)
        return out

    # ------------------------------------------------------------------ events
    def events(self, owner: str, project_id: str, *, status: Optional[str] = None, limit: int = 50, offset: int = 0, min_sources: int = 1) -> Tuple[List[sqlite3.Row], int]:
        where, args = ["owner_user_id = ?", "project_id = ?", "source_count >= ?"], [owner, project_id, min_sources]
        if status:
            where.append("status = ?")
            args.append(status)
        clause = " AND ".join(where)
        total = self.conn.execute(f"SELECT COUNT(*) FROM news_events WHERE {clause}", args).fetchone()[0]
        rows = self.conn.execute(f"SELECT * FROM news_events WHERE {clause} ORDER BY COALESCE(last_seen, '') DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return rows, total

    def all_events(self, owner: str, project_id: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM news_events WHERE owner_user_id = ? AND project_id = ?", (owner, project_id)).fetchall()

    def event(self, owner: str, event_id: str):
        return self.conn.execute("SELECT * FROM news_events WHERE id = ? AND owner_user_id = ?", (event_id, owner)).fetchone()

    def upsert_event(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "label", "first_seen", "last_seen", "article_count", "source_count", "status", "summary", "members_hash", "updated_at")
        self.conn.execute(f"INSERT OR REPLACE INTO news_events ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def delete_events(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            self.conn.execute(f"DELETE FROM news_events WHERE id IN ({','.join('?' for _ in chunk)})", chunk)

    def set_event_ids(self, pairs: Iterable[Tuple[Optional[str], str]]) -> None:
        self.conn.executemany("UPDATE news_articles SET event_id = ? WHERE id = ?", list(pairs))

    def clear_event_ids(self, owner: str, project_id: str) -> None:
        self.conn.execute("UPDATE news_articles SET event_id = NULL WHERE owner_user_id = ? AND project_id = ?", (owner, project_id))

    # ------------------------------------------------------------------ influence
    def create_influence(self, **v: Any) -> None:
        cols = ("id", "project_id", "owner_user_id", "label", "config", "rule_version", "input_hash", "result_key", "result_hash", "candidate_count", "created_at")
        self.conn.execute(f"INSERT INTO influence_analyses ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [v.get(c) for c in cols])

    def influence(self, owner: str, analysis_id: str):
        return self.conn.execute("SELECT * FROM influence_analyses WHERE id = ? AND owner_user_id = ?", (analysis_id, owner)).fetchone()

    def influences(self, owner: str, project_id: str, limit: int = 50) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM influence_analyses WHERE owner_user_id = ? AND project_id = ? ORDER BY created_at DESC, id LIMIT ?", (owner, project_id, limit)).fetchall()

    def count_influences(self, owner: str, project_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM influence_analyses WHERE owner_user_id = ? AND project_id = ?", (owner, project_id)).fetchone()[0]

    def delete_influence(self, analysis_id: str) -> None:
        self.conn.execute("DELETE FROM influence_analyses WHERE id = ?", (analysis_id,))
