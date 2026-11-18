# Execution and jobs

## Modes (`PREDICTAI_DEV_EXECUTION`)
| Mode | What it is | Status |
|---|---|---|
| `off` (default) | Nothing executes. Files, history, records and the UI still work. | Verified |
| `local_trusted` | Child process of the worker, scrubbed environment, private job folder, kernel resource limits, supervised kill. Linux: `unshare` user+net+pid+mount namespaces, other projects' folders covered by empty tmpfs (fails closed). macOS: `sandbox-exec` network-deny profile. **One trusted person on their own machine. Not a multi-user sandbox. Refused when hosted.** | Linux namespaces **verified in the build sandbox**; macOS `sandbox-exec` **UNVERIFIED** (deprecated by Apple) |
| `docker` | `docker run --network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit --memory --user 65534`, only the job's own directories mounted, secrets passed as `-e NAME` (never in argv), Docker socket never mounted. | Command construction tested; **never executed (no Docker daemon in the build sandbox): UNVERIFIED** |

If network isolation cannot be enforced the job is **blocked, not run**.

## Running
`POST /dev/projects/{id}/jobs` with `kind` = `script | notebook | command`, `entry`, optional `env_id`, `secret_refs`, `limits` (can only be **lowered** below the effective limit), `max_attempts`. Start the worker: `python backend/scripts/run_dev_worker.py` (`--once` drains the queue). Without a worker jobs stay `queued`, and the page says so.

## Guarantees tested
Exit codes and error classes (runtime/syntax/missing dependency), wall-clock, CPU, memory, process-count, output flood, storage caps, cancel (via `cancelling`), timeout, no orphan processes, dead-worker recovery with bounded retry and dead-lettering, queue depth and daily budget, fair queue, secrets reach only the named job and are redacted from logs and artifacts, data copies are read-only and project-scoped.
Job logs are redacted of secret values before storage. Artifacts are content-addressed (sha256) with provenance (job, commit/tree hash, environment version) and re-verified on read.
