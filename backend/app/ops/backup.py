"""Backup, verification, restore and retention for PredictAI (SQLite + local file storage).

    python -m app.ops.backup create  --out DIR [--database PATH] [--storage DIR]
    python -m app.ops.backup verify  BACKUP_DIR
    python -m app.ops.backup restore BACKUP_DIR --target EMPTY_DIR
    python -m app.ops.backup prune   --out DIR --keep N

Rules this tool keeps:
* The live database is read through SQLite's online backup API, so the copy is consistent even while the app runs (WAL is handled by SQLite).
* Files are copied, never moved or changed. A file that changes while it is being copied is retried once, then the backup FAILS instead of
  storing a half-written file.
* The backup is assembled in a hidden ``.partial-*`` directory and renamed only when complete, so a crash never leaves something that looks valid.
* Secrets are never included: ``.env*``, ``*.key``, ``*.pem``, ``dev-secret-key`` and similar are skipped and the skip list is recorded.
* Every file has a SHA-256 in ``manifest.json``; ``verify`` re-reads everything. ``restore`` writes ONLY into an empty target directory and
  refuses to touch the live database; it then runs SQLite integrity and foreign-key checks and re-verifies every restored file.
* Limits: this protects files and the database it is pointed at. It is not off-site storage, it does not encrypt (encrypt the backup
  directory yourself, e.g. with an encrypted disk image), and it does not capture Docker volumes or anything outside the two paths.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import __version__

FORMAT = 1
DB_NAME = "predictai.db"
SECRET_PATTERNS = (".env", ".env.*", "*.key", "*.pem", "*.p12", "*.pfx", "dev-secret-key", "*secret*", "id_rsa*", "*.token", "*.sock")
SKIP_PATTERNS = ("*.db-wal", "*.db-shm", "*.tmp", "*.partial", "*.lock", ".DS_Store")
PREFIX = "backup-"


class BackupError(Exception):
    pass


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _is_secret(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p) for p in SECRET_PATTERNS)


def _is_skipped(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in SKIP_PATTERNS)


def _schema_version(db: Path) -> Optional[int]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _copy_stable(src: Path, dst: Path) -> None:
    """Copy and make sure the source did not change while copying."""
    for attempt in (1, 2):
        before = src.stat()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        after = src.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and dst.stat().st_size == after.st_size:
            return
        time.sleep(0.2)
    raise BackupError(f"{src.name} kept changing while it was copied, so no consistent copy could be made. Try again when nothing is writing to it.")


def create_backup(database: str, storage_dir: str, out_dir: str, *, note: str = "") -> Dict[str, Any]:
    db, storage, out = Path(database), Path(storage_dir), Path(out_dir)
    if not db.is_file():
        raise BackupError(f"Database not found: {db}")
    out.mkdir(parents=True, exist_ok=True)
    started = _utc()
    bid = f"{PREFIX}{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    work = out / f".partial-{bid}"
    final = out / bid
    if final.exists():
        raise BackupError("A backup with this id already exists.")
    work.mkdir()
    try:
        files: List[Dict[str, Any]] = []
        # 1. database: consistent snapshot through SQLite itself
        target_db = work / DB_NAME
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(target_db)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode = DELETE")        # one self-contained file, no -wal/-shm side files
            finally:
                dst.close()
        finally:
            src.close()
        check = sqlite3.connect(target_db)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        for side in ("-wal", "-shm"):
            Path(str(target_db) + side).unlink(missing_ok=True)
        if ok != "ok":
            raise BackupError(f"The database copy failed its integrity check ({ok}). The live database may be damaged.")
        files.append({"path": DB_NAME, "size": target_db.stat().st_size, "sha256": _sha256(target_db), "component": "database"})
        # 2. storage tree
        skipped_secret, skipped_other, symlinks = [], [], []
        if storage.is_dir():
            for p in sorted(storage.rglob("*")):
                rel = p.relative_to(storage)
                if p.is_symlink():
                    symlinks.append(str(rel))
                    continue
                if not p.is_file():
                    continue
                if any(_is_secret(part) for part in rel.parts):
                    skipped_secret.append(str(rel))
                    continue
                if _is_skipped(p.name):
                    skipped_other.append(str(rel))
                    continue
                dest = work / "storage" / rel
                _copy_stable(p, dest)
                files.append({"path": f"storage/{rel.as_posix()}", "size": dest.stat().st_size, "sha256": _sha256(dest), "component": "storage"})
        manifest = {
            "format": FORMAT, "backup_id": bid, "created_at": started.isoformat(timespec="seconds"), "app_version": __version__,
            "schema_version": _schema_version(target_db), "note": note[:200],
            "components": sorted({f["component"] for f in files}),
            "counts": {"files": len(files), "bytes": sum(f["size"] for f in files)},
            "excluded": {"secrets": skipped_secret, "transient": skipped_other, "symlinks": symlinks,
                         "note": "Secrets (.env, keys, tokens) are never backed up. Keep them in your own secure store."},
            "files": files,
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2))
        os.rename(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def _load_manifest(backup_dir: Path) -> Dict[str, Any]:
    m = backup_dir / "manifest.json"
    if not m.is_file():
        raise BackupError("Not a backup: manifest.json is missing.")
    try:
        data = json.loads(m.read_text())
    except ValueError as e:
        raise BackupError("manifest.json is not valid.") from e
    if data.get("format") != FORMAT:
        raise BackupError(f"Unsupported backup format {data.get('format')!r}.")
    return data


def verify_backup(backup_dir: str) -> Dict[str, Any]:
    root = Path(backup_dir)
    man = _load_manifest(root)
    problems: List[str] = []
    listed = {f["path"] for f in man["files"]}
    for f in man["files"]:
        p = root / f["path"]
        if ".." in Path(f["path"]).parts or Path(f["path"]).is_absolute():
            problems.append(f"unsafe path: {f['path']}")
        elif not p.is_file():
            problems.append(f"missing: {f['path']}")
        elif p.stat().st_size != f["size"] or _sha256(p) != f["sha256"]:
            problems.append(f"changed or damaged: {f['path']}")
    for p in root.rglob("*"):
        if p.is_file() and p.name != "manifest.json" and p.relative_to(root).as_posix() not in listed:
            problems.append(f"not in manifest: {p.relative_to(root).as_posix()}")
    return {"backup_id": man["backup_id"], "ok": not problems, "files_checked": len(man["files"]), "problems": problems}


def restore_backup(backup_dir: str, target_dir: str) -> Dict[str, Any]:
    root, target = Path(backup_dir), Path(target_dir)
    v = verify_backup(backup_dir)
    if not v["ok"]:
        raise BackupError("The backup failed verification, nothing was restored: " + "; ".join(v["problems"][:5]))
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("The target must be an empty or new directory. Restore never writes over existing data.")
    man = _load_manifest(root)
    target.mkdir(parents=True, exist_ok=True)
    try:
        for f in man["files"]:
            dest = (target / f["path"]).resolve()
            if target.resolve() not in dest.parents:
                raise BackupError("The backup lists a path outside its own folder; it was not restored.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / f["path"], dest)
        report = check_restored(target, man)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)         # the target was empty or new when we started, so removing it loses nothing
        raise
    (target / "restore-report.json").write_text(json.dumps(report, indent=2))
    return report


def check_restored(target: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    problems: List[str] = []
    for f in manifest["files"]:
        rel = Path(f["path"])
        p = target / rel
        if not p.is_file() or _sha256(p) != f["sha256"]:
            problems.append(f"restored file differs: {f['path']}")
    conn = sqlite3.connect(target / DB_NAME)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if _has(conn, "users") else None
    finally:
        conn.close()
    if integrity != "ok":
        problems.append(f"database integrity: {integrity}")
    if fk:
        problems.append(f"{len(fk)} foreign-key violation(s)")
    if version != manifest.get("schema_version"):
        problems.append(f"schema version {version} differs from the backup's {manifest.get('schema_version')}")
    return {"backup_id": manifest["backup_id"], "restored_at": _utc().isoformat(timespec="seconds"), "target": str(target), "ok": not problems,
            "integrity_check": integrity, "foreign_key_violations": len(fk), "schema_version": version, "users": users,
            "files_verified": len(manifest["files"]), "problems": problems}


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def list_backups(out_dir: str) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(Path(out_dir).glob(PREFIX + "*")):
        if p.is_dir() and (p / "manifest.json").is_file():
            try:
                m = _load_manifest(p)
                out.append({"path": str(p), "backup_id": m["backup_id"], "created_at": m["created_at"], "files": m["counts"]["files"], "bytes": m["counts"]["bytes"]})
            except BackupError:
                continue
    return out


def prune_backups(out_dir: str, keep: int) -> List[str]:
    """Delete the oldest backups beyond `keep`. Only directories that hold a valid manifest are touched; the newest `keep` always stay."""
    if keep < 1:
        raise BackupError("Keep at least one backup.")
    found = list_backups(out_dir)
    doomed = found[:-keep] if len(found) > keep else []
    for b in doomed:
        shutil.rmtree(b["path"])
    for p in Path(out_dir).glob(".partial-*"):                    # leftovers of an interrupted run
        shutil.rmtree(p, ignore_errors=True)
    return [b["backup_id"] for b in doomed]


def _defaults() -> Dict[str, str]:
    from ..core.config import get_settings
    s = get_settings()
    return {"database": s.database_path, "storage": s.storage_dir, "backend": s.database_backend}


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ops.backup", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--out", required=True); c.add_argument("--database"); c.add_argument("--storage"); c.add_argument("--note", default="")
    v = sub.add_parser("verify"); v.add_argument("backup")
    r = sub.add_parser("restore"); r.add_argument("backup"); r.add_argument("--target", required=True)
    p = sub.add_parser("prune"); p.add_argument("--out", required=True); p.add_argument("--keep", type=int, required=True)
    l = sub.add_parser("list"); l.add_argument("--out", required=True)
    a = ap.parse_args(list(argv) if argv is not None else None)
    try:
        if a.cmd == "create":
            d = _defaults() if not (a.database and a.storage) else {}
            if not a.database and d.get("backend") == "turso":
                print("This tool backs up a local SQLite file; the database is on Turso (PREDICTAI_DATABASE_BACKEND=turso).\n"
                      "Back up Turso with its point-in-time restore and a dump: turso db shell <database> .dump > predictai-$(date +%F).sql\n"
                      "(see docs/PHASE11_TURSO_DATABASE_INTEGRATION.md, section 9). Stored files can still be backed up with --database <a local copy>.")
                return 2
            m = create_backup(a.database or d["database"], a.storage or d["storage"], a.out, note=a.note)
            print(f"OK  {m['backup_id']}  {m['counts']['files']} files, {m['counts']['bytes']} bytes, schema {m['schema_version']}, components {', '.join(m['components'])}")
            print(f"    {len(m['excluded']['secrets'])} secret file(s) deliberately left out")
        elif a.cmd == "verify":
            r_ = verify_backup(a.backup)
            print(("OK  " if r_["ok"] else "FAIL ") + f"{r_['backup_id']}: {r_['files_checked']} files checked", *r_["problems"], sep="\n    " if r_["problems"] else "")
            return 0 if r_["ok"] else 1
        elif a.cmd == "restore":
            rep = restore_backup(a.backup, a.target)
            print(("OK  " if rep["ok"] else "FAIL ") + f"restored {rep['backup_id']} to {rep['target']}: integrity {rep['integrity_check']}, FK violations {rep['foreign_key_violations']}, schema {rep['schema_version']}")
            return 0 if rep["ok"] else 1
        elif a.cmd == "prune":
            print("deleted:", ", ".join(prune_backups(a.out, a.keep)) or "nothing")
        elif a.cmd == "list":
            for b in list_backups(a.out):
                print(b["backup_id"], b["created_at"], b["files"], "files")
    except BackupError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
