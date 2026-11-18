# Platform architecture status

What exists and is exercised, at module level. "Verified" means the named tests pass in the build sandbox (see `ENVIRONMENT_VERIFICATION.md` for what that does not prove).

```
Browser (React 19 + Vite SPA, 168 .jsx files)  ──/api/v1──►  FastAPI app (thin routers → services in app/modules/*)
                                                               │
              ┌────────────┬────────────┬───────────┬──────────┴───┬───────────────┬────────────┐
           identity    workspace    analytics   predictive    research (P6)   research_intelligence (P10)
           roles/RBAC  projects,    stats,      AutoML,       web, news,      workspaces, discovery, ingestion,
           sessions    files, OCR,  assistant   models,       sources         retrieval, reasoning, studio, reports
                       cleaning                 registry
              ├── intelligence / exam / reports / collaboration / schedule / ownerops (Phase 7)
              └── devspace (Phase 9: notebooks, envs, execution engine, git, pipelines — execution OFF by default)
                                   │
                          SQLite (WAL, 11 migrations) + local file storage (PREDICTAI_STORAGE_DIR)
                          in-process job workers (training, research, ingestion); optional external scripts (schedules, dev worker)
              ops: app/ops/backup.py, app/ops/release.py (this release round)
```

## Module status (from `REQUIREMENTS_REGISTER.md`)
Counts by phase and status are generated in `REQUIREMENTS_COVERAGE.md`. In short: Phases 1–8 are implemented and tested offline; Phase 9 (Development Workspace) is implemented with untrusted execution disabled and its isolation unverified beyond Linux; Phase 10 (Research Intelligence) is implemented backend, API and UI, with the UI never rendered in a browser and providers never reached live.

## Architectural facts that matter for operations
- One process serves the API; background work runs in that process's worker threads/subprocess children. Restart interrupts running jobs (reported stale, not resumed).
- Authentication is cookie sessions with a CSRF token; every route is covered by the route sweep.
- Persistence is a single SQLite file plus a storage directory; both must be backed up together.
- Configuration is environment variables (`config/env.example`); production start-up refuses weak settings.
- External services are optional and degrade to documented fallbacks (extractive answers, document-only research).

## Not part of this architecture today
Hosted deployment, container image, message broker, metrics/alerting stack, CDN, multi-node operation.
