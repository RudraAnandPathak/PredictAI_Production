# Data governance (Phase 3, Stage 8)

## What is stored, and where
| Data | Location | Notes |
|---|---|---|
| Original upload | storage `p/<project>/a/<asset>/v/<version>/original` | written once, read-only, never modified |
| Canonical table of a dataset / cleaned version | `…/v/<version>/table.csv` | derived versions are new objects |
| Extracted text, structure | `…/x/<extraction>.json` | |
| Profiles | `…/f/<profile>.json` | |
| Cleaning previews | `…/pv/<transformation>.json` | temporary; expire after `PREDICTAI_PREVIEW_TTL_MINUTES` |
| Chunks of an unfinished upload | `t/<upload>/<chunk>` | removed on completion, cancellation or expiry |
| Metadata only (ids, sizes, counts, checksums, statuses) | SQLite | no file contents, no extracted text |

All storage keys are opaque hex identifiers. User filenames and project names are metadata in the database and are never used in a path.

## Retention
* Default: a file is kept until its owner deletes it.
* Per file, the owner may set a date (`PATCH /assets/{id}/retention`, body `{"retain_until": "YYYY-MM-DD"}`; `null` clears it). Dates are normalised to UTC, must be in the future and within ten years. A date-only value means the end of that UTC day. Archived projects are read-only, so retention cannot be changed there.
* `Governance.run_retention()` deletes expired files with the same workflow as a manual deletion (below). It runs once at process start, and from the CLI: `python -m app.cli purge` (suitable for a daily cron/scheduled task on any host; it needs no paid service).

## Deletion workflow
1. A `deletion_records` row is written (`pending`) and the resource is hidden (`deleting`: invisible to its owner).
2. Every stored object under the asset/project prefix is removed: original, derived versions, extracted text, profiles, previews.
3. Database rows are removed and the record becomes `completed`, with counts of what was removed.
If step 2 fails the record stays pending and is retried by `run_retention`. `GET /workspace/deletions` shows the owner the evidence; it holds ids, status and counts only.

Before confirming, the UI shows the footprint (`GET /assets/{id}/footprint`): the number and size of stored items by kind. A test asserts that the number promised equals the number removed.

Account deletion calls the same workflow for every project of the user.

## Housekeeping performed by `run_retention`
* expired upload sessions and their chunks
* expired cleaning previews (files and rows)
* files past their retention date
* retry of deletions that did not finish
* assets stuck in `processing` for more than 30 minutes (the server stopped mid-way) become `failed` with code `PROCESSING_INTERRUPTED` — the original is kept
* orphan sweep: stored objects with no database row (a crash between "write" and "record") are removed, but only if older than 24 hours so an upload in flight is never touched
* a `retention.purged` audit event with counts, only when something was removed

## Privacy-aware logging
* The workspace logger (`predictai.workspace`) drops tracebacks. A failure is logged as the exception **type and code location only** (`ValueError at datasets.py:88 in rows`), because exception messages and source lines can quote the data being parsed.
* Audit events and project activity contain ids, sizes, counts, reason codes and step *names* — never file contents, extracted text, cell values, filenames or project names. Tests upload sentinel values and assert they appear in no log line, audit row, activity row or deletion record.
* Stored error text shown to the user (`status_detail`) is fixed, parser-authored wording.
* The global JSON formatter (Phase 1) is unchanged; the safe filter applies at the workspace logger so existing behaviour and tests are preserved.

## Storage abstraction (for a future cloud adapter)
`app/modules/storage/base.py` defines `StorageBackend`: `put`, `open`, `read_bytes`, `exists`, `size`, `modified_at`, `delete`, `delete_prefix`, `list_prefix`, `local_path`. Callers depend only on that protocol. To add an S3/R2 adapter later: implement the protocol (keys are already flat, opaque, `/`-separated strings), keep `put(..., overwrite=False)` write-once semantics, return the same `StorageError` on failure, and select it in the container from configuration. `local_path` is used only by parsers that need a seekable file; a cloud adapter can download to a temporary file there. Nothing in Phase 3 enables a paid service; Cloudflare R2 has a free tier but must not be switched on without explicit approval.

## Inventory
`GET /workspace/inventory` returns the caller's totals (projects, files by kind, original and derived bytes) and the files scheduled for deletion in the next N days (default 30). It is scoped to the caller; the Owner role sees only its own.

## Known limitations
* Retention is date-based per file; there are no per-project policies or legal holds yet.
* Deletion removes files from the storage backend, but it cannot erase copies in backups made outside the application.
* `run_retention` runs at start-up and via the CLI; a hosted deployment must schedule `python -m app.cli purge`.
