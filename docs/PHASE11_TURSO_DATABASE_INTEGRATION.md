# Phase 11: Turso (libSQL) database integration

> Status: implemented and tested against a stand-in for the `libsql` package. **Not yet run against a real Turso database**
> (the build environment had no network). Run the live checks in section 6.4 before relying on it. See section 11.
>
> Numbering: the repository already has an earlier "Phase 11" (release, backup and restore tooling, `docs/phase11/`,
> `tests/test_p11_*`). This work is registered as **11T** to keep the two apart; its tests are `tests/test_p17_turso*.py`.

## 1. Architecture

```
                    PREDICTAI_DATABASE_BACKEND
                      │
          ┌───────────┴────────────┐
       sqlite (default)          turso
          │                        │
   app/db/database.py         app/db/database.py
     Database                   TursoDatabase ── pool of up to 4 request connections
     (Python sqlite3,             │
      a local file)          app/db/libsql_adapter.py
                                LibsqlConnection ── libsql (official client) ── libsql:// (TLS) ── Turso
```

* Everything in the app gets its connection from `app.state.db` (requests), `open_database(settings)` (CLI, worker scripts) or
  `Database.connect()` (background job threads). No production code path opens SQLite directly any more, except the SQLite-only
  backup tool and the in-memory schema check in `app/ops/release.py`.
* Both backends expose `connect()`, `session()` and `ensure_ready()`. The rest of the code is unchanged: the same SQL, the same
  `transaction()` helper, the same `row["column"]` access, the same `except sqlite3.IntegrityError`.
* Turso is the only relational database in production. There is no PostgreSQL and no second database.

### What the adapter does (and why)
The `libsql` package follows Python's `sqlite3` interface, but its documentation does not promise row factories, autocommit,
`in_transaction`, rollback behaviour or sqlite3's exception classes. PredictAI depends on all of them, so the adapter provides
them itself rather than relying on undocumented behaviour:

| PredictAI relies on | Adapter |
|---|---|
| `row["email"]`, `dict(row)`, `row.keys()` (`sqlite3.Row`, 26 files) | builds a `Row` from `cursor.description` |
| autocommit (`isolation_level=None`) | asks the driver for it, and commits a lone statement itself either way |
| `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`, `conn.in_transaction` | tracks the transaction from the statements sent |
| `except sqlite3.IntegrityError` / `sqlite3.Error` (9 places) | maps driver errors to the sqlite3 classes by message |
| foreign keys enforced | `PRAGMA foreign_keys = ON` on every connection, then **checks** it is on; refuses the connection otherwise |
| a failed write must not leave a transaction open | rolls the driver back after a failed lone write or a failed COMMIT |
| network drops | a read is retried once on a fresh connection; a write is never retried (it may have been applied) |
| `with conn:` (31 places): commit or roll back, **not** close | same meaning as `sqlite3.Connection` |
| the app's own trigger errors (`RAISE(ABORT, '…')`, 8 messages) are `sqlite3.IntegrityError` | recognised by their text, read from the migrations |
| `cursor.connection` | provided |
| secrets | the token is removed from every error message; `repr()` and `/health/ready` never show it |

## 2. Client selection

| Package | Status | Use here |
|---|---|---|
| **`libsql`** (`pip install libsql`) | Turso's official client for **libSQL** databases: remote and embedded replicas. MIT. Pinned `0.1.11`. | **Used** |
| `pyturso` / `turso_serverless` | For Turso's newer engine. Its full-text search is Tantivy-based and does not provide SQLite's FTS5 virtual tables. | Not used: migration 0009 creates an FTS5 table |
| `libsql-client` | Archived June 2025 | Not used |
| `libsql-experimental` | Old name of `libsql` | Not used |

Turso documents FTS5 as a preloaded extension on libSQL databases. **When you create the database, make sure it is a libSQL
database**, not one on the newer engine.

