"""Database access. Connections are short-lived (one per request) and run in autocommit mode; multi-statement changes use
:func:`transaction` explicitly.

Two backends (Phase 11), chosen with ``PREDICTAI_DATABASE_BACKEND``:

* ``sqlite`` (default): a local SQLite file, exactly as before. Used for development and tests.
* ``turso``: a remote libSQL database on Turso, through :mod:`app.db.libsql_adapter`. Used in production on Render, where the
  local disk is wiped on every deploy and restart.

Both expose the same ``connect()`` / ``session()`` / ``ensure_ready()`` methods, so the rest of the app does not know which
one it is talking to. Use :func:`open_database` to build the right one from settings.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional


class Database:
    """A local SQLite file."""

    backend = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path
        self._ready = False
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[Any]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def describe(self) -> Dict[str, Any]:
        """Safe to show in health checks: no credentials, no file paths."""
        return {"backend": self.backend}

    def ensure_ready(self, *, auto_migrate: bool) -> None:
        """Make sure the schema is current before serving requests (once per process)."""
        from .migrator import MigrationError, migrate, pending_versions

        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.session() as conn:
                if auto_migrate:
                    migrate(conn)
                elif pending_versions(conn):
                    raise MigrationError("The database schema is not up to date. Run: python -m app.cli migrate")
            self._ready = True


class TursoDatabase(Database):
    """A remote libSQL database on Turso.

    ``mode="remote"``: every statement goes to Turso over an encrypted connection (libsql:// or https://).
    ``mode="replica"``: an embedded replica; reads come from a local copy, writes go to Turso, and the copy is synced after each
    write (or every ``sync_interval`` seconds). The local copy is only a cache and may be lost at any time.

    Request connections are kept in a small pool so each request does not pay for a new TLS connection; background workers that
    call :meth:`connect` get their own dedicated connection.
    """

    backend = "turso"
    POOL_SIZE = 4
    IDLE_PING_AFTER = 30.0          # seconds; an idle pooled connection is checked before reuse (the server may have closed it)
    CONNECT_ATTEMPTS = 3

    def __init__(self, url: str, auth_token: str, *, mode: str = "remote", replica_path: str = "", sync_interval: int = 0,
                 module: Any = None, sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(path=f"turso:{_host(url)}")
        self._url, self._token, self.mode, self._replica_path, self._sync_interval = url, auth_token, mode, replica_path, sync_interval
        self._module, self._sleep = module, sleep
        self._pool: List[Any] = []
        self._pool_lock = threading.Lock()

    def __repr__(self) -> str:                                           # never shows the token
        return f"TursoDatabase(host={_host(self._url)!r}, mode={self.mode!r})"

    def _libsql(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            import libsql                                                # the official libSQL Python package (pip install libsql)
        except ImportError as e:                                         # pragma: no cover - depends on the install
            raise RuntimeError("PREDICTAI_DATABASE_BACKEND=turso needs the libsql package: pip install -r backend/requirements-turso.txt") from e
        self._module = libsql
        return libsql

    def _opener(self) -> Callable[[], Any]:
        lib = self._libsql()
        if self.mode == "replica":
            path = self._replica_path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            kwargs: Dict[str, Any] = {"sync_url": self._url, "auth_token": self._token}
            if self._sync_interval:
                kwargs["sync_interval"] = self._sync_interval

            def open_replica() -> Any:
                conn = _connect_compat(lib, path, **kwargs)
                conn.sync()                                              # start from the current remote state
                return conn
            return open_replica

        def open_remote() -> Any:
            return _connect_compat(lib, database=self._url, auth_token=self._token)
        return open_remote

    def connect(self) -> Any:
        from .libsql_adapter import DatabaseAuthError, DatabaseUnavailable, LibsqlConnection

        last: Optional[BaseException] = None
        for attempt in range(self.CONNECT_ATTEMPTS):
            try:
                return LibsqlConnection(self._opener(), secrets=(self._token,), sync_after_write=(self.mode == "replica" and not self._sync_interval),
                                        label=f"turso:{self.mode}")
            except DatabaseAuthError:
                raise                                                    # a wrong token will not fix itself
            except DatabaseUnavailable as e:
                last = e
                if attempt + 1 < self.CONNECT_ATTEMPTS:
                    self._sleep(0.5 * (2 ** attempt))
        assert last is not None
        raise last

    @contextmanager
    def session(self) -> Iterator[Any]:
        conn = self._checkout()
        try:
            yield conn
        finally:
            self._checkin(conn)

    def _checkout(self) -> Any:
        with self._pool_lock:
            conn = self._pool.pop() if self._pool else None
        if conn is None:
            return self.connect()
        if time.monotonic() - conn.last_used > self.IDLE_PING_AFTER:
            try:
                conn.execute("SELECT 1").fetchone()                      # a read: reconnects by itself once if the connection was dropped
            except sqlite3.Error:
                conn.close()
                return self.connect()
        return conn

    def _checkin(self, conn: Any) -> None:
        if conn.in_transaction:                                          # a request that ended mid-transaction must not hand it to the next one
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                conn.broken = True
        if conn.broken:
            conn.close()
            return
        with self._pool_lock:
            if len(self._pool) < self.POOL_SIZE:
                self._pool.append(conn)
                return
        conn.close()

    def close_all(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, []
        for c in pool:
            c.close()

    def describe(self) -> Dict[str, Any]:
        return {"backend": self.backend, "mode": self.mode, "host": _host(self._url)}


def _host(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split("?", 1)[0].rsplit("@", 1)[-1]


def _connect_compat(lib: Any, *args: Any, **kwargs: Any) -> Any:
    """Ask the driver for autocommit (isolation_level=None) when it accepts that option; the adapter commits lone statements
    itself either way, so a driver without the option still behaves correctly."""
    try:
        return lib.connect(*args, isolation_level=None, **kwargs)
    except TypeError:
        return lib.connect(*args, **kwargs)


def open_database(settings: Any, *, module: Any = None) -> Database:
    """The database the settings ask for."""
    if getattr(settings, "database_backend", "sqlite") == "turso":
        replica = settings.turso_replica_path or str(Path(settings.storage_dir) / "turso" / "replica.db")
        return TursoDatabase(settings.turso_database_url, settings.turso_auth_token, mode=settings.turso_mode, replica_path=replica,
                             sync_interval=settings.turso_sync_interval, module=module)
    return Database(settings.database_path)


@contextmanager
def transaction(conn: Any) -> Iterator[Any]:
    """Atomic block (BEGIN IMMEDIATE). Joins an already-open transaction instead of nesting."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
