"""Phase 11: run PredictAI's existing SQL on a Turso (libSQL) database without changing the code that issues it.

The app talks to the database the way Python's ``sqlite3`` works with ``isolation_level=None``: autocommit by default, explicit
``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK`` for multi-statement changes, ``conn.in_transaction``, rows read by column name
(``sqlite3.Row``) and ``sqlite3.IntegrityError`` for constraint failures. The ``libsql`` package follows the sqlite3 interface,
but its documentation does not promise row factories, autocommit, ``in_transaction`` or sqlite3's exception classes, so this
module supplies them itself instead of relying on undocumented behaviour:

* rows come back as :class:`Row` (index by position or name, ``keys()``, ``dict(row)``), built from ``cursor.description``;
* a statement outside an explicit transaction is committed straight away (autocommit);
* ``in_transaction`` is tracked from the BEGIN/COMMIT/ROLLBACK statements the app sends;
* driver errors are re-raised as the matching ``sqlite3`` exception, so existing ``except sqlite3.IntegrityError`` code keeps working;
* a read that fails because the network dropped is retried once on a fresh connection; writes are never retried (they may have
  been applied), so the caller sees the error;
* the auth token never appears in an error message.
"""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

_FIRST_WORD = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*([A-Za-z]+)", re.S)
_READ_PRAGMA = re.compile(r"^\s*PRAGMA\s+[\w.]+\s*(?:\(\s*[\w\"']*\s*\))?\s*;?\s*$", re.I)
_WRITE_WORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "REINDEX", "VACUUM", "ANALYZE")

_TRANSIENT_HINTS = ("connection", "connect", "stream", "hrana", "timed out", "timeout", "network", "dns", "tls", "ssl", "socket",
                    "reset by peer", "broken pipe", "eof", "502", "503", "504", "temporarily unavailable", "transport")
_AUTH_HINTS = ("401", "403", "unauthorized", "unauthorised", "forbidden", "authentication", "auth token", "invalid token", "jwt", "permission denied")


class DatabaseUnavailable(sqlite3.OperationalError):
    """The remote database could not be reached (network, DNS, TLS, timeout)."""


class DatabaseAuthError(sqlite3.OperationalError):
    """Turso refused the credentials."""


class DatabaseConfigError(RuntimeError):
    """The connection does not behave the way the app needs (for example foreign keys cannot be switched on)."""


def first_word(sql: str) -> str:
    m = _FIRST_WORD.match(sql or "")
    return m.group(1).upper() if m else ""


def is_read_only(sql: str) -> bool:
    """True only for statements that cannot change data, so retrying them after a dropped connection is safe."""
    word = first_word(sql)
    if word == "SELECT":
        return True
    if word == "PRAGMA":
        return bool(_READ_PRAGMA.match(sql)) and "=" not in sql
    return False


