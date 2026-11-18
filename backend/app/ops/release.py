"""Release records and migration-compatibility checks.

    python -m app.ops.release manifest ARCHIVE.zip [--commit SHA] [--tests-json FILE] [--notes FILE] [--out FILE]
    python -m app.ops.release compat            # is every migration additive (safe for the previous app version)?

A release manifest ties one archive to what it contains: version, commit (only if you give one: this tool never guesses), build id, SHA-256 of the
archive, migration state with checksums, hashes of the dependency files and of the configuration template, and the test report you pass in.
Nothing here deploys anything or touches a database."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import __version__
from ..db.migrator import MIGRATIONS_DIR, discover, migrate

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def schema_snapshot(conn: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    """{table: {column: declared type}} for every table the application owns."""
    snap: Dict[str, Dict[str, str]] = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        snap[name] = {r[1]: (r[2] or "").upper() for r in conn.execute(f'PRAGMA table_info("{name}")')}
    return snap


def additive_diff(old: Dict[str, Dict[str, str]], new: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    """What the previous application version could no longer find: dropped tables, dropped columns, changed column types."""
    out: Dict[str, List[str]] = {"dropped_tables": [], "dropped_columns": [], "changed_types": []}
    for t, cols in old.items():
        if t not in new:
            out["dropped_tables"].append(t)
            continue
        for c, typ in cols.items():
            if c not in new[t]:
                out["dropped_columns"].append(f"{t}.{c}")
            elif new[t][c] != typ:
                out["changed_types"].append(f"{t}.{c}: {typ} -> {new[t][c]}")
    return out


def _db_at(version: int, directory: Path = MIGRATIONS_DIR) -> sqlite3.Connection:
    tmp = Path(tempfile.mkdtemp())
    for m in discover(directory):
        if m.version <= version:
            (tmp / f"{m.version:04d}_{m.name}.sql").write_text(m.sql, encoding="utf-8")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    migrate(conn, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return conn


def compatibility_report(directory: Path = MIGRATIONS_DIR) -> List[Dict[str, Any]]:
    """For each migration N: would application version N-1 still find everything it knew? Computed from real fresh databases, not from reading SQL."""
    versions = [m.version for m in discover(directory)]
    rows = []
    prev = _db_at(versions[0], directory)
    prev_snap = schema_snapshot(prev)
    prev.close()
    rows.append({"version": versions[0], "additive": True, "diff": {}})
    for v in versions[1:]:
        cur = _db_at(v, directory)
        snap = schema_snapshot(cur)
        cur.close()
        diff = additive_diff(prev_snap, snap)
        rows.append({"version": v, "additive": not any(diff.values()), "diff": {k: x for k, x in diff.items() if x}})
        prev_snap = snap
    return rows


def manifest(archive: str, *, commit: Optional[str] = None, tests: Optional[Dict[str, Any]] = None, notes: str = "") -> Dict[str, Any]:
    arc = Path(archive)
    if not arc.is_file():
        raise FileNotFoundError(archive)
    with zipfile.ZipFile(arc) as z:
        names = z.namelist()
        bad = [n for n in names if n.endswith((".env", ".db", ".sqlite", ".pem", ".key")) or "/node_modules/" in n or "/__pycache__/" in n or "/.venv/" in n]
    migs = discover()
    return {
        "format": 1,
        "version": __version__,
        "build_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit or "not recorded (this tree is not a git checkout; pass --commit)",
        "archive": {"name": arc.name, "sha256": _sha(arc), "bytes": arc.stat().st_size, "files": len(names), "forbidden_entries": bad},
        "database": {"latest_migration": migs[-1].version, "migrations": [{"version": m.version, "name": m.name, "sha256": m.checksum} for m in migs]},
        "dependencies": {p.name: _sha(p) for p in sorted(BACKEND.glob("requirements*.txt"))} | ({"frontend/package.json": _sha(ROOT / "frontend" / "package.json")} if (ROOT / "frontend" / "package.json").is_file() else {}),
        "configuration": {p.name: _sha(p) for p in sorted((ROOT / "config").glob("*.example"))},
        "tests": tests or "not supplied",
        "notes": notes,
        "ok": not bad,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ops.release")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest"); m.add_argument("archive"); m.add_argument("--commit"); m.add_argument("--tests-json"); m.add_argument("--notes"); m.add_argument("--out")
    sub.add_parser("compat")
    a = ap.parse_args(list(argv) if argv is not None else None)
    if a.cmd == "compat":
        bad = 0
        for r in compatibility_report():
            print(f"{r['version']:04d}  {'additive' if r['additive'] else 'NOT ADDITIVE'}  {json.dumps(r['diff']) if r['diff'] else ''}")
            bad += 0 if r["additive"] else 1
        return 1 if bad else 0
    tests = json.loads(Path(a.tests_json).read_text()) if a.tests_json else None
    notes = Path(a.notes).read_text() if a.notes else ""
    man = manifest(a.archive, commit=a.commit, tests=tests, notes=notes)
    out = Path(a.out or (a.archive + ".release.json"))
    out.write_text(json.dumps(man, indent=2))
    print(("OK   " if man["ok"] else "FAIL ") + f"{out}  {man['version']}  build {man['build_id']}  sha256 {man['archive']['sha256'][:16]}…")
    return 0 if man["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
