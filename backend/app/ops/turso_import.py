"""Copy an existing local SQLite database into Turso (Phase 11). Run by an operator, never automatically.

    python -m app.cli import-to-turso --source data/predictai.db              # dry run: reads both sides, writes nothing
    python -m app.cli import-to-turso --source data/predictai.db --execute    # copies

What it guarantees:

* The source file is opened read-only and is never changed or deleted.
* The source must be fully migrated (same schema version as this code); otherwise it stops and says how to fix that on a copy.
* The target gets the schema from the normal migrations if it has none.
* A target that already holds user data is refused unless ``--allow-existing`` is given.
* Rows are matched by primary key. A row already in the target with identical content is counted as "already present";
  one with different content is a "conflict" and is never overwritten. Everything else is inserted.
* Tables are copied parents first (by foreign keys), in batches, each batch in its own transaction.
* Afterwards every table's count and keys are checked against the source and foreign keys are checked on the target.
* A JSON and a Markdown report are written. Neither contains the token or any row content (only keys of conflicting rows).

Full-text index tables are not copied: the target rebuilds them through its triggers as passages are inserted.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..db.migrator import discover, migrate, pending_versions

BATCH = 200
MAX_CONFLICT_KEYS = 20


class ImportRefused(RuntimeError):
    pass


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _source(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise ImportRefused(f"The source database {path} does not exist.")
    conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)            # read-only: the source can never be changed
    conn.row_factory = sqlite3.Row
    return conn


def _tables(src: sqlite3.Connection) -> List[str]:
    rows = src.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    virtual = [r["name"] for r in rows if (r["sql"] or "").upper().startswith("CREATE VIRTUAL TABLE")]
    out = []
    for r in rows:
        n = r["name"]
        if n.startswith("sqlite_") or n == "schema_migrations" or n in virtual or any(n.startswith(v + "_") for v in virtual):
            continue
        out.append(n)
    return out


def _order(src: sqlite3.Connection, tables: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Parents before children. Returns (order, tables in a cycle) — cycles are copied last with foreign-key checks deferred."""
    parents: Dict[str, Set[str]] = {t: set() for t in tables}
    for t in tables:
        for fk in src.execute(f"PRAGMA foreign_key_list({_q(t)})").fetchall():
            if fk["table"] in parents and fk["table"] != t:
                parents[t].add(fk["table"])
    order: List[str] = []
    left = dict(parents)
    while left:
        ready = sorted(t for t, ps in left.items() if not (ps & set(left)))
        if not ready:
            break
        order.extend(ready)
        for t in ready:
            del left[t]
    cyclic = sorted(left)
    return order + cyclic, cyclic


def _columns(src: sqlite3.Connection, table: str) -> Tuple[List[str], List[str]]:
    info = src.execute(f"PRAGMA table_info({_q(table)})").fetchall()
    cols = [r["name"] for r in info]
    pk = [r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])]
    return cols, pk