def _redact(text: str, secrets: Sequence[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


def _trigger_messages() -> Tuple[str, ...]:
    """The messages PredictAI's own triggers raise with RAISE(ABORT, '...'). SQLite reports them as constraint failures
    (sqlite3.IntegrityError); a driver may report only the text, so the adapter recognises the text."""
    from pathlib import Path
    found = []
    for f in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
        found += re.findall(r"RAISE\s*\(\s*(?:ABORT|FAIL|ROLLBACK)\s*,\s*'((?:[^']|'')+)'\s*\)", f.read_text(encoding="utf-8"), re.I)
    return tuple(sorted({m.replace("''", "'").lower() for m in found}))


_TRIGGER_MESSAGES = _trigger_messages()


def map_error(exc: BaseException, secrets: Sequence[str] = ()) -> BaseException:
    """The sqlite3 exception the app expects for a driver error. sqlite3 errors pass through unchanged."""
    if isinstance(exc, sqlite3.Error):
        return exc
    msg = _redact(str(exc) or type(exc).__name__, secrets)
    low = msg.lower()
    code = str(getattr(exc, "sqlite_errorname", "") or getattr(exc, "code", "") or "").upper()
    if ("constraint failed" in low or "constraint violation" in low or "sqlite_constraint" in low or "CONSTRAINT" in code
            or any(m in low for m in _TRIGGER_MESSAGES)):
        return sqlite3.IntegrityError(msg)
    if any(h in low for h in _AUTH_HINTS):
        return DatabaseAuthError("Turso refused the credentials. Check TURSO_AUTH_TOKEN (and that it belongs to this database). Driver said: " + msg)
    if any(h in low for h in _TRANSIENT_HINTS) or isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return DatabaseUnavailable("The database could not be reached: " + msg)
    if "locked" in low or "busy" in low:
        return sqlite3.OperationalError(msg)
    if any(h in low for h in ("no such", "syntax error", "already exists", "no column", "has no column", "parse error", "fts5", "unknown")):
        return sqlite3.OperationalError(msg)
    if "datatype mismatch" in low or "too big" in low:
        return sqlite3.DataError(msg) if hasattr(sqlite3, "DataError") else sqlite3.OperationalError(msg)
    return sqlite3.DatabaseError(msg)


class Row:
    """A result row readable like ``sqlite3.Row``: ``row[0]``, ``row["email"]``, ``row.keys()``, ``dict(row)``, iteration over values."""

    __slots__ = ("_values", "_names", "_index")

    def __init__(self, names: Tuple[str, ...], index: dict, values: Sequence[Any]) -> None:
        self._names, self._index, self._values = names, index, tuple(values)

    def keys(self) -> List[str]:
        return list(self._names)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            try:
                return self._values[self._index[key]]
            except KeyError:
                low = key.lower()                                   # sqlite3.Row is case-insensitive by name
                for i, n in enumerate(self._names):
                    if n.lower() == low:
                        return self._values[i]
                raise IndexError(f"No item with that key: {key!r}") from None
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Row):
            return self._names == other._names and self._values == other._values
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._names, self._values))

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._names, self._values))!r})"


class Cursor:
    """The result of :meth:`LibsqlConnection.execute`: fetchone/fetchall/fetchmany/iteration, rowcount, lastrowid, description."""

    def __init__(self, raw: Any, rows: Optional[List[Tuple[Any, ...]]] = None, connection: Any = None) -> None:
        self.connection = connection                                    # like sqlite3.Cursor.connection
        desc = getattr(raw, "description", None)
        self.description = desc
        names = tuple(d[0] for d in desc) if desc else ()
        self._names, self._index = names, {n: i for i, n in enumerate(names)}
        self._rows = [self._wrap(r) for r in (rows if rows is not None else [])]
        self._pos = 0
        rc = getattr(raw, "rowcount", -1)
        self.rowcount = rc if isinstance(rc, int) else -1
        lr = getattr(raw, "lastrowid", None)
        self.lastrowid = lr if isinstance(lr, int) else None

    def _wrap(self, values: Sequence[Any]) -> Any:
        if isinstance(values, Row):
            return values
        return Row(self._names, self._index, values) if self._names else tuple(values)

    def fetchone(self) -> Any:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> List[Any]:
        out = self._rows[self._pos:]
        self._pos = len(self._rows)
        return out

    def fetchmany(self, size: int = 1) -> List[Any]:
        out = self._rows[self._pos:self._pos + size]
        self._pos += len(out)
        return out

    def __iter__(self) -> Iterator[Any]:
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self) -> None:
        self._rows, self._pos = [], 0


