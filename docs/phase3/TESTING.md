# Phase 3 testing evidence

Date of final run: 2026-11-12. Every number below was produced in this build environment (Linux, Python 3.13, Node, no network). **Real** = real code in the real environment; **stand-in** = real application code on a FastAPI stand-in (see "Not executed").

## Results
| Suite | Result | Notes |
|---|---|---|
| Backend, real environment (`python -m unittest discover -s tests`) | **323 run, OK** (319 before the correction round), 5 modules skipped | the 5 skipped modules need FastAPI + httpx: `test_health_api`, `test_identity_api`, `test_social_auth_api`, `test_workspace_api`, `test_workspace_security_api` |
| Backend with FastAPI stand-in | **392 run, OK** (385 before), 0 skipped | includes the 5 HTTP modules (14+31+7+13+6 = 71 tests). **Stand-in, not real FastAPI** |
| Frontend (`npm test`, Node runner) | **88 pass, 0 fail** | 12 files |
| Phase 2 browser regression (real Chromium via Playwright, bundle served by a local server) | **47/47** | identical script to the Phase 2 release |
| Phase 3 browser flows (same rig) | **75 steps, 0 failures, no console errors** | stage 3 (11), uploads (11), extraction (11), profiling (8), cleaning/history (15), governance (8), social auth (11) |
| `node --check vite.config.js` | passes | syntax only |
| Migrations / start-up / CLI | verified | see `MIGRATIONS.md` |
| Repo hygiene (`test_repo_hygiene`) | passes | no `.env`, database or data directory in the tree (it caught a stray `data/` created by a manual run; removed) |

Backend per file: cli 8, config 16, envelope/logging 9, health_api 14, identity_access 34, identity_api 31, identity_auth 35, identity_platform 38, identity_registration 13, repo_hygiene 4, request_context 11, social_auth 23, social_auth_api 7, workspace_api 13, workspace_cleaning 29, workspace_extraction 30, workspace_governance 13, workspace_profiling 12, workspace_projects 15, workspace_security_api 6, workspace_uploads 24.

## Spec §13 security tests → evidence
Ownership/isolation and guessed IDs (`test_workspace_security_api`, `test_workspace_projects`, `test_workspace_api`); anonymous access to every route; CSRF on every state change; path traversal and hostile filenames (`test_workspace_uploads`, security api); content-signature and extension mismatch; ZIP bombs, XML entity expansion (`test_workspace_extraction`); oversized/malformed uploads; quota and instant revocation (`test_workspace_uploads`, `test_identity_access`); no secrets/paths in responses or logs (`test_workspace_governance` safelog tests); OAuth state replay/expiry/swap, login-CSRF nonce, no merge by email, unverified email, suspended account (`test_social_auth*`). Two mutation checks (remove the nonce check; allow merging) made the suite fail, confirming those tests bite.

## Baseline tests that were changed (none deleted; same per-file counts)
* `tests/support.py`: storage directory and a `workspace()` helper.
* `test_cli.py`: migration output is now `[1, 2]`.
* `test_identity_api.py`: the `call()` helper accepts a body `content`.
* `frontend/tests/navigation.test.js`: Projects and Uploads are now existing navigation items, not "planned".
* Phase 2 e2e: unchanged. One check ("login form is fully keyboard operable") failed when the social buttons were first in the page; the buttons were moved below the form rather than changing the test.

## Not executed in the real environment (honest list)
1. **Real FastAPI** (not installable offline). HTTP-level tests ran on a Starlette/pydantic stand-in that mimics the FastAPI features the app uses. Run `pip install -r requirements-dev.txt && python -m unittest discover -s tests` to execute the 71 HTTP tests for real. Possible differences would be in FastAPI-specific validation wording or OpenAPI output.
2. **`vite build`** (Vite and @vitejs/plugin-react could not be installed: `npm install --offline` → ENOTCACHED, registry unreachable). Instead the same `src/` was bundled with esbuild (JSX, production minification) and driven in a real browser. Run `cd frontend && npm install && npm run build` to confirm.
3. **Real Google and GitHub sign-in.** Needs client credentials and the network; tested with a fake provider HTTP client and an intercepted redirect.
4. **Real SMTP delivery.** Development writes email files; the SMTP backend is exercised only through unit tests with a fake transport.
5. **Legacy `.xls` parsing** (needs `xlrd`): only the `PARSER_UNAVAILABLE` path is tested.
6. Optional libraries present in this environment and therefore exercised: `pypdf`, `pdfplumber`, `chardet`, local `tesseract` and `pdftoppm` (OCR verified on a generated scanned PDF). Absent and therefore untested: `xlrd` (item 5). Behaviour with a different library version is unverified.
7. Automatic retention expiry in a live server: covered by unit tests with an advanced clock, not by waiting in a browser.
8. CI (GitHub Actions) was not run.
9. macOS (the development target) was not available; tests ran on Linux.

## Correction round (2026-11-12)
* Route discovery in `test_workspace_security_api.py` now merges a recursive router-tree walk with `app.openapi()` (needed for FastAPI 0.162, whose `app.routes` holds lazy include objects). Assertions and the >40 / >20 thresholds are unchanged; three self-tests for the helper were added. **The failure could not be reproduced here (real FastAPI cannot be installed offline); the fix follows the reported symptoms and must be confirmed on a machine with FastAPI 0.162.5.**
* Google/GitHub "not set up": added `oauth_diagnostics`, `python -m app.cli oauth-status`, a start-up log line, intent-aware button wording, and `tests/test_oauth_diagnostics.py`. The actual cause on the reporting machine could not be observed (its `.env` is not available); see `SOCIAL_AUTH.md` troubleshooting.
