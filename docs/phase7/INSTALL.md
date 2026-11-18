# Phase 7: install and run

Phase 7 adds no required dependency. Everything below is free and open source.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-data.txt        # Phase 3 parsers (optional)
pip install -r requirements-reports.txt     # Phase 7 export formats: PDF, DOCX, PPTX, XLSX, PNG (optional)
python -m app.cli migrate                   # or start the API with auto-migrate on; applies 0006_phase7.sql once, never touches existing rows
```

Without `requirements-reports.txt` the app still runs: Markdown and CSV exports work, and the other formats answer "this export format is not available on this server" with the package name to install.

## Scheduled reports
Add one line to cron (or a launchd job on macOS) so due schedules and retries run:

```
*/5 * * * *  cd /path/to/PredictAI/backend && .venv/bin/python scripts/run_schedules.py
```
The script is safe to run from several places at once (runs and deliveries carry idempotency keys) and does nothing while maintenance mode is on. Without it, schedules are created but never run.

## Optional delivery
Email: set `PREDICTAI_EMAIL_BACKEND=smtp` and the SMTP settings. Slack: set `PREDICTAI_P7_SLACK_WEBHOOK_URL` to an incoming-webhook URL. Until then both are shown as unavailable.

## Frontend
```bash
cd frontend && npm ci && npm test && npm run build
```
No new npm dependency was added.

## Tests
```bash
cd backend && PYTHONPATH=.:tests python -m unittest discover -s tests
```
