# Migration notes (Phase 3)

* `0001_identity.sql` is **byte-identical** to the Phase 1+2 release (checksum enforced: editing an applied migration makes the migrator refuse to run).
* `0002_workspace.sql` is new and additive: it creates `projects`, `project_events`, `assets`, `asset_versions`, `uploads`, `upload_chunks`, `extractions`, `profiles`, `transformations`, `deletion_records` and indexes. It alters no Phase 1/2 table. Social sign-in needed **no schema change** (it reuses `identities`, `auth_tokens` and `sessions`).
* SQL is plain SQLite and D1-compatible (no extensions, no triggers that D1 lacks).

## Applying
```bash
cd backend
python -m app.cli migrate        # idempotent; development also auto-migrates at start-up
```
Hosted environments do not auto-migrate (`PREDICTAI_AUTO_MIGRATE=false` by default there); run the command as a deploy step. The API reports "not ready" until the schema is current.

## Rollback
The migrator is forward-only (as in Phase 2). To go back, restore the database file from a backup taken before upgrading, and run the previous release. Phase 3 only *adds* tables, so the Phase 2 code runs unchanged against an upgraded database (the extra tables are ignored). Back up `PREDICTAI_DATABASE_PATH` **and** `PREDICTAI_STORAGE_DIR` together: the database holds metadata, the storage directory holds the files.

## Evidence (run in this environment, Python 3.13)
| Check | Result |
|---|---|
| Baseline (Phase 1+2) code creates DB, provisions an owner | `Applied migrations: [1]` |
| Phase 3 code upgrades that same DB | `Applied migrations: [2]`; second run "Database is up to date." |
| After upgrade | owner account intact; `PRAGMA integrity_check` = ok; `foreign_key_check` = 0 violations; 10 workspace tables present; `schema_migrations` = identity, workspace |
| Fresh database | `Applied migrations: [1, 2]` |
| `0001` edited after being applied | refused: "Migration 0001_identity was changed after it was applied." |
| `python -m app.cli purge` | runs identity housekeeping and workspace retention (all counts 0 on an empty database) |
Automated: `test_cli.py` (migrate output), `test_workspace_projects.py` (schema, constraints), plus every workspace test builds a fresh migrated database.
