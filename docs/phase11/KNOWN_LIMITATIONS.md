# Known limitations (as of this release candidate)

Each item is something a reader could otherwise assume works. Detailed per-phase lists remain in `docs/phase7/KNOWN_LIMITATIONS.md`, `docs/phase8/KNOWN_LIMITATIONS.md`, `docs/phase9/KNOWN_LIMITATIONS.md`, `docs/phase10/STATUS.md`.

## Verification limits
- **Never run in the target environment:** real FastAPI/Uvicorn (tests use a stand-in over real Starlette/pydantic), `vite build` (esbuild bundles were used), any browser, macOS, Python 3.14, Docker, Ollama, SearXNG, Qdrant, real email/Slack, Google/GitHub OAuth, real PDF/Word/PowerPoint viewers.
- **No pilot with people. No accessibility audit in a browser. No load test beyond 8 clients. No DAST. No CVE audit** (`pip-audit`, `npm audit` need network). The tree is not a git checkout, so secret history was not scanned.

## Security and isolation
- **Untrusted multi-user code execution is not supported and must stay disabled.** `local_trusted` runs code under Linux namespaces/limits only; Docker and macOS isolation were never run or escape-tested.
- Backups are **not encrypted** and not off-site; they contain users' private data.
- Antivirus is a hook (tested with a fake engine); no real scanner is bundled.

## Operations
- No alerting, no metrics history, no log shipping, no CPU/memory collection; monitoring is an on-demand owner snapshot plus two health endpoints.
- Single process, SQLite, in-process job queues: the practical ceiling for concurrent users is unmeasured above 8; writes serialise.
- `app.__version__` is still `0.1.0`; no git commit is recorded in the manifest (no repository).
- No down-migrations; rollback is restore-from-backup, losing data written after the backup.
- Scheduled report delivery needs `backend/scripts/run_schedules.py` run externally (cron/launchd); the owner snapshot reports `stale` if it is not.

## Product
- Research Intelligence ingestion does **not** run OCR (a scanned PDF yields no text there); the Data Workspace has optional OCR that needs Tesseract installed. PDF tables are not extracted. Charts in reports are data, not rendered images.
- Answers without an AI model are extractive quotations; model-written answers were only exercised with fakes.
- Research quality on live sources (Wikipedia, arXiv, SearXNG, scholarly APIs) was tested only with recorded/canned payloads.
- Frontend never rendered in a browser here: layouts, the 8-section Research Intelligence UI and the new control-border colours are unseen. Large single bundle (264 KB gzip), no route code-splitting.
- Requirement register: 152 of 416 rows are "Implemented but partially verified" and 6 "not tested"; Priority and Dependencies were not recorded by earlier phases (`REQUIREMENTS_COVERAGE.md`).

## Accessibility
Static review and contrast only; screen-reader, keyboard, zoom, touch and chart-alternative behaviour untested (`ACCESSIBILITY_REVIEW.md`).
