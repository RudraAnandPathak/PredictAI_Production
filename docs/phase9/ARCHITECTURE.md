# Phase 9 architecture: Development Workspace

**Numbering.** The brief was titled "Phase 8"; Phase 8 (accounts, access control, research readiness) was already delivered, so this release is **Phase 9**. The roadmap label "Development workspace" in `navigation.js` still reads 8 (the master roadmap's number); only the delivery numbering moved.

## Shape
```
Browser (React)  ->  FastAPI  /api/v1/dev/*   (validates, authorises, records; NEVER runs user code)
                          |
                      SQLite  dv_* tables  (jobs queue, files, envs, artifacts, secrets, repos, pipelines, experiments, events)
                          |
        python backend/scripts/run_dev_worker.py   (the ONLY process that executes user code)
                          |
                  isolation provider:  off | local_trusted | docker
```
The web process only inserts a `queued` job row. The worker claims it (fair queue: fewest running jobs for that person first, then priority, then age), re-checks the person's entitlement, builds a scrubbed job folder, wraps the command in the provider, supervises it (wall clock, memory, processes, output, storage, cancellation, revocation) and records the result.

## Modules (`backend/app/modules/devspace/`)
| Module | Responsibility |
|---|---|
| `common.py` | Special-access gate (`DevService.gate`), project open/ownership via the existing Project system, blocked file names, history events |
| `files.py` | Versioned file tree (content-addressed, optimistic concurrency, restore, search, quotas, credential-content refusal) |
| `envs.py` | Per-project environments: immutable versions, wheels-only builds, lockfile, `pip check`, audit if available |
| `engine.py` | Job service + state machine + Worker |
| `isolation.py` | Providers and the honest `describe()` of each |
| `sessions.py`, `notebooks.py` | JupyterLab / IDE sessions (local_trusted only), notebook create/validate/export |
| `git.py` | Git through the `git` CLI with hardened flags, consent, protected branches, webhooks |
| `pipelines.py`, `experiments.py` | Pipelines (feature-flagged), experiments, lineage, recorded approval/rollback decisions |
| `artifacts.py`, `compute.py`, `secrets.py`, `overview.py` | Artifacts, quotas/usage/capabilities, encrypted secret references, landing page |

Reuse: Projects, Data Workspace assets (copied read-only into jobs, own project only), identity `Principal`/`Perm`, rate limiting, audit, the Phase 7 Model Registry (an experiment can link a `reg_versions` row that belongs to the same project and owner), and Phase 7 deletion (`phase7_purge`, `governance`) which now also removes `dv_*` rows, folders, sessions and running jobs.

## Job state machine
`queued -> starting -> running -> succeeded | failed | timed_out`, `queued|starting|running -> cancelling -> cancelled`, `queued -> blocked`, `running -> cancelling -> cancelled (blocked_reason = entitlement_revoked)`. Only the transitions in `engine.TRANSITIONS` exist; a test asserts every other pair is refused.

## Data model
Migration `0008_dev_workspace.sql` adds 18 empty `dv_*` tables and nothing else; a 7->8 upgrade test proves no existing table, row, index or trigger changes.
