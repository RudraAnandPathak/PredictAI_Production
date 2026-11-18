# Runbooks (14)

Each: **Identify · Inspect · Limit impact · Recover · Verify**. Commands run from `backend/` unless stated. Never paste credentials, cookies, tokens or user content into tickets or chat; use request IDs (`X-Request-ID`) and error codes. These procedures were written from the code and configuration; the sandbox-reproducible parts were exercised by tests (cited), the rest were **not run on a real deployment**.

**Maintenance mode** (limit impact in many runbooks): owner → `PUT /api/v1/owner/maintenance` (needs recent sign-in and confirmation). See runbook 14.

---
## 1. Backend unavailable
- **Identify:** `GET /api/v1/health` fails or times out; users see network errors.
- **Inspect:** process running? port in `PREDICTAI_API_PORT` listening? last log lines (start-up errors name the missing setting; production refuses to start without a strong `PREDICTAI_SECRET_KEY` and an https `PREDICTAI_FRONTEND_BASE_URL`). Disk full? Free memory?
- **Limit:** tell users; nothing else to protect — the service is down.
- **Recover:** fix the named setting or free disk, restart (`python -m app`). If a new version caused it, roll back (`RELEASE_AND_ROLLBACK.md`).
- **Verify:** `/health` 200, `/health/ready` 200, sign in, open one project.

## 2. Database unavailable
- **Identify:** `/health` 200 but `/health/ready` returns **503 `NOT_READY`**; owner snapshot `database: unavailable`. (Tested: `test_p11_failures` — generic message, no path leak, recovers by itself when the database answers.)
- **Inspect:** DB file exists at `PREDICTAI_DATABASE_PATH`, readable/writable by the service user, disk not full, no stale lock holder, `PRAGMA integrity_check` on a **copy**.
- **Limit:** enable maintenance mode if writes might hit a damaged file. Do not delete `-wal`/`-shm` files by hand.
- **Recover:** if the file is intact, fix permissions/space and restart. If corrupt: **restore the newest verified backup into a new directory** (`BACKUP_RESTORE.md`), swap it in, keep the damaged files aside.
- **Verify:** `/health/ready` 200; `PRAGMA foreign_key_check` empty on the restored file; sign in; a project opens; owner snapshot `database: ok`.

## 3. Storage failure
- **Identify:** uploads or downloads fail; owner `disk` is `degraded`/`unavailable`; errors mentioning stored files.
- **Inspect:** free space and mount of `PREDICTAI_STORAGE_DIR`, permissions, whether the volume is attached.
- **Limit:** maintenance mode to stop new uploads if space is nearly out; free space by moving old backups off the volume or expanding it. (`python -m app.cli purge` deletes data that is **past its retention period** — it is not a way to reclaim space from current data; do not run it as a reflex.)
- **Recover:** restore the mount/space. If files are missing, restore the storage tree from the newest backup into a new directory and copy back **only the missing files**; the database and files must come from the same backup moment or references will dangle.
- **Verify:** upload a small file, download it, owner `disk: ok`; `python -m app.ops.backup create` succeeds.

## 4. Queue backlog
- **Identify:** training or research jobs stay `queued`; owner overview shows many running/queued jobs.
- **Inspect:** worker settings (`PREDICTAI_ML_WORKERS`, `PREDICTAI_RESEARCH_WORKERS`), whether jobs are actually running (heartbeat), host load.
- **Limit:** cancel clearly runaway jobs (`POST /ml/jobs/{id}/cancel`, `/research/jobs/{id}/cancel`); lower per-person limits in the owner quota policy.
- **Recover:** wait for drain, or raise workers if the host has capacity, then restart.
- **Verify:** queued count falls; a new small job starts promptly.
- **Note:** queues are in-process and SQLite-backed; there is no separate broker to inspect.

## 5. Worker crash
- **Identify:** owner snapshot `training_jobs`/`research_jobs` = **`stale`** (a running job with no heartbeat for 15 min); users see a job stuck "running".
- **Inspect:** job row status and heartbeat; logs around the request ID; host memory (OOM kills).
- **Limit:** cancel the stuck job so quota is released.
- **Recover:** restart the service; stale jobs are reported rather than silently resumed — re-submit the job. A killed child that exceeded its memory limit is reported as failed, not retried (`test_analytics_sandbox`, `test_p9_engine`: no orphan processes).
- **Verify:** snapshot returns to `ok`; re-run completes.

## 6. AI provider unavailable
- **Identify:** assistants fall back to extractive quotes or say no model is available; discovery lists the failing provider (`test_p10_assistant`, `test_one_provider_failing_does_not_hide_the_others`).
- **Inspect:** `PREDICTAI_AI_PROVIDER`, `PREDICTAI_AI_BASE_URL`, is Ollama running (`ollama list`), model pulled, timeout setting.
- **Limit:** none needed — fallbacks are by design; tell users answers are quotation-only meanwhile.
- **Recover:** start Ollama / correct the URL / pull the model; restart if settings changed (settings are read once at start-up).
- **Verify:** an assistant question returns a model answer with verified citations.

