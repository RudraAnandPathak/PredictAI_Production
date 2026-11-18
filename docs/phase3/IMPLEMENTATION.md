# Phase 3 implementation reference

Projects, data workspace (upload → extraction/OCR → profiling → previewable cleaning → version history), governance, and Google/GitHub registration and sign-in. Design decisions and the Stage 1 findings are in `PLAN.md`; governance in `GOVERNANCE.md`; social auth in `SOCIAL_AUTH.md`; migrations in `MIGRATIONS.md`.

## Architecture
Modular monolith, same layering as identity: thin routers (`app/api/v1`) → framework-free services → one `repository.py` per module holding all SQL.
```
app/modules/storage/      StorageBackend protocol + LocalStorage (opaque hex keys, containment checks, write-once originals)
app/modules/workspace/    projects, uploads, validation, formats, parsing/{xmlsafe,coltypes,canonical,datasets,documents,ocr},
                          extraction, profiling, cleaning(+cleaning_ops), assets, quota, governance, safelog, presenters, repository
app/modules/identity/oauth.py   extended: link | reauth | signin | register
app/api/v1/               projects.py, assets.py (+ uploads/cleaning routes), oauth.py extended
app/db/migrations/        0001_identity.sql (unchanged), 0002_workspace.sql
frontend/src/api/workspace.js, features/workspace/*, pages/projects/*, components/auth/{SocialButtons,ProviderReauth}.jsx
```
The workspace reuses the identity `Context`, `Principal`, audit log, rate limiter and entitlement system. Nothing about authentication, RBAC or quotas was duplicated.

## Endpoints added in Phase 3 (all under `/api/v1`; 74 routes in total, Phase 1/2 routes unchanged)
| Area | Routes |
|---|---|
| Projects | `GET/POST /projects`, `GET/PATCH /projects/{id}`, `POST /projects/{id}/archive|restore|delete`, `GET /projects/{id}/activity`, `GET /projects/{id}/assets` |
| Uploads | `GET/POST /projects/{id}/uploads`, `GET/DELETE /uploads/{id}`, `POST /uploads/{id}/chunks/{index}`, `POST /uploads/{id}/complete` |
| Assets | `GET/DELETE /assets/{id}`, `GET /assets/{id}/download|preview|structure|pages|extraction|history|footprint`, `GET/POST /assets/{id}/profile`, `PATCH /assets/{id}/retention`, `POST /assets/{id}/rollback` |
| Cleaning | `GET /assets/{id}/cleaning/suggestions`, `POST /assets/{id}/cleaning/preview`, `GET /cleaning/{tid}`, `POST /cleaning/{tid}/confirm|discard` |
| Workspace | `GET /workspace/quota`, `GET /workspace/inventory`, `GET /workspace/deletions` |
| Social auth | `GET /auth/oauth/available`, `POST /auth/oauth/{p}/signin|reauth`, callback dispatch on `GET /auth/oauth/{p}/callback` (plus Phase 2 list/link/unlink) |

Every state-changing route needs the CSRF header; every non-public route needs a session (`test_workspace_security_api` sweeps the live route table for both).

## Data model (0002)
`projects`, `project_events`, `assets` (dataset|document; status; checksum; size; format), `asset_versions` (original + derived; storage key; checksum; link to transformation), `uploads`, `upload_chunks`, `extractions`, `profiles`, `transformations`, `deletion_records`.
All reads are scoped by owner. A missing, foreign or deleted resource returns an identical 404.

## Workflows
* **Upload**: init (declared size/name, quota check) → chunks (resumable, per-chunk checksum) → complete (assemble, verify checksum/size, signature + extension check, ZIP-bomb limits, dedupe, write original once) → synchronous extraction/OCR → asset ready or failed with a reason code. Cancel and resume supported; stale uploads are swept by retention.
* **Extraction**: datasets → canonical table (typed columns, row/column caps); documents → pages with text, tables where detectable, OCR with per-page confidence for scanned PDFs. Nothing is dropped silently: skipped sheets/pages/rows are listed in the extraction record.
* **Profiling**: every statistic is labelled fact, heuristic or estimate, carries the source version and timestamp, and has a reproducible `content_hash`. No predictive modelling.
* **Cleaning**: 9 operations; suggest → preview (nothing written) → explicit confirm → new derived version; rollback restores a prior version as current; originals are never modified.
* **Governance**: deletion state machine with evidence, retention dates, expiry sweep, interrupted-processing recovery, orphan-object sweep, footprint/inventory (see `GOVERNANCE.md`).
* **Quota**: Standard users: at most 3 document assets and 100 pages per document; Special/Owner unlimited through the existing `uploads.unlimited` permission, evaluated per request so revocation is immediate.

## Configuration added
`PREDICTAI_STORAGE_DIR`, upload/extraction limits, `PREDICTAI_OCR_LANGUAGES`, `PREDICTAI_AUTO_MIGRATE`, `PREDICTAI_COOKIE_SECURE`, `PREDICTAI_SESSION_COOKIE_NAME`, `PREDICTAI_EMAIL_OUTBOX_DIR`, and the four OAuth client variables plus `PREDICTAI_OAUTH_REDIRECT_BASE_URL`. All are in `.env.example` (safe placeholders, secrets blank) and `config/env.production.example`; a test fails if code reads a variable the template omits.

## Optional system components (free)
`backend/requirements-data.txt` (pypdf, pdfplumber, xlrd, chardet) and the local programs `tesseract` and `pdftoppm` (poppler). Without one, the matching format is reported `PARSER_UNAVAILABLE` with an instruction; nothing fails silently.

## Operating
Schedule `python -m app.cli purge` (daily) wherever the API is hosted: it runs identity housekeeping and workspace retention. Back up the database file and the storage directory together.
