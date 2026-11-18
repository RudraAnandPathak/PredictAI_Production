# Phase 9 requirement status (Development Workspace brief, Parts 1-20)
Vocabulary: **Fully implemented** (built, tested in the sandbox) · **Partially verified** (built; a real-world path was not run here) · **Local-only** · **Feature-flagged** · **Future** · **Blocked** (cannot be offered until you verify something).

| Part | Requirement | Status | Notes / tests |
|---|---|---|---|
| 1 | Reuse Projects + Model Registry; no parallel project system | Fully implemented | `common.open_project`; registry link validated; `test_p9_pipelines_experiments` |
| 2 | Special/Owner-only, enforced by backend; landing page with real data and locked state | Fully implemented | `test_p9_files_access`, `test_p9_api`, `test_p9_security` 01-04 |
| 3 | Workspace shell + frontend navigation (Development, Compute) | Fully implemented; **not browser-tested** | `frontend/src/pages/dev`, `tests/dev.test.js`; sub-areas are tabs inside the project workspace, not separate sidebar entries |
| 4 | JupyterLab | **Partially verified / Local-only**: availability, limits, stop paths tested; server never launched | `sessions.py`; local_trusted only |
| 5 | Browser IDE | Same as 4 (code-server). The Code tab is a plain text editor, not Monaco/VS Code | |
| 6 | Per-project environments, wheels-only, lockfile, audit | Fully implemented offline; pypi.org builds and `pip-audit` **not run** (no network) | `test_p9_envs` |
| 7 | Isolated execution engine + state machine | Fully implemented for `local_trusted` on Linux; **docker UNVERIFIED; macOS UNVERIFIED** | `test_p9_engine`, `test_p9_security` |
| 8 | Project storage, quotas, versions, artifacts, deletion | Fully implemented | `test_p9_files_access`, `test_p9_deletion_upgrade` |
| 9 | Git (consent, protected branches, secret scan, webhooks) | Fully implemented against local bare repos; **github.com/gitlab.com + GIT_ASKPASS flow not run** | `test_p9_git` |
| 10 | Pipelines | **Feature-flagged** (off); deploy **Blocked/disabled by design** | `test_p9_pipelines_experiments` |
| 11 | Experiments and lineage | Fully implemented (records; missing links reported) | |
| 12 | Compute: usage, quotas, capabilities, owner overrides | Fully implemented; pause/resize/GPU reported **Future/unavailable** | `test_p9_api`, `test_p9_security` |
| 13 | Consolidated history with redaction | Fully implemented | `/history`, `/timeline`, History tab |
| 14 | Honest status/recovery states | Fully implemented | `docs/phase9/RECOVERY.md`; UI states |
| 15 | Versioned APIs | Fully implemented under `/api/v1/dev` | `docs/phase9/API.md` |
| 16 | 20 release-blocking security tests | All 20 present and passing **for Linux namespaces**; they do **not** establish Docker/macOS/multi-user safety | `tests/test_p9_security.py` |
| 17 | Workspace/Jupyter/IDE/env/execution/storage/Git/pipeline/lineage tests | Implemented | `test_p9_*` |
| 18 | Full regression | See `docs/phase9/TESTING.md` | |
| 19 | 15 documentation topics | Implemented | `docs/phase9/` |
| 20 | Final ZIP and report | Delivered | |

**Untrusted multi-user execution: Blocked.** The brief forbids approving it unless isolation tests establish the boundary; the Docker boundary could not be exercised here, and `local_trusted` leaves most of the host filesystem readable. Leave `PREDICTAI_DEV_EXECUTION=off` on any shared or hosted installation.
