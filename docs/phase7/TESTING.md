# Phase 7: testing record

All numbers below are from runs in the build sandbox (Linux, Python 3.13, Node 22) on 14 Nov 2026. Nothing here was run on macOS / Python 3.14, against real FastAPI, with `vite build`, in a browser, or against live providers.

## Backend
- Command: `cd backend && PYTHONPATH=<FastAPI stand-in>:.:tests python -m unittest discover -s tests -p "test_*.py"`
- **Full suite: 1518 tests, 1517 passed, 1 failed** (`test_repo_hygiene`: the root `.env.example` must mirror `config/env.example`, which I had edited). The mirror was fixed and `test_repo_hygiene` re-run: 7/7 pass. **The whole suite was not run a third time after that one-file fix.**
- Phase 7 tests (80, all passing): `test_p7_registry` 10, `test_p7_reports` 10, `test_p7_exam` 10, `test_p7_intelligence` 8, `test_p7_collab` 13, `test_p7_schedule` 11, `test_p7_api` 10 (HTTP: authentication, CSRF, flows, isolation), `test_p7_dashboard_owner` 8.
- Existing tests changed (legitimately; none weakened): five assertions about the migration list/count (`test_cli`, `test_workspace_projects`) now expect migration 6; the Phase 5→6 upgrade test now applies migrations 1–5 only so it still proves "migration 5 adds only the research tables", and a new test proves "migration 6 adds only Phase 7 tables, keeps every row, index definition and checksum, and is idempotent".
- Spec 13 coverage by area: authorisation and isolation (other users get 404, owner role gets no workspace access), revocation effective on next request, last-admin protection, invitation single use and email binding, lifecycle transitions and confirmations, integrity failures, deterministic exam generation with origin labels, blocking vs warning validation, approval never automatic, optimistic edit conflict, stale-research detection, citation and export content, formula neutralisation, scheduling idempotency/retry/dead-letter/blocked-on-revocation/maintenance pause, quota overrides and bounds, maintenance guard, metadata-only owner views, monitoring "unavailable" and "stale" states, wording guards (no "probability", no causal claims).

## Frontend
- `cd frontend && node --test`: **234 tests, 234 passed** (9 new in `tests/phase7.test.js`; `navigation.test.js` and one assertion in `predictive.test.js` updated because exam, reports and the model registry are no longer planned placeholders).
- Import and syntax check: `esbuild src/main.jsx --bundle` (react externalised) completes with no errors, so every import and named export in the new pages resolves. **This is not `vite build`** and says nothing about runtime rendering.

## Not run
Real FastAPI/uvicorn · `npm run build` · any browser or end-to-end run · keyboard/screen-reader audit · macOS and Python 3.14 (export-library wheels) · live SMTP/Slack · live research providers · load or concurrency testing of the scheduler across several processes.

## To verify on your machine
```
cd backend && pip install -r requirements-dev.txt -r requirements-reports.txt && python -m unittest discover -s tests
cd ../frontend && npm install && npm test && npm run build
```
