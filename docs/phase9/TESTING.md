# Phase 9 testing record

All results below were produced in the build sandbox (Linux, Python 3.13, FastAPI **stand-in**, no network, no Docker, uid 0). Nothing here was run on macOS / Python 3.14 or against real FastAPI.

## Final full regression (after the last code change)
`cd backend && python -m unittest discover -s tests -p "test_*.py"` -> **Ran 1666 tests ... OK** (0 failures, 0 errors; 713 s). Phase 8 baseline was 1554 (net +112; the per-module method counts below sum to 114 and I did not reconcile the 2-test difference).
Frontend: `cd frontend && node --test` -> **244 tests, 244 pass** (Phase 8 baseline 235; +9 in `tests/dev.test.js`; `navigation.test.js` updated, not weakened: Development and Compute move from planned to existing).
Frontend import check: `esbuild --bundle src/main.jsx` resolves every import of the new pages (**not `vite build`**).

An earlier full run found one failure: `test_workspace_security_api` (CSRF sweep) hit the Git webhook route, which is deliberately session-less and CSRF-exempt (authenticated by HMAC). It was added to that test's explicit PUBLIC list with a comment; the webhook's own authentication is tested in `test_p9_git` and `test_p9_api`. The anonymous-call sweep still covers it (it answers 401). The full suite was then re-run from scratch (the result above).

## Existing tests changed (not weakened)
`test_cli` (migrations list now ends at 8) · `test_workspace_projects` (migration lists now include 8; the Phase 7 upgrade test now stops at migration 7 so it still proves "7 only") · `test_workspace_security_api` (webhook route listed as public, above) · `navigation.test.js` (above). New: 7->8 upgrade test in `test_p9_deletion_upgrade`.

## New modules
| Module | Tests | Covers |
|---|---|---|
| `test_p9_files_access` | 9 | gate, landing, project isolation, versions/concurrency/restore/delete, unsafe paths, credential content, quota, suspension/reactivation, secrets |
| `test_p9_engine` | 28 | Lifecycle, limits, cancel/timeout, revocation, recovery/retry, queue, fairness, isolation honesty, docker command construction |
| `test_p9_envs` | 8 | offline wheel builds, pins, versions, lockfile, compare |
| `test_p9_git` | 15 | remote rules, consent, commit/branch/tag, secret scan, pull/push rules, hooks, credentials, webhooks |
| `test_p9_pipelines_experiments` | 19 | stages, flag, deploy disabled, experiments, lineage, registry link |
| `test_p9_api` | 10 | HTTP: CSRF, 403/locked landing, lifecycle, envelopes, notebook export, owner overrides, capabilities, webhook |
| `test_p9_security` | 20 | the 20 Part-16 items, numbered `test_01`...`test_20` |
| `test_p9_deletion_upgrade` | 5 | 7->8 upgrade, project/account deletion (rows, folders, running job killed, other project untouched), file deletion ownership |

## Part 16 mapping (what each test really asserts)
01 plain user cannot write/submit/create a workspace row · 02 other Special user gets 404 on read/write/submit · 03 owner gets 404 on others' projects, works on own, only owner may set overrides · 04 every service entry point refuses without Special access · 05 job cannot read another project's files by host path · 06 other user/other project cannot read/delete an artifact · 07 job sees no host secret variables · 08 secret absent from job output, stored rows, audit and captured platform logs · 09 socket/HTTP/DNS all blocked · 10 memory limit kills; output flood capped · 11 cancel ends the process (pid gone) · 12 timeout ends it · 13 revoked user cannot submit or write · 14 running job cancelled with `entitlement_revoked`, pid gone · 15 credential-looking content, `.git/config`, `.netrc` refused · 16 webhook tests present and executed · 17 artifact read/retention/delete permissions · 18 write over quota refused · 19 deletion needs ownership and the typed name · 20 private file content absent from events and platform logs, other user cannot read logs.

## Not run (reported, not assumed)
Docker execution · macOS `sandbox-exec` · Jupyter/code-server · real Git hosts · pypi.org builds / pip-audit · real FastAPI · `vite build` · browser/accessibility · macOS / Python 3.14.
