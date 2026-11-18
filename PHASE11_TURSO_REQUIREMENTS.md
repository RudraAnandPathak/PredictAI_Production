# Phase 11T — Turso (libSQL) database integration: requirements & status
Registered as 11T because an earlier Phase 11 (release/backup tooling) already exists. Guide: docs/PHASE11_TURSO_DATABASE_INTEGRATION.md.
PASS = verified by tests that ran here. PARTIAL = implemented, but the Turso side was only tested against a stand-in for the libsql package (no network in the build environment).

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 11T-01 | Backend chosen by PREDICTAI_DATABASE_BACKEND (sqlite default, turso); local development never needs Turso | PASS | test_p17_turso.Config |
| 11T-02 | Startup validation: URL and token required for Turso, encrypted URLs only, no credentials in the URL, SQLite refused on Render in production; token never in errors, repr or health | PASS | test_p17_turso.Config, AppOnTurso |
| 11T-03 | Official client (libsql 0.1.11, pinned in requirements-turso.txt) | PARTIAL: chosen from Turso's docs; not installable here | test_p17_turso_live (skipped) |
| 11T-04 | Adapter: rows by name, autocommit, transactions, in_transaction, sqlite3 exception classes, FK enforced and checked, rollback after failed writes | PASS on the stand-in; PARTIAL on real Turso | test_p17_turso.Adapter; full suite with PREDICTAI_TEST_DB=fake-turso |
| 11T-05 | Connection reuse (pool of 4), no open transaction handed to the next request, retries: connect ×3, reads ×1, writes never | PASS on the stand-in | test_p17_turso.Connecting |
| 11T-06 | Clear errors for wrong credentials and unreachable database; readiness 503 without details | PASS on the stand-in | test_p17_turso.Connecting, AppOnTurso |
| 11T-07 | Every production code path uses the central database (app, CLI, worker scripts); backup tool refuses on Turso | PASS | grep audit; test_p17_turso |
| 11T-08 | Migrations: unchanged SQL, forward-only, repeatable, checksum-checked, safe with concurrent instances, clear failure messages, hosted start refuses an out-of-date schema | PASS on SQLite and the stand-in; PARTIAL on real Turso | test_p17_turso.Migrations, test_identity_platform, test_p11_release |
| 11T-09 | Import tool: dry run by default, source read-only and unchanged, refuses non-empty target without --allow-existing, duplicates and conflicts detected and never overwritten, counts/keys/FK verified, JSON+Markdown report without secrets or content | PASS on the stand-in | test_p17_turso.Import |
| 11T-10 | Accounts, sign-in, sessions, projects, owner access and special access work on the Turso path and survive an application restart | PASS on the stand-in | test_p17_turso.AppOnTurso |
| 11T-11 | The whole existing test suite passes on the Turso adapter path | PASS on the stand-in | PREDICTAI_TEST_DB=fake-turso full run |
| 11T-12 | The whole existing test suite still passes on local SQLite | PASS | full run |
| 11T-13 | Live checks against a real Turso test database | NOT RUN (needs your test database) | test_p17_turso_live |
| 11T-14 | Render deployment: render.yaml (Singapore, migrate-then-start, readiness health check), env examples, guide | PARTIAL: written, not deployed | — |
| 11T-15 | No production data on Render's temporary disk | NOT IMPLEMENTED for files (uploads, models, exports); database done. Next phase: object storage | — |
| 11T-16 | Replica (embedded) mode | PARTIAL: implemented, experimental, unverified | test_p17_turso.Connecting |
