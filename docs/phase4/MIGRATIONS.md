# Phase 4 migrations

`0003` (checksummed, forward-only, D1-compatible SQL) adds `analysis_runs`, `insights`, `conversations`, `conversation_messages`
and their indexes. `0001` and `0002` are untouched. Existing data and configuration are preserved.

Verified: a Phase 3 database (migrations 1, 2, one owner) upgraded with `python -m app.cli migrate` → "Applied migrations: [3]",
a second run → "Database is up to date.", owner preserved, tables present, app starts. Also covered by a unit test.
Rollback: restore the database backup taken before upgrading (migrations are forward-only by design).
