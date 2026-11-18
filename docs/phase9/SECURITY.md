# Security restrictions and what the isolation does and does not prove

**Do not approve production or public multi-user code execution on the basis of this release.** The isolation tests establish the boundaries below for **Linux namespaces in the build sandbox only**. The Docker boundary and the macOS boundary were not demonstrated.

## Established by tests (Linux sandbox, `tests/test_p9_security.py` etc.)
Special-access gate on every entry point; project ownership; owner has no implicit project access; no network from a job (socket, HTTP, DNS); scrubbed environment (no host secrets, no Docker socket path variable); other projects' folders not readable by path from a job; secrets only to the named job and redacted from logs, artifacts, events and platform logs; resource limits (CPU, memory, time, output, processes, storage); cancel/timeout kill the whole process group with no orphans; revocation blocks queued work and terminates running work; project/account deletion kills running jobs and removes rows and folders; webhook signature/replay checks; Git credentials never in files/config/logs; artifact and file permissions; storage quotas.

## NOT established
* **Filesystem isolation of `local_trusted` is incomplete**: the job's user can still read any other host file that user can read (home directory, `.env`, the database). Only sibling project folders are covered. Hence `multi_user_safe: false` in every provider's `describe()`.
* **Docker mode never ran.** **macOS `sandbox-exec`** never ran. **Jupyter / code-server** never ran (and are unsandboxed by design).
* Env builds and pushes/pulls use the network by design (documented, allow-listed).
* No kernel-level CPU/memory cgroup quota; limits are rlimits + supervised kill.

## Rules held
No arbitrary code in the API process; no plain subprocess presented as a multi-user sandbox; no host directory or Docker socket mounted into jobs; secrets are not logged; Git credentials are not stored in notebooks or project files; paid services not used.
