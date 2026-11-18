# Installation and configuration

The canonical, tested quick-start is the **"Run locally"** section of the root `README.md` (Python ≥ 3.10, Node ≥ 20; `cp .env.example .env`; backend venv, `pip install -r requirements-dev.txt`, `python -m app.cli migrate`, `provision-owner`, `python -m app`; frontend `npm install && npm run dev`). This page adds only what Phase 11 introduced and the optional pieces.

## Optional dependency sets
| File | Enables | If missing |
|---|---|---|
| `requirements-data.txt` | Excel/ODS parsing, richer data work | those formats report PARSER_UNAVAILABLE |
| `requirements-research.txt` | Research Intelligence parsing/retrieval extras | documented fallbacks |
| `requirements-reports.txt` | PDF/DOCX/PPTX export | export returns a clear 503 naming the package; Markdown still works |
| `requirements-devspace.txt` | Development Workspace crypto helpers | workspace unavailable |
Every requirement line has an upper version bound (checked by `test_p11_security_scan`). `pip install` was **not** run in the build sandbox for these; install and test them on your machine.

## Configuration
All settings are `PREDICTAI_*` environment variables documented in `config/env.example` (kept identical to the root `.env.example`; a test guards this). Production requires a strong `PREDICTAI_SECRET_KEY` and an https `PREDICTAI_FRONTEND_BASE_URL`; start-up refuses otherwise. Settings are read once at start-up — restart after editing `.env`. Secrets live only in `.env` or your secret store, never in the repository or in backups.

## Operational commands
`python -m app.cli {migrate | list-owners | provision-owner | recover-owner | bootstrap-owner | purge | oauth-status}` · `python -m app.ops.backup {create|verify|restore|prune|list}` · `python -m app.ops.release {manifest|compat}` · `python scripts/package_release.py` · `python scripts/security_scan.py` · `python scripts/a11y_contrast.py` · `python scripts/run_pilot.py` · `python scripts/verify_phase10_live.py` · `python backend/scripts/run_schedules.py` · `python backend/scripts/run_dev_worker.py`.

Further guides: `docs/phase6/WEB_RESEARCH_SETUP.md` (SearXNG), `docs/phase9/INSTALLATION.md` (Development Workspace), `docs/phase7/INSTALL.md`.
