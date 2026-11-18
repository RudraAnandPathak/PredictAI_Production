# Phase 1 + Phase 2 integration record

## What was integrated
Phase 2 (identity & security) was built on top of the Phase 1 tree, so integration was a compatibility review plus
repair, not a rebuild. Phase 2 deleted nothing from Phase 1; it added 73 files and modified 28 Phase 1 files.
Dependency manifests are unchanged (no new dependencies, no paid services, no new infrastructure).

## Deliberate contract changes (Phase 1 tests updated accordingly)
| Area | Phase 1 | Now | Why |
|---|---|---|---|
| Production config | no secret needed | strong `PREDICTAI_SECRET_KEY` and an https `PREDICTAI_FRONTEND_BASE_URL` required | sessions and signed tokens need a secret |
| API client credentials | `same-origin` | `include` | the session cookie must reach an API on another origin in production |
| Navigation | only Overview and System status exist | Account and Owner controls are real; each entry has an access level | role-aware navigation |
| CORS | read-only methods | also POST/PATCH/DELETE, credentials, `X-CSRF-Token` | authenticated API |

## Regressions found during integration and fixed
- CI workflow had been reduced (branch filter, `permissions`, pip cache, Node 22): original restored.
- `.gitignore` had lost editor/OS/cache entries: Phase 1 list restored plus `data/`, `*.db`, `*.eml`.
- README had lost Phase 1 health-check steps and configuration notes: restored and merged.
- Backend tests that import `app.main` wrote `data/dev-secret-key` into the project: tests now use a throwaway secret.
- Added a root `.env.example` (placeholders only) and tests that guard against secrets in templates.

## Known limitations
- Verified in a sandbox without network: the HTTP layer was exercised with a minimal FastAPI stand-in (real Starlette and
  pydantic), and the frontend production bundle was built with esbuild. Run `pip install -r backend/requirements-dev.txt`,
  `npm install`, `npm test` and `npm run build` on your machine for the real-FastAPI and real-Vite confirmation.
- Email delivery is verified only with the file backend and fake senders; configure SMTP for production.
- Google/GitHub OAuth linking is implemented and tested with fake providers, never against real ones.
- Browser end-to-end tests are not part of CI.
- Phase 3 (projects, data workspace, governance, Google/GitHub sign-in) was added afterwards; see `docs/phase3/`. Phase 4 (Core Intelligence) was added after that; see `docs/phase4/`. Phase 5 (Predictive Intelligence & AutoML, plus the app-wide Explanation section) followed; see `docs/phase5/`. Phase 6 and later are not started.

## Phase 6 (Research & World Intelligence)
Added on top of Phase 5 without removing anything: migration 0005, six new backend packages (`webaccess`, `providers`, `retrieval`, `research`, `world`, `textkit`), 73 routes, the Research section of the front end. Existing files changed are listed in `docs/phase6/IMPLEMENTATION.md`; the two existing tests that count migrations were updated (and a Phase 5 → 6 upgrade test added). Requirements: `docs/phase6/REQUIREMENTS_REGISTER.md`; carried forward: `docs/phase6/OUTSTANDING.md`.



## Phase 8
Phase 8 (accounts, owner bootstrap, research workspace foundation, deletion fix) is documented in `docs/phase8/` (start with `IMPLEMENTATION.md`); requirement status is in `PHASE8_REQUIREMENTS.md`. Migration 0007 is create-only.

## Phase 7
Phase 7 integration notes, install, migration and guides are in `docs/phase7/` (start with `IMPLEMENTATION.md`); requirement status is in `PHASE7_REQUIREMENTS.md`.