## 7. Web search unavailable
- **Identify:** research reports sources missing or "web off"; discovery shows provider errors.
- **Inspect:** `PREDICTAI_RESEARCH_WEB_ENABLED`, `PREDICTAI_SEARXNG_URL` reachable, outbound network, provider rate limits (per-person limits exist).
- **Limit:** none needed — research continues with uploaded documents and datasets and says what was not searched.
- **Recover:** restore SearXNG/network; clear nothing by hand (the cache expires on its own).
- **Verify:** `scripts/verify_phase10_live.py` on the Mac, or run a discovery search.
- **Not tested:** live providers were never reachable from the build sandbox.

## 8. Failed report export
- **Identify:** export returns an error; a missing library returns **503** naming the package (`test_a_missing_export_library_is_a_clear_503_not_a_crash`).
- **Inspect:** error code in the response; `backend/requirements-reports.txt` / `requirements-research.txt` installed in the running environment.
- **Limit:** Markdown export needs no library — offer it meanwhile.
- **Recover:** install the named package in the service's environment, restart.
- **Verify:** export PDF, DOCX, PPTX, Markdown from the same report and open each.

## 9. Failed model execution
- **Identify:** a training job ends `failed`; prediction refuses with a plain message (tampered/missing model file is refused before opening: `test_tampered_model_files_are_refused_before_they_are_ever_opened`).
- **Inspect:** the job's stored error, dataset suitability notes, memory/time limits, whether the model file's checksum still matches.
- **Limit:** cancel repeats; do not re-use a model whose file fails its checksum.
- **Recover:** fix the data issue and retrain; for a damaged model file restore it from backup or retrain.
- **Verify:** training completes, evaluation shown, one prediction names its model version.

## 10. Failed backup
- **Identify:** `python -m app.ops.backup create` exits non-zero ("file kept changing", "database integrity failed", "missing database"); newest backup older than expected.
- **Inspect:** the printed error; free space at `--out`; paths given; `PRAGMA integrity_check` on the live DB (via a copy).
- **Limit:** make a manual copy of the data directory while the app is stopped if no good backup exists.
- **Recover:** fix the cause and re-run; a failed run leaves nothing that looks valid (`.partial-*` is cleaned by `prune`).
- **Verify:** `backup verify` passes on the new backup; run a restore drill into an empty directory.

## 11. Failed restoration
- **Identify:** `restore` refuses (non-empty target, failed verification, integrity or foreign-key check, manifest path outside backup).
- **Inspect:** the error; `backup verify` on the same backup; try the next-older backup. **Never** point restore at live data; it refuses non-empty targets.
- **Limit:** leave the damaged system untouched until a restore verifies.
- **Recover:** use the newest backup that verifies; if none does, escalate as SEV1 — keep every copy.
- **Verify:** restored copy passes integrity and foreign-key checks, app starts against it in a scratch location, a project opens.

## 12. Suspected unauthorized access
- **Identify:** unexpected sign-ins/role changes in the audit trail, owner events you did not make, reports of another person's data, a dependency advisory affecting the running version.
- **Inspect:** owner `events`, audit log (security events are recorded, carry no content), sessions of the affected account.
- **Limit:** maintenance mode; revoke the account's sessions and special access (revocation takes effect on the very next check: `test_revoking_special_access_takes_effect_on_the_very_next_check`); disable the account; rotate `PREDICTAI_SECRET_KEY` and provider keys if exposure is possible (everyone signs in again).
- **Recover:** reset passwords via the normal flow; `python -m app.cli recover-owner` if the owner account is affected; patch the vulnerable component (`SECURITY_REVIEW.md`); restore data only if it was changed.
- **Verify:** audit trail quiet, affected user can access only their own data, route sweep (`test_p11_security_sweep`) green on the patched build.

## 13. Development sandbox failure
- **Identify:** dev jobs fail, hang, or the environment build fails; owner shows stale dev jobs.
- **Inspect:** `PREDICTAI_DEV_EXECUTION` setting, job state, environment lockfile/audit result, quota usage.
- **Limit:** **set `PREDICTAI_DEV_EXECUTION=off`** and restart — that disables execution for everyone and is always safe. Untrusted multi-user execution must remain off unless isolation has been verified in the target environment (it has not — see `RELEASE_GATES.md`).
- **Recover:** cancel stuck jobs; rebuild the environment; investigate the job's stored log.
- **Verify:** a trivial job completes within limits; no orphan processes.

## 14. Maintenance mode activation
- **Identify (when to use it):** before risky maintenance (restore, migration), or to stop writes during an incident (runbooks 2, 3, 12). Check whether it is already on: owner `GET /api/v1/owner/maintenance`; other accounts see a 503 `MAINTENANCE` on writes.
- **Inspect:** the current state and notice; who changed it and when (owner `events`: `maintenance_on`/`maintenance_off`); that your owner session is recent (turning it on needs recent sign-in).
- **Limit:** this *is* the impact-limiting step. Turning it on needs explicit confirmation: `PUT /api/v1/owner/maintenance` with `{"active": true, "notice": "Back at 10:00", "confirm": true}`. Reads continue; writes by every non-owner account are refused with the notice; owners can still make changes; signing out always works (`deps._maintenance_guard`; `test_p11_failures.MaintenanceMode` over HTTP, `test_p7_dashboard_owner`).
- **Recover (leave it):** do the work, then `PUT` with `{"active": false, "confirm": true}`. If the database is down you cannot toggle it — it is a flag in the database — use runbook 2 first.
- **Verify:** as a normal account, a write is refused with the notice while it is on, and succeeds after it is off; `/health/ready` is 200; the owner events show both changes.