def _key(row: Any, cols: Sequence[str], pk: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(row[c] for c in (pk or cols))


def _target_has_user_data(t: Any) -> bool:
    has_schema = t.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
    return bool(has_schema) and t.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def run_import(source_path: str, target_db: Any, *, execute: bool = False, allow_existing: bool = False, report_dir: Optional[str] = None,
               batch: int = BATCH, clock=lambda: datetime.now(timezone.utc)) -> Dict[str, Any]:
    started = clock()
    report: Dict[str, Any] = {"mode": "execute" if execute else "dry-run", "started_at": started.isoformat(timespec="seconds"),
                              "source": Path(source_path).name, "target": target_db.describe(), "tables": [], "problems": [], "result": "ok"}
    try:
        _run(source_path, target_db, report, execute=execute, allow_existing=allow_existing, batch=batch)
    except ImportRefused as e:
        report["result"], report["refused_because"] = "refused", str(e)
    except sqlite3.Error as e:
        report["result"] = "failed"
        report["problems"].append(f"{type(e).__name__}: {e}")
    report["finished_at"] = clock().isoformat(timespec="seconds")
    if report["result"] == "ok" and (report["problems"] or any(t["status"] != "ok" for t in report["tables"])):
        report["result"] = "incomplete"
    if report_dir:
        report["report_files"] = _write_report(report, Path(report_dir), started)
    return report


def _run(source_path: str, target_db: Any, report: Dict[str, Any], *, execute: bool, allow_existing: bool, batch: int) -> None:
    src = _source(source_path)
    try:
        expected = [m.version for m in discover()]
        have = sorted(r[0] for r in src.execute("SELECT version FROM schema_migrations").fetchall()) if src.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_migrations'").fetchone()[0] else []
        if have != expected:
            raise ImportRefused(f"The source is at schema version {max(have) if have else 0}, this code expects {expected[-1]}. "
                                "Make a copy of the source file, run `python -m app.cli migrate` against the copy (PREDICTAI_DATABASE_BACKEND=sqlite, "
                                "PREDICTAI_DATABASE_PATH=<copy>), then import the copy.")
        report["schema_version"] = expected[-1]
        tables = _tables(src)
        order, cyclic = _order(src, tables)
        if cyclic:
            report["problems"].append(f"Tables that refer to each other in a cycle (copied last, foreign keys checked at the end): {', '.join(cyclic)}")
        with target_db.session() as t:
            has_table = t.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone()[0]
            pending = pending_versions(t) if has_table else expected               # a dry run must not even create the bookkeeping table
            if pending and len(pending) != len(expected):
                raise ImportRefused(f"The target is part-way through the schema (missing versions {pending}). Run `python -m app.cli migrate` against it first.")
            had_data = (not pending) and _target_has_user_data(t)
            if had_data and not allow_existing:
                raise ImportRefused("The target already holds user accounts. Nothing was written. To merge into it anyway (existing rows are never "
                                    "overwritten; matching rows are skipped and differing ones are reported), run again with --allow-existing.")
            report["target_had_data"] = had_data
            if pending:
                if execute:
                    migrate(t)
                    report["target_schema"] = "created by the migrations"
                else:
                    report["target_schema"] = "would be created by the migrations"
            for table in order:
                report["tables"].append(_copy_table(src, t, table, execute=execute, schema_exists=not pending or execute, batch=batch,
                                                    defer=table in cyclic))
            if execute:
                fk = t.execute("PRAGMA foreign_key_check").fetchall()
                report["foreign_key_violations"] = len(fk)
                if fk:
                    report["problems"].append(f"{len(fk)} foreign-key violation(s) in the target after the import (tables: "
                                              f"{', '.join(sorted({str(r[0]) for r in fk})[:10])}).")
    finally:
        src.close()


def _copy_table(src: sqlite3.Connection, t: Any, table: str, *, execute: bool, schema_exists: bool, batch: int, defer: bool) -> Dict[str, Any]:
    cols, pk = _columns(src, table)
    src_rows = src.execute(f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(table)}").fetchall()
    item: Dict[str, Any] = {"table": table, "source_rows": len(src_rows), "inserted": 0, "already_present": 0, "conflicts": 0, "conflict_keys": [],
                            "matched_by": "primary key" if pk else "whole row", "status": "ok"}
    target_rows: Dict[Tuple[Any, ...], Tuple[Any, ...]] = {}
    if schema_exists:
        for r in t.execute(f"SELECT {', '.join(_q(c) for c in cols)} FROM {_q(table)}").fetchall():
            target_rows[_key(r, cols, pk)] = tuple(r[c] for c in cols)
    to_insert: List[Tuple[Any, ...]] = []
    for r in src_rows:
        k, values = _key(r, cols, pk), tuple(r[c] for c in cols)
        if k in target_rows:
            if target_rows[k] == values:
                item["already_present"] += 1
            else:
                item["conflicts"] += 1
                if len(item["conflict_keys"]) < MAX_CONFLICT_KEYS:
                    item["conflict_keys"].append(list(k))
        else:
            to_insert.append(values)
            target_rows[k] = values                                       # a duplicate inside the source itself is only inserted once
    if not execute:
        item["would_insert"] = len(to_insert)
        item["target_rows_after"] = None
        return item
    sql = f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})"
    for i in range(0, len(to_insert), batch):
        chunk = to_insert[i:i + batch]
        t.execute("BEGIN IMMEDIATE")
        try:
            if defer:
                try:
                    t.execute("PRAGMA defer_foreign_keys = ON")
                except sqlite3.Error:
                    pass
            for values in chunk:
                t.execute(sql, values)
            t.execute("COMMIT")
        except BaseException:
            if t.in_transaction:
                t.execute("ROLLBACK")
            item["status"] = "failed"
            raise
        item["inserted"] += len(chunk)
    after = t.execute(f"SELECT {', '.join(_q(c) for c in (pk or cols))} FROM {_q(table)}").fetchall()
    keys_after = {tuple(r) for r in after}
    missing = [k for k in (_key(r, cols, pk) for r in src_rows) if tuple(k) not in keys_after]
    item["target_rows_after"] = len(after)
    if missing:
        item["status"] = "missing rows"
        item["missing"] = len(missing)
    elif item["conflicts"]:
        item["status"] = "conflicts"
    return item