class LibsqlConnection:
    """A libsql connection with the parts of ``sqlite3.Connection`` (``isolation_level=None``) that PredictAI uses."""

    def __init__(self, opener: Callable[[], Any], *, secrets: Sequence[str] = (), sync_after_write: bool = False, label: str = "turso") -> None:
        self._opener, self._secrets, self._sync_after_write, self.label = opener, tuple(s for s in secrets if s), sync_after_write, label
        self._explicit = False
        self.broken = False
        self.row_factory = None                                       # accepted for compatibility; rows are always Row
        self.opened_at = time.monotonic()
        self.last_used = self.opened_at
        self.reconnects = 0
        self._raw = self._open()

    # ------------------------------------------------------------------ lifecycle
    def _open(self) -> Any:
        try:
            raw = self._opener()
        except BaseException as e:                                     # noqa: BLE001
            raise map_error(e, self._secrets) from None
        self._prepare(raw)
        return raw

    def _prepare(self, raw: Any) -> None:
        """Foreign keys must be enforced on every connection; refuse to work without them."""
        try:
            raw.execute("PRAGMA foreign_keys = ON")
            self._commit_raw(raw)
            cur = raw.execute("PRAGMA foreign_keys")
            row = cur.fetchone()
        except BaseException as e:                                     # noqa: BLE001
            raise map_error(e, self._secrets) from None
        if not row or int(row[0]) != 1:
            raise DatabaseConfigError("Foreign keys could not be switched on for the Turso connection; PredictAI relies on them to keep data consistent.")

    @staticmethod
    def _commit_raw(raw: Any) -> None:
        commit = getattr(raw, "commit", None)
        if commit is not None:
            commit()

    def _reconnect(self) -> None:
        try:
            self._raw.close()
        except BaseException:                                           # noqa: BLE001
            pass
        self._raw = self._open()
        self.reconnects += 1
        self.broken = False

    def close(self) -> None:
        try:
            self._raw.close()
        except BaseException:                                           # noqa: BLE001
            pass
        self.broken = True

    # ------------------------------------------------------------------ sqlite3-compatible surface
    @property
    def in_transaction(self) -> bool:
        return self._explicit

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> Cursor:
        self.last_used = time.monotonic()
        word = first_word(sql)
        try:
            return self._run(sql, parameters, word)
        except (DatabaseUnavailable,) as e:
            if self._explicit or not is_read_only(sql):
                self.broken = True
                if self._explicit:
                    self._explicit = False                              # the transaction is gone with the connection
                raise
            self._reconnect()                                           # a dropped connection: one retry, reads only
            try:
                return self._run(sql, parameters, word)
            except DatabaseUnavailable:
                self.broken = True
                raise e from None

    def _run(self, sql: str, parameters: Sequence[Any], word: str) -> Cursor:
        try:
            raw_cur = self._raw.execute(sql, tuple(parameters) if not isinstance(parameters, dict) else parameters)
            rows = raw_cur.fetchall() if getattr(raw_cur, "description", None) else []
        except BaseException as e:                                      # noqa: BLE001
            mapped = map_error(e, self._secrets)
            if not self._explicit and not is_read_only(sql) and not isinstance(mapped, DatabaseUnavailable):
                rollback = getattr(self._raw, "rollback", None)         # a failed lone write must not leave the driver's implicit transaction open
                if rollback is not None:
                    try:
                        rollback()
                    except BaseException:                               # noqa: BLE001
                        self.broken = True
            if word in ("COMMIT", "END") and self._explicit:
                try:                                                    # a COMMIT that failed must not leave a half-open transaction behind for the next request
                    self._raw.execute("ROLLBACK")
                except BaseException:                                   # noqa: BLE001
                    self.broken = True
            if word in ("COMMIT", "END") or (word == "ROLLBACK" and not re.match(r"^\s*ROLLBACK\s+(?:TRANSACTION\s+)?TO\b", sql, re.I)):
                self._explicit = False
            raise mapped from None
        if word == "BEGIN":
            self._explicit = True
        elif word in ("COMMIT", "END"):
            self._explicit = False
            self._after_write()
        elif word == "ROLLBACK" and not re.match(r"^\s*ROLLBACK\s+(?:TRANSACTION\s+)?TO\b", sql, re.I):
            self._explicit = False
        elif not self._explicit and not is_read_only(sql):
            try:
                self._commit_raw(self._raw)                             # autocommit: a lone statement is committed at once (a no-op if the driver opened nothing)
            except BaseException as e:                                  # noqa: BLE001
                raise map_error(e, self._secrets) from None
            if word in _WRITE_WORDS or word == "WITH":
                self._after_write()
        return Cursor(raw_cur, rows, self)

    def _after_write(self) -> None:
        if self._sync_after_write:
            sync = getattr(self._raw, "sync", None)
            if sync is not None:
                try:
                    sync()
                except BaseException as e:                              # noqa: BLE001  the write already reached Turso; a failed refresh of the local copy is reported, not hidden
                    raise map_error(e, self._secrets) from None

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> Cursor:
        last = None
        for params in seq:
            last = self.execute(sql, params)
        return last if last is not None else Cursor(object(), [], self)

    def commit(self) -> None:
        if self._explicit:
            self.execute("COMMIT")

    def rollback(self) -> None:
        if self._explicit:
            self.execute("ROLLBACK")

    def __enter__(self) -> "LibsqlConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Same as ``sqlite3.Connection``: ``with conn:`` ends an open transaction (commit, or roll back on an error); it does NOT close."""
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
