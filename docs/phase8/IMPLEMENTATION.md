# Phase 8: Accounts, Access Control & Research Readiness. Implementation summary

**Rating: Implemented but partially verified.** Backend and frontend tests pass in the build sandbox (see `TESTING.md`). Nothing was run on real FastAPI, with `vite build`, in a browser, on macOS/Python 3.14, or against live providers, email or Slack.

## The audit first (the spec's rule: inspect before modifying)
Phase 2 already delivered most of what the Phase 8 list asks for. Rebuilding it would have broken the rules "preserve all existing functionality", "do not rewrite working modules" and "do not introduce duplicate database structures". The audit result, item by item:

| Spec item | State before Phase 8 | Phase 8 work |
|---|---|---|
| 1 Register, login, logout, hashing, persistent sessions, email validation, duplicate prevention, password reset, account management | Done in Phase 2 (scrypt hashing, rotating server-side sessions with idle + absolute expiry, "remember me", verification email, same answer for taken/new emails, recovery tokens, profile/password/devices/delete) | None. Re-verified by the new HTTP tests and the existing suite |
| 2 Roles: Normal (3 documents, 100 pages), Special, Owner; no self-assignment | Done (Phase 2 roles/tiers/permissions; Phase 3 quotas; Phase 7 owner-adjustable limits) | HTTP-level escalation tests added (`test_p8_api`) |
| 3 Permanent special access with hashed codes, rate limiting, brute-force protection, audit | Done in Phase 2 (individual single-use codes, 100-bit, only hashes stored, per-user failure lockout, per-IP limit, audit of every attempt) | Throttling re-proved over HTTP |
| 4 Owner initialisation from `OWNER_NAME/EMAIL/PASSWORD/ACTIVATION_CODE` | **Gap.** Phase 2 had a CLI only | **New:** `identity/bootstrap.py`, start-up hook, `bootstrap-owner` CLI command, settings |
| 5 Per-user isolation; owner access controlled and audited | Done (every query filters by owner; other people get 404; the owner role has no content access, only metadata/counts) | **Fixed a Phase 7 defect** (below) and extended isolation to the new tables |
| 6 Extend the database without destroying data | n/a | **New:** migration `0007_phase8_research_foundation.sql` (create-only) |
| 7 Research readiness: several workspaces per user, six source kinds, conversations with source references, per-role limits the owner can manage | **Gap** | **New:** `rw_*` tables, `researchspace` service, `/research-workspaces` API, owner-managed research limits. No research engine |
| 8 Every module respects auth | Done; verified by the existing route-wide sweep, which now also covers the new routes | None |
| 9 Frontend pages for login, register, forgot/reset, account settings, profile, special-access activation; navigation for future modules | Done in Phase 2 (`LoginPage`, `RegisterPage`, `ForgotPasswordPage`, `ResetPasswordPage`, `AccountPage` with Profile / Password / Special access / Devices / Connected accounts / Delete) | One navigation entry: **Research Intelligence** (Planned, phase 10). No redesign |
| 10 Security: hashing, middleware, RBAC, expiry, rate limiting, CSRF, validation, secrets, audit | Done in Phase 2/3 | New routes use the same guards; new secrets are never logged or shown |
| 11 Tests | Large existing suite | 36 new backend tests, 1 new frontend test (see `TESTING.md`) |

## What was added
| Layer | Files |
|---|---|
| Migration | `backend/app/db/migrations/0007_phase8_research_foundation.sql` (5 tables, 4 owner-consistency triggers) |
| Owner bootstrap | `app/modules/identity/bootstrap.py`; `app/main.py` (`_bootstrap_owner` at start-up); `app/cli.py` (`bootstrap-owner`); `app/core/config.py` (settings) |
| Research workspace foundation | `app/modules/researchspace/service.py`; `app/api/v1/research_workspaces.py`; router registration in `app/api/v1/__init__.py` |
| Limits | `ownerops/service.py` (`QUOTA_KEYS`, defaults shown in Owner controls); `identity/ratelimit.py` (`rw_write:user`) |
| Deletion fix | `app/modules/workspace/phase7_purge.py` (new), wired through `repository.py` and `governance.py` |
| Frontend | `src/app/navigation.js` (one planned item, phase 10) |
| Config | `config/env.example` and root `.env.example` (kept identical) |
| Tests | `test_p8_bootstrap.py`, `test_p8_research_workspaces.py`, `test_p8_deletion.py`, `test_p8_api.py`; `navigation.test.js` (+1); migration-count assertions in `test_cli.py` and `test_workspace_projects.py` |

## Modified existing files (all small, none rewritten)
`config.py` (8 settings), `main.py` (start-up hook), `cli.py` (one command), `ownerops/service.py` (two limit keys + note text), `ratelimit.py` (one policy), `workspace/repository.py` and `governance.py` (calls into the new purge module), `api/v1/__init__.py` (one router), `navigation.js`.

## Phase 7 defect found and fixed (errata)
While auditing isolation I reproduced that **deleting a project, a file or an account left Phase 7 data behind**: registry models and versions, their stored model copies (`registry/…`), reports, versions, evidence, exports (`reports/…`), schedules and runs, exam material, advanced-intelligence runs and shares. Those stored objects sit outside the project prefix, so the existing prefix delete never reached them. The Phase 7 documentation said deletion was complete; it was not.

Fix (`phase7_purge.py`): a project's deletion now removes all of those rows and objects and **revokes** (never deletes) shares pointing at them; a file's deletion removes exam documents and runs built from it, blanks report-evidence passages quoted from it and removes research sources that point at it; an account's deletion removes the person's research workspaces (all child rows), custom report templates and memberships and revokes shares they gave. `test_p8_deletion` fails when the fix is disabled (checked) and passes with it.

Existing databases are not changed by the fix itself: data left behind by earlier deletions stays there. Phase 9 should add a one-off orphan sweep (see `KNOWN_LIMITATIONS.md`).

## Design decisions and positions you should know about
1. **`OWNER_ACTIVATION_CODE` as a shared static code is weaker than the individual codes Phase 2 already issues.** It is implemented only as a *seed*: it must have the full 100-bit format, is stored only as a hash, works once for one account, and expires after 30 days. A short, memorable owner-chosen code is refused with a clear message. Nothing else in the system accepts a reusable code.
2. **Env password changes do not reset the owner.** Once an owner exists the owner variables are ignored, so a leaked or stale `.env` cannot be used to take over or reset the owner. `recover-owner` remains the audited break-glass route.
3. **`user_activity_logs` was not created.** `audit_events` (Phase 2) already records security and administrative activity with actor, target, outcome, IP, request id and metadata; a second table would duplicate it.
4. **New tables use the `rw_` prefix, not the suggested `research_*` names.** Phase 6 already owns `research_sessions`, `research_sources` and `research_notes` (project-scoped "answer a question" research). Reusing the names would have collided with, or silently changed, working tables. `rw_` keeps the two features independent.
5. **Special and Owner research limits are not "unlimited".** They are capped by safeguard settings (200 workspaces / 500 sources per workspace by default) so no account can exhaust the server. Standard limits (5 workspaces / 20 sources per workspace) are owner-adjustable.
6. **Foundations only.** There is no ingestion, retrieval, chat, summarisation or note-taking UI. The service creates, lists, renames, archives, deletes and limits workspaces, and exposes `ensure_source_slot` for Phase 10 to call. The tables for sources, conversations, messages and notes exist, are ownership-protected by triggers, and are cleaned up on deletion.