def _write_report(report: Dict[str, Any], directory: Path, started: datetime) -> List[str]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"turso-import-{started.strftime('%Y%m%dT%H%M%SZ')}-{report['mode']}"
    Path(f"{stem}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [f"# Turso import report ({report['mode']})", "", f"- Result: **{report['result']}**", f"- Source: {report['source']} (opened read-only, not changed)",
             f"- Target: {report['target'].get('backend')} {report['target'].get('host', '')}", f"- Schema version: {report.get('schema_version', '?')}",
             f"- Started {report['started_at']}, finished {report.get('finished_at', '')}"]
    if report.get("refused_because"):
        lines += ["", f"**Refused:** {report['refused_because']}"]
    if report["tables"]:
        lines += ["", "| Table | Source rows | Inserted | Already present | Conflicts (not overwritten) | Target rows after | Status |", "|---|---|---|---|---|---|---|"]
        for tb in report["tables"]:
            ins = tb["inserted"] if report["mode"] == "execute" else f"{tb.get('would_insert', 0)} (would insert)"
            lines.append(f"| {tb['table']} | {tb['source_rows']} | {ins} | {tb['already_present']} | {tb['conflicts']} | {tb['target_rows_after'] if tb['target_rows_after'] is not None else '—'} | {tb['status']} |")
    if "foreign_key_violations" in report:
        lines += ["", f"Foreign-key violations in the target after the import: {report['foreign_key_violations']}"]
    for p in report["problems"]:
        lines.append(f"- Problem: {p}")
    Path(f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [f"{stem}.json", f"{stem}.md"]


def summary_lines(report: Dict[str, Any]) -> List[str]:
    out = [f"Turso import ({report['mode']}): {report['result']}"]
    if report.get("refused_because"):
        out.append("  " + report["refused_because"])
    totals = {k: sum(t.get(k) or 0 for t in report["tables"]) for k in ("source_rows", "inserted", "would_insert", "already_present", "conflicts")}
    if report["tables"]:
        out.append(f"  {len(report['tables'])} tables, {totals['source_rows']} source rows: "
                   + (f"{totals['inserted']} inserted" if report["mode"] == "execute" else f"{totals['would_insert']} would be inserted")
                   + f", {totals['already_present']} already present, {totals['conflicts']} conflicts (not overwritten)")
    for p in report["problems"]:
        out.append("  Problem: " + p)
    for f in report.get("report_files", []):
        out.append("  Report: " + f)
    return out
