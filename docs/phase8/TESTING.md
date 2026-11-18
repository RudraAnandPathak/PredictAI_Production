# Phase 8: testing record

All numbers are from runs in the build sandbox (Linux, Python 3.13, Node 22) on 15 Nov 2026. Nothing here was run on macOS / Python 3.14, against real FastAPI (a stand-in was used), with `vite build`, in a browser, or against live providers, email or Slack.

## Backend
- Command: `cd backend && PYTHONPATH=<FastAPI stand-in>:.:tests python -m unittest discover -s tests -p "test_*.py"`
- **Full suite: 1554 tests, all passed** (1518 before Phase 8 + 36 new). Docs and env files were edited while that run was in progress, so `test_repo_hygiene`, `test_config`, `test_cli` and `test_p8_bootstrap` were re-run afterwards and pass.
- New tests:
  - `test_p8_bootstrap` (11): no-op without env values; creates an owner who can sign in; idempotent; a different email or password never creates a second owner or resets the first; weak code refused and nothing created; code stored only as a hash and redeemable once; code expires; no secret in logs or settings repr; audit records the environment source; CLI command; documented variable names and precedence; no bootstrap/owner-creation route.
  - `test_p8_research_workspaces` (12): CRUD, archive/restore; unique names per person; other people get 404; platform owner gets no access; standard limit, owner override, bounds and reset; special accounts capped by the safeguard; revoking special access restores the standard limit; source slot check; six source kinds and database constraints; conversations/messages with validated `source_refs` and cross-owner writes refused; validation; audit holds ids, not content.
  - `test_p8_deletion` (5): project deletion removes every Phase 7 row and the `registry/` and `reports/` objects and revokes shares; another project is untouched; file deletion removes what was built from it; account deletion removes research workspaces (all child rows), templates, memberships, and revokes shares. **Checked to fail with the fix disabled** (5 of 5 failed), so the tests do detect the defect.
  - `test_p8_api` (7, HTTP): sessions and CSRF on the new routes; lifecycle, isolation, limits; registration and profile updates cannot carry a role/tier/owner flag; a normal account cannot use any owner route or promote itself; wrong activation codes are rate limited and grant nothing; no public owner-creation route.
  - `test_workspace_projects`: one new test (migration 7 adds only empty `rw_` tables, changes nothing else, enforces the triggers and constraints, is idempotent).
- Existing tests changed (legitimately; none weakened): migration list/count assertions in `test_cli` and `test_workspace_projects` now expect version 7; the Phase 6→7 upgrade test now applies migrations up to 6 only, so it still proves "migration 6 adds only Phase 7 tables".
- Existing tests that already cover spec items 1–3, 8 and 10 and still pass: `test_identity_*` (registration, login, sessions, recovery, roles, codes, throttling, owner rules, audit), `test_workspace_security_api` (every non-public route refuses anonymous callers; every state-changing route needs CSRF; discovery now includes `/research-workspaces`), `test_phase6_security`, `test_predictive_security`.
- One bug found by the new tests in my own code and fixed before delivery: the activation-code shape check compared against `PAI` although normalisation turns `I` into `1`, so every valid code was refused.

## Frontend
- `cd frontend && node --test`: **235 tests, 235 passed** (one new in `navigation.test.js`).
- Import check: `esbuild src/main.jsx --bundle` completes with no errors. This is **not** `vite build` and says nothing about rendering.

## Not run
Real FastAPI/uvicorn (including the actual start-up bootstrap hook) · `npm run build` · any browser or end-to-end run · accessibility audit · macOS and Python 3.14 · live SMTP/Slack/providers · load and multi-process concurrency tests · a penetration test.

## To verify on your machine
```
cd backend && pip install -r requirements-dev.txt && python -m unittest discover -s tests
cd ../frontend && npm install && npm test && npm run build
# first-owner flow, with a throwaway database:
PREDICTAI_OWNER_EMAIL=you@example.com PREDICTAI_OWNER_PASSWORD='<passphrase>' python -m app.cli migrate && python -m app.cli bootstrap-owner
```
