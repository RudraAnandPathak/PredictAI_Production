# Monitoring guide

PredictAI reports what it can measure itself. It installs **no monitoring agent**, stores **no time series**, and sends **no alerts**. For alerting, point an external checker at the endpoints below.

## Endpoints
| Endpoint | Who | Meaning |
|---|---|---|
| `GET /api/v1/health` | anyone | Liveness: the API process answers. Checks no dependency. |
| `GET /api/v1/health/ready` | anyone | Readiness: schema current and the database answers. **503 `NOT_READY`** otherwise, with a generic message (no paths or driver text). |
| `GET /api/v1/owner/monitoring` | owner | A point-in-time snapshot (below). |
| `GET /api/v1/owner/overview` | owner | Counts only (accounts, content, jobs). Never titles or user content. |
| `GET /api/v1/owner/events` | owner | Owner actions in the audit trail. |

## Owner snapshot vocabulary
Each check is one of **`ok`**, **`degraded`**, **`stale`**, **`unavailable`**; `overall` is the worst one. **`unavailable` means "cannot be measured here", not "fine".**

| Check | Meaning | `ok` → other |
|---|---|---|
| `database` | `SELECT 1` answers | `unavailable` if not |
| `disk` | free space where files are stored | `degraded` below 15% free |
| `load` | 1-minute load average | `unavailable` where the OS has none |
| `cpu_memory` | **not collected** (always `unavailable`) | use OS tools |
| `training_jobs`, `research_jobs` | running jobs with a recent heartbeat | `stale` if a running job has no heartbeat for the stale window |
| `scheduler` | any active schedule overdue by more than 30 min | `stale` — means `backend/scripts/run_schedules.py` is not running |
| `deliveries` | permanently failed (dead-lettered) deliveries | `degraded` |

## What is not visible
Latency percentiles, error rates, request counts over time, per-provider uptime history, backup age and queue depth over time are **not recorded**. Backup freshness is checked by `python -m app.ops.backup list`. Logs go to stdout (`PREDICTAI_LOG_FORMAT=json` for structured lines); every response carries `X-Request-ID`, which also appears in error bodies and logs so a report can be traced.

## Suggested external checks (not shipped)
Poll `/health/ready` every minute (alert after 3 failures); watch free disk on the data volume; run `backup list` daily and alert if the newest backup is older than 26 h; run a restore drill monthly.

## Evidence
`test_p7_dashboard_owner` (honest monitoring, stale heartbeat, metadata-only boundary), `test_p11_failures` (readiness 503 with no leak, automatic recovery), `test_health_api`. The vocabulary above was read from `ownerops/service.py`, not assumed.
