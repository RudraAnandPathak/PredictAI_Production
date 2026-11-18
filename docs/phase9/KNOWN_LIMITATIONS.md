# Phase 9: known limitations (read before relying on it)

## Not verified in the build environment
**Docker execution** (no daemon; command construction only) · **macOS `sandbox-exec`** and **macOS / Python 3.14** · **JupyterLab and code-server sessions** (never launched) · **pushes/pulls to real github.com / gitlab.com and the `GIT_ASKPASS` flow** · **package builds from pypi.org and `pip-audit`** (no network) · **real FastAPI** (the stand-in supports pydantic body models only) · **`vite build`** (esbuild import check passes; that is not a build) · **any browser, keyboard or screen-reader check of the new pages**.

## Product limits
- **Untrusted multi-user execution is not offered.** `local_trusted` hides sibling project folders but leaves the rest of the host filesystem readable to the job; `docker` is unverified. Every provider reports `multi_user_safe: false`.
- **Execution is off by default** and refused for `local_trusted` when hosted.
- **The "Code" tab is a plain text editor** with versions, not VS Code/Monaco. The full IDE is code-server in `local_trusted` mode only.
- **Notebook jobs are headless**: code cells only, no IPython magics, no rich display outputs beyond text/repr; PDF export is not available.
- **No cgroups**: memory/process limits are monitored (sampled every ~50 ms) and enforced by killing the process group; a very fast allocation can overshoot briefly. CPU and file size use kernel rlimits.
- **Environment builds use the network** (allow-listed indexes) outside the no-network isolation; wheels only, so packages with no wheel for the platform cannot be installed.
- **Pause, resize, GPU: unavailable**, reported as such. No managed compute, nothing that can incur charges.
- **No deployment.** Approval/rollback are records.
- **Pipelines are feature-flagged off**; triggers other than manual/webhook-recorded are not scheduled automatically.
- **One worker, one job at a time** by default; concurrency raises only with the limits.
- **Frontend**: sub-areas are tabs inside a project workspace, not separate sidebar entries (the sidebar has Development and Compute). Polling (3 s) only while a job is active; no live streaming of logs.
- **Orphan sweep from Phase 8 notes is still not done** for pre-existing Phase 7 residue.
- **A 1-second window** exists between revocation and a running job being stopped (the worker re-checks entitlement every second); new work is refused immediately.

## Defects found and fixed during the build (kept for the record)
`.git/config` could be saved as a project file (blocked now); deleting a project with a running job left the job's process alive and crashed the worker tick (now killed directly and the worker treats a vanished job folder as cancellation); docker mode put secret values in argv (now `-e NAME`); cancel from `running` skipped `cancelling`; `ru_maxrss` was misreported; 500s on empty request bodies; a job could read sibling projects' folders by host path in `local_trusted` (now covered by tmpfs, fail closed).
