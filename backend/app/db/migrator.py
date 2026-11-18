"""Versioned SQL migrations: ``migrations/NNNN_name.sql`` applied in order, once each,
each inside a transaction. Applied files are checksummed so edits to history are detected."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> List[Migration]:
    found: List[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILE.match(path.name)
        if not match:
            raise MigrationError(f"Migration file name must look like 0001_name.sql: {path.name}")
        text = path.read_text(encoding="utf-8")
        found.append(Migration(int(match.group(1)), match.group(2), text, hashlib.sha256(text.encode()).hexdigest()))
    versions = [m.version for m in found]
    if versions != sorted(set(versions)) or (versions and versions != list(range(1, len(versions) + 1))):
        raise MigrationError(f"Migration versions must be unique and consecutive from 1: {versions}")
    return found


def _split(sql: str) -> List[str]:
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError("Migration ends with an incomplete statement.")
    return statements


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )


def applied(conn: sqlite3.Connection) -> Dict[int, str]:
    _ensure_table(conn)
    return {row[0]: row[1] for row in conn.execute("SELECT version, checksum FROM schema_migrations")}  # works with any row factory


def latest_version(directory: Path = MIGRATIONS_DIR) -> int:
    found = discover(directory)
    return found[-1].version if found else 0


def pending_versions(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> List[int]:
    done = applied(conn)
    return [m.version for m in discover(directory) if m.version not in done]


BUSY_ATTEMPTS = 6


def _busy(e: BaseException) -> bool:
    text = str(e).lower()
    return isinstance(e, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def migrate(conn: Any, directory: Path = MIGRATIONS_DIR, *, sleep: Callable[[float], None] = time.sleep) -> List[int]:
    """Apply every pending migration. Returns the versions applied by this call.

    Safe when several processes start at once (Phase 11): each migration runs in its own BEGIN IMMEDIATE transaction, which only
    one process can hold, and the version is checked again inside that transaction, so a migration another process has just
    applied is skipped instead of applied twice. A busy database is retried a few times. A failure rolls the migration back and
    names the migration and the statement that failed. Nothing is ever dropped or reset here: migrations only move forward."""
    done = applied(conn)
    newly: List[int] = []
    for migration in discover(directory):
        if migration.version in done:
            if done[migration.version] != migration.checksum:
                raise MigrationError(f"Migration {migration.version:04d}_{migration.name} was changed after it was applied.")
            continue
        for attempt in range(BUSY_ATTEMPTS):
            try:
                if _apply_one(conn, migration):
                    newly.append(migration.version)
                break
            except sqlite3.OperationalError as e:
                if _busy(e) and attempt + 1 < BUSY_ATTEMPTS:
                    sleep(0.25 * (2 ** attempt))
                    continue
                raise
    return newly


def _apply_one(conn: Any, migration: Migration) -> bool:
    """True when this call applied it; False when another process applied it first."""
    conn.execute("BEGIN IMMEDIATE")
    step, statement = 0, ""
    try:
        row = conn.execute("SELECT checksum FROM schema_migrations WHERE version = ?", (migration.version,)).fetchone()
        if row is not None:                                              # another process got here first
            conn.execute("ROLLBACK")
            if row[0] != migration.checksum:
                raise MigrationError(f"Migration {migration.version:04d}_{migration.name} was changed after it was applied.")
            return False
        for step, statement in enumerate(_split(migration.sql), 1):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, migration.checksum, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute("COMMIT")
        return True
    except MigrationError:
        raise
    except BaseException as e:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        if _busy(e):
            raise
        if isinstance(e, sqlite3.Error):                                 # same exception class as before (callers catch sqlite3.Error), clearer message
            first_line = " ".join(statement.split())[:120]
            raise type(e)(f"Migration {migration.version:04d}_{migration.name} failed at statement {step} ({first_line}…): {e}. "
                          "It was rolled back; earlier migrations stay applied. Fix the cause and run the migration again.") from e
        raise
