# Backup and restoration guide

Tool: `backend/app/ops/backup.py` (SQLite + local file storage). Run every command from `backend/`.

## What is protected
| Included | Not included |
|---|---|
| The SQLite database (consistent snapshot through SQLite's online backup API, safe while the app runs) | Secrets: `.env*`, `*.key`, `*.pem`, `dev-secret-key`, anything named `*secret*` (the skipped names are recorded in the manifest) |
| Everything under `PREDICTAI_STORAGE_DIR` (uploads, derived files, model files, reports) | Docker volumes and anything outside those two paths |
| `manifest.json` with a SHA-256 per file | Off-site copies and encryption — **you** provide those (see below) |

**A backup holds users' private data** (uploaded documents, datasets, reports). Treat it like the live system: encrypted disk or image, restricted access, never committed to git or attached to a ticket.
**Keep `PREDICTAI_SECRET_KEY` and your `.env` in your own secret store.** They are deliberately not in the backup; a restored system needs them supplied again.

## Create
```
python -m app.ops.backup create --out ~/predictai-backups [--database PATH] [--storage DIR] [--note "before upgrade"]
python -m app.ops.backup verify ~/predictai-backups/backup-<timestamp>      # re-reads every file against the manifest
python -m app.ops.backup list   --out ~/predictai-backups
python -m app.ops.backup prune  --out ~/predictai-backups --keep 7          # also removes interrupted (.partial-*) runs only
```
A backup is built in a hidden `.partial-*` directory and renamed only when complete, so a crash never leaves something that looks valid. A file that keeps changing while being copied fails the backup rather than storing a half-written file. A damaged live database is reported, not backed up.

## Restore (never over live data)
```
python -m app.ops.backup restore ~/predictai-backups/backup-<timestamp> --target ~/predictai-restored   # target must be EMPTY
```
Restore refuses a non-empty target, a backup that fails verification, and a manifest with paths outside the backup. After copying it runs `PRAGMA integrity_check`, `PRAGMA foreign_key_check` and re-verifies every restored file. To make the restored copy the live system: stop the app, move the old data directory aside (do not delete it yet), put the restored database and storage in place, supply `.env`, start the app, check `/health/ready` and sign in.

## Recommended routine (a recommendation, not something the product enforces)
Before every upgrade; then daily while people use it. Keep at least 7, and copy the newest to a second location. **Run a restore drill after every change to the backup tool and at least monthly** — an unrestored backup is not evidence.

## Evidence (executed in the build sandbox)
- `test_p11_backup` (13 tests): scope and secret exclusion, originals untouched, consistent snapshot while the app writes, unstable file fails and leaves nothing, damaged/missing database, tampered/missing/extra file detected, restore into a new directory leaves live data alone, non-empty target refused, path-traversal manifest rejected, foreign-key damage detected, prune only removes interrupted runs, CLI exit codes.
- `test_p11_restore_drill`: a restored copy is internally consistent.
- Measured: a 5-file, 2.2 MB backup took 0.026 s and restore + verification 0.011 s (`perf_baseline.json`) — tiny data; not a prediction for large installations.

## Not tested
Restoring on macOS, a Docker volume layout, very large storage trees, backups to network or cloud storage, and encryption (not provided).
