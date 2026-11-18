"""A stand-in for the ``libsql`` package, for tests only.

The real package cannot be installed in this build environment (no network), so the Turso adapter is exercised against this
stand-in. It is deliberately *less* convenient than Python's sqlite3, following what the libsql documentation shows and does
not promise:

* ``connect(database, auth_token=..., sync_url=..., sync_interval=...)`` only; passing ``isolation_level`` raises TypeError;
* statements run in implicit transactions that need ``commit()`` (the documented examples call ``conn.commit()`` after writes);
* rows are plain tuples (no ``row_factory``);
* errors are its own ``LibsqlError`` (not sqlite3 exceptions);
* remote URLs map to local files registered with :func:`register`, and the token must match;
* ``sync()`` exists and is counted; network failures and refused tokens can be injected.

Passing these tests shows the adapter does not depend on sqlite3 conveniences. It does not show that real Turso behaves the
same way; ``test_p17_turso_live.py`` checks that against a real test database.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict, Optional, Tuple

_registry: Dict[str, Tuple[str, str]] = {}            # url -> (file path, token)
_lock = threading.Lock()


class LibsqlError(Exception):
    pass


class Controls:
    """Failure injection shared by every fake connection."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fail_next_execute = 0
        self.fail_message = "hrana: stream closed by server"
        self.fail_connects = 0
        self.fk_pragma_ignored = False
        self.connects = 0
        self.syncs = 0
        self.statements = 0
        self.kwargs_seen: list = []


controls = Controls()


def register(url: str, path: str, token: str) -> None:
    with _lock:
        _registry[url] = (path, token)


def connect(database: str, *, auth_token: Optional[str] = None, sync_url: Optional[str] = None, sync_interval: Optional[int] = None) -> "Connection":
    controls.kwargs_seen.append({"database_is_url": "://" in str(database), "sync_url": bool(sync_url), "sync_interval": sync_interval})
    if controls.fail_connects > 0:
        controls.fail_connects -= 1
        raise LibsqlError("failed to connect: dns error: temporarily unavailable")
    url = sync_url or database
    with _lock:
        entry = _registry.get(url)
    if entry is None:
        raise LibsqlError(f"failed to connect: unknown database {url}")
    path, token = entry
    if auth_token != token:
        raise LibsqlError("HTTP status 401 Unauthorized: invalid auth token")
    controls.connects += 1
    return Connection(path)


class Cursor:
    def __init__(self, cur: sqlite3.Cursor) -> None:
        self._cur = cur
        self.description = cur.description
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> Any:
        return self._cur.fetchall()


class Connection:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False)    # legacy implicit transactions, tuple rows
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self.closed = False

    def execute(self, sql: str, parameters: Any = ()) -> Cursor:
        if self.closed:
            raise LibsqlError("connection is closed")
        controls.statements += 1
        if controls.fail_next_execute > 0:
            controls.fail_next_execute -= 1
            self.closed = True
            raise LibsqlError(controls.fail_message)
        if controls.fk_pragma_ignored and sql.strip().upper().startswith("PRAGMA FOREIGN_KEYS"):
            sql = "SELECT 0" if "=" not in sql else "SELECT 1"
        try:
            return Cursor(self._conn.execute(sql, parameters))
        except sqlite3.Error as e:
            raise LibsqlError(str(e)) from None

    def commit(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error as e:
            raise LibsqlError(str(e)) from None

    def rollback(self) -> None:
        self._conn.rollback()

    def sync(self) -> None:
        controls.syncs += 1

    def close(self) -> None:
        self.closed = True
        self._conn.close()