Known facts and gaps about `libsql` (from Turso's docs and package indexes, not verified by running it here):
* Synchronous, sqlite3-style API: `libsql.connect(database=URL, auth_token=TOKEN)` for remote; `libsql.connect(path, sync_url=URL, auth_token=TOKEN, sync_interval=N)` plus `conn.sync()` for an embedded replica.
* Authentication: a database token (`turso db tokens create <db>`); connections use TLS for `libsql://` and `https://`.
* Parameterised queries: `?` placeholders.
* Not documented: `row_factory`, `isolation_level`, `in_transaction`, rollback semantics, exception classes, `rowcount`, Python 3.13 wheels. The adapter does not rely on any of them except `cursor.description`, which the live tests check.
* Concurrent writes on embedded replicas: "Not supported" according to Turso's comparison table.
* A dependency scanner (Snyk) rates the package's maintenance as **inactive**; the last release is about 6 months old. Turso recommends its newer packages for new projects. If `libsql` stops being maintained, the move would be to `turso_serverless` once Turso's newer engine supports what migration 0009 needs (see section 11).

## 3. Local SQLite (development, unchanged)
Nothing to do. `PREDICTAI_DATABASE_BACKEND` defaults to `sqlite`, the file is `data/predictai.db` (or `PREDICTAI_DATABASE_PATH`),
and migrations run automatically in development. The `libsql` package is not needed. All existing tests run on SQLite.

## 4. Turso production configuration
1. Install the Turso CLI and sign in (`turso auth login`).
2. Create a libSQL database close to the API. Render's nearest region is Singapore; choose the nearest Turso location your plan offers:
   `turso db create predictai` (check `turso db locations` for the closest one).
3. Get the URL: `turso db show predictai --url` → `libsql://predictai-<org>.turso.io`.
4. Create a token **for this database only** (least privilege; not an organisation-wide token): `turso db tokens create predictai`.
   Turso can also issue read-only tokens (check `turso db tokens create --help`); the API needs read-write.
5. Put both into the host's secret store (section 5). Never in the repository, never in the frontend.

## 5. Environment variables
| Variable | Required | Value |
|---|---|---|
| `PREDICTAI_DATABASE_BACKEND` | yes in production | `turso` (`sqlite` is the default) |
| `TURSO_DATABASE_URL` | with turso | `libsql://<database>-<org>.turso.io` (must be `libsql://` or `https://`; no credentials in it) |
| `TURSO_AUTH_TOKEN` | with turso | the database token (**secret**) |
| `PREDICTAI_TURSO_MODE` | no | `remote` (default) or `replica` (experimental; section 11) |
| `PREDICTAI_TURSO_REPLICA_PATH` | no | local copy for replica mode (default `<storage dir>/turso/replica.db`); a cache only |
| `PREDICTAI_TURSO_SYNC_INTERVAL` | no | replica mode: seconds between syncs; `0` = sync after every write |
| `PREDICTAI_AUTO_MIGRATE` | no | `false` when hosted (the default); migrations run from the start command |

Start-up validation refuses to run, listing every problem, when: the backend is not `sqlite`/`turso`; Turso is chosen without a
URL or token; the URL is not encrypted (plain `http://` is allowed only to a local server in development); the URL contains
credentials; the token has spaces or is too short; or **SQLite is used in production on Render** (detected by Render's `RENDER=true`),
where the file would be wiped on every deploy. Error messages never include the token.

## 6. Migrations

### 6.1 How they run
Migrations are the same 16 SQL files for both backends; nothing in them was changed. Each runs in its own `BEGIN IMMEDIATE`
transaction and is recorded in `schema_migrations` with a checksum.

* **Forward-only and repeatable:** applied migrations are skipped; an edited applied migration is refused; nothing is dropped or reset.
* **Safe with several instances starting at once:** the version is checked again inside the transaction, which only one process
  can hold, so a migration another instance just applied is skipped. A "database is locked/busy" error is retried with back-off.
* **Clear failures:** "Migration 0009_research_intelligence failed at statement 12 (CREATE VIRTUAL TABLE …): <reason>. It was
  rolled back; earlier migrations stay applied." The exception class is unchanged.
* **Not on every request:** `ensure_ready()` checks once per process. Hosted deployments do not migrate inside the app
  (`PREDICTAI_AUTO_MIGRATE=false`); the start command runs `python -m app.cli migrate` first, and the app refuses to serve an
  out-of-date schema.

### 6.2 A new Turso database
`python -m app.cli migrate` with the Turso variables set creates the whole schema. On Render this happens in the start command.

### 6.3 Existing local installations
Unchanged: local SQLite files keep working and keep migrating as before.

### 6.4 Live verification against a test Turso database (do this before going live)
```bash
turso db create predictai-test
cd backend && pip install -r requirements.txt -r requirements-turso.txt
PREDICTAI_TEST_TURSO_URL=$(turso db show predictai-test --url) \
PREDICTAI_TEST_TURSO_TOKEN=$(turso db tokens create predictai-test) \
PREDICTAI_TEST_TURSO_I_UNDERSTAND=this-database-may-be-changed \
python -m unittest test_p17_turso_live -v
```
It refuses to run against the production URL or a database that already has accounts. It checks: the driver's cursor
description, CRUD, rowcount, transactions and rollback, UNIQUE/CHECK/foreign-key errors, concurrent writers, the full migration
(including the FTS5 table) and a wrong token. Then, for a full check, run the whole suite against Turso semantics locally with
the stand-in (`PREDICTAI_TEST_DB=fake-turso python -m unittest discover -s tests`).

## 7. Importing an existing SQLite database
```bash
# 1. The source must be at the current schema. Work on a COPY:
cp data/predictai.db /tmp/predictai-copy.db
PREDICTAI_DATABASE_BACKEND=sqlite PREDICTAI_DATABASE_PATH=/tmp/predictai-copy.db python -m app.cli migrate
# 2. Dry run (reads both sides, writes nothing, writes a report):
PREDICTAI_DATABASE_BACKEND=turso TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... \
  python -m app.cli import-to-turso --source /tmp/predictai-copy.db
# 3. Copy:
  python -m app.cli import-to-turso --source /tmp/predictai-copy.db --execute
```
* The source is opened read-only and never changed or deleted.
* The target gets the schema from the migrations if it has none.
* A target that already has user accounts is refused unless `--allow-existing`.
* Rows are matched by primary key (whole row for tables without one). Identical rows are counted as "already present"; differing
  rows are **conflicts and are never overwritten** (their keys are listed). Everything else is inserted, parents before children,
  in batches of 200, each batch in its own transaction.
* Afterwards each table's count and keys are checked against the source, and `PRAGMA foreign_key_check` runs on the target.
* Reports: `data/import-reports/turso-import-<time>-<mode>.json` and `.md`, with counts per table, conflicts, foreign-key
  violations and problems. No token and no row content (only keys of conflicting rows).
* Accounts, password hashes, owner rights and special access are ordinary rows and are copied as they are. Sessions stay valid if
  `PREDICTAI_SECRET_KEY` is the same on the new server.
* Full-text index tables are not copied: the target rebuilds the index through its triggers as passages are inserted.
* **Files are not copied.** Rows that point at stored files (uploads, models, exports) need those files on the new server; see
  section 11.
* Each run reads every row of the target tables for duplicate checks, which counts towards Turso's monthly "rows read".

## 8. Render deployment
`render.yaml` (repository root) is a Blueprint for the API. Steps:
1. Do sections 4 and 6.4 first.
2. In Render: **New → Blueprint**, pick the repository; it creates `predictai-api` in **Singapore** with `rootDir: backend`.
3. Enter the `sync: false` values in the dashboard: `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `PREDICTAI_SECRET_KEY`,
   `PREDICTAI_FRONTEND_BASE_URL`, `PREDICTAI_CORS_ALLOWED_ORIGINS`, plus the email and AI variables from `config/env.production.example`.
4. Build: `pip install -r requirements.txt -r requirements-turso.txt`.
5. Start: `python -m app.cli migrate && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` (migrations first,
   safe on every start).
6. Health check: `/api/v1/health/ready` returns 200 only when Turso is reachable and the schema is current; it reports
   `database_backend: turso` and never the URL or token. A failure reason is written to the server log only.
7. If you have existing data, import it (section 7) **before** people start using the new server.
8. Create the first owner with the `PREDICTAI_OWNER_*` variables or `python -m app.cli provision-owner` from a Render shell.

The API needs no persistent disk for its **database**. Render's plan matters: the free instance has 512 MB RAM and sleeps after
15 minutes idle (about a minute to wake); `render.yaml` uses `starter` so it does not sleep. The analytics and ML features may need
more than 512 MB.

## 9. Backup and recovery
* **Turso point-in-time restore:** 1 day on the free plan (longer on paid plans). Restore into a new database:
  `turso db create predictai-restored --from-db predictai --timestamp 2026-11-18T10:00:00Z`, then point `TURSO_DATABASE_URL` at it.
  (Check `turso db create --help` for the current flags.)
* **Your own copies (recommended daily):** `turso db shell predictai .dump > predictai-$(date +%F).sql`, kept somewhere other than
  Render. Restore a dump into a new database with `turso db shell predictai-new < predictai-YYYY-MM-DD.sql`, or into a local SQLite
  file with `sqlite3 local.db < dump.sql` (then `import-to-turso` can copy it back).
* `python -m app.ops.backup` remains the tool for **local SQLite** installations. With the Turso backend it stops and points here.
* Recovery order: create or restore the database → set the URL/token → `python -m app.cli migrate` (no-op if current) →
  deploy → check `/api/v1/health/ready` → sign in as owner.

## 10. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Start-up: "TURSO_DATABASE_URL must be set…" | Variables missing in the host; they are listed all at once. |
| "Turso refused the credentials" | Wrong, expired or revoked token, or a token for another database. Create a new one. |
| "The database could not be reached" | Network or Turso outage; reads retry once, connections retry 3 times. Check Turso status. |
| "Foreign keys could not be switched on" | The connection ignores `PRAGMA foreign_keys`; the app refuses to run without them. Report it; do not bypass it. |
| "needs the libsql package" | Build did not install `requirements-turso.txt`. |
| pip cannot find `libsql` for this Python | Set `PYTHON_VERSION=3.12` and rerun the live tests. |
| Migration fails on `CREATE VIRTUAL TABLE … fts5` | The database is on Turso's newer engine, not libSQL. Create a libSQL database. |
| "database is locked" / busy under load | Concurrent write transactions; migrations retry, requests surface the error. The live concurrency test shows whether this happens. |
| `/health/ready` 503 | See the server log line "Readiness failed (turso database): …". |
| Slow pages | Every query is a network round trip in remote mode; pages that run many queries pay for each. Replica mode reads locally but is experimental. |

## 11. Known limitations (read before going live)
1. **Not run against real Turso.** All Turso behaviour is tested with a stand-in that behaves the way the `libsql` docs describe.
   Section 6.4 is the step that verifies it. Until it passes, treat the Turso backend as unverified.
2. **Files are still on the local disk.** Uploaded papers, research source files, trained models, exports and dev workspaces live
   under `PREDICTAI_STORAGE_DIR`. On Render this disk is wiped on every deploy and restart, so those files are lost while the
   database rows that point at them remain. Object storage is the next phase. Until then, on Render, treat stored files as temporary.
3. **Replica mode is experimental.** It relies on the driver's `sync()` and on interactive write transactions through an embedded
   replica, which are not verified; concurrent writes are documented as unsupported there. Use `remote`.
4. **Latency.** Remote mode makes a network round trip per statement. There is no query batching.
5. **Driver maintenance.** `libsql` is rated inactive by one scanner, and Turso is steering new projects to its newer engine.
6. **Vendor change.** Turso's pricing page currently says it is joining Supabase. The free plan today: 5 GB storage, 500 million
   rows read and 10 million rows written per month, 1 day of point-in-time restore.
7. **Frontend hosting.** If the frontend is served from a different site than the API, session cookies are cross-site; serve both
   under one domain (see earlier hosting notes).
8. `ops/backup.py` (file backup) does not apply to Turso; use section 9.
