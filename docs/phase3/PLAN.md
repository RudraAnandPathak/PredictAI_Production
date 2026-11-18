# Phase 3 plan — Projects, Data Workspace, Governance, Social Authentication

Baseline: Integrated Phase 1 + Phase 2 (170 backend tests, 2 skipped without FastAPI; 47 frontend tests; all green at the start of Phase 3).
Stages are completed in the order below. Nothing is marked complete without implementation and test evidence (see `REQUIREMENTS_REGISTER.md`).

## Stage 1 findings (from the real code)

| # | Finding | Impact | Decision |
|---|---------|--------|----------|
| F1 | **Phase 2 defect:** `confirm_password`, `request_recovery`, `change_password`, `reauthenticate`, `delete_account` and OAuth `unlink` all require a *password identity*. An account created through Google/GitHub has none. | Social-only users could not delete their account, re-authenticate for step-up, or recover access. Blocks Stage 9. | Fix in Stage 9: let an active, email-verified account without a password create one through the existing emailed reset-token flow ("Set a password"); `confirm_password` for password-less accounts is satisfied by a fresh provider sign-in (re-auth through the provider) — see Stage 9 design. Existing behaviour for password accounts is unchanged. |
| F2 | OAuth is **link-only** and its callback requires an existing session (`api/v1/oauth.py`). | Cannot register or sign in with a provider. | Extend `OAuthService` (same PKCE/state/token storage/http client); add `login`/`register` intents that bind state to *no user*. No parallel architecture. |
| F3 | No object-storage abstraction exists. | Needed by §9. | New `modules/storage` (protocol + local backend, opaque keys, containment check). |
| F4 | `delete_account` removes identities/sessions but knows nothing about project data. | Orphaned files would violate §9. | Account deletion calls the workspace purge for the user (Stage 8). |
| F5 | The API client is JSON-only with an 8 s default timeout; CORS allows `POST` but not `PUT`. | Uploads need progress, cancel and large bodies. | Chunked upload over `POST` with `application/octet-stream` bodies; frontend upload uses XHR (progress/abort) with the same CSRF header. Client gets a `csrfToken()` accessor; no CORS method change. |
| F6 | Permission `uploads.unlimited` already exists for Special/Owner. | Quota system already exists. | Reuse it; no new entitlement system. |
| F7 | `requirements.txt` has only fastapi, uvicorn, python-dotenv. | Parsers/OCR need libraries. | Stdlib-first parsers (csv/json/xml via `defusedxml`-style safe parsing, DOCX/XLSX/ODS via `zipfile`+safe XML); **optional** extras file `requirements-data.txt` (pypdf, pdfminer.six/pdfplumber, pytesseract, Pillow, xlrd, pandas not required). Each parser reports `PARSER_UNAVAILABLE` clearly if its optional library/binary is missing. |
| F8 | Master spec lists Apple OAuth and magic links under identity. | Phase 3 forbids adding Apple. | Kept in the register as **Deferred (out of Phase 3 scope by instruction)**. |

No other Phase 1/2 defect blocks Phase 3.

## Architecture

Modular monolith. New module `app/modules/workspace` beside `identity`, same layering (thin routers → framework-free services → one `repository.py` holding all SQL). It reuses the identity `Context` (connection, settings, clock, audit, rate limiter) and `Principal`; it does not duplicate authentication, RBAC or auditing.

```
app/modules/storage/      base.py (StorageBackend protocol), local.py (LocalStorage), errors
app/modules/workspace/    constants, errors, repository, projects, uploads, assets, quota,
                          parsing/{detect,datasets,documents,ocr}, extraction, profiling,
                          cleaning, governance, container
app/api/v1/               projects.py, assets.py, uploads.py  (+ oauth.py extended)
app/db/migrations/        0002_workspace.sql  (0001 untouched)
```

### Data model (migration 0002, D1-compatible SQL)
`projects`, `project_events` (activity), `assets` (kind dataset|document, status, source checksum, size, detected format, parent/derived links), `asset_versions` (original + derived versions, storage key, checksum, transformation link), `uploads` + `upload_chunks` (resumable sessions), `extractions` (text/pages/tables/OCR confidence per page), `profiles` (per asset version, reproducible), `transformations` (operations, preview, who, when, status), `deletion_records` (tombstones with completion evidence), `oauth_pending`-style state reuse via existing `auth_tokens`.

### Key rules
- Every query is scoped by `owner_user_id`; a project or asset that is not the caller's is indistinguishable from a missing one (404). IDs are random 128-bit, never sequential.
- Originals are written once to opaque keys (`u/<hash>/<id>/original`) and never opened for write again; derived versions are new objects. User filenames are metadata only and are sanitised for display and download headers.
- Server-side validation: size cap per request and per file, extension *and* content signature checks, ZIP-bomb limits (entry count, uncompressed size, ratio) for OOXML/ODS, XML parsed with entities/DTD disabled, page caps, row/column caps.
- Quota (Standard: 3 document assets per user, ≤100 pages each) checked in the service under a transaction; Special/Owner bypass via the existing `uploads.unlimited` permission read per request, so revocation is immediate.
- Audit/log metadata contains IDs, sizes, counts and reason codes only — never file contents or extracted text.

### Dependency policy
Free/open-source only; no paid APIs; no service that can bill. Heavy libraries are optional and detected at runtime. Tesseract is a local binary and optional.

## Stages (all ten complete; see REQUIREMENTS_REGISTER.md and TESTING.md)
2. Migration + storage + repositories + project service (+ tests)
3. Project APIs + frontend (dashboard, create, workspace shell, archive/restore/delete, activity)
4. Upload pipeline (init/chunk/complete/cancel/resume, checksum dedupe, validation, quota) + UI
5. Extraction + OCR (datasets and documents), asset details/preview APIs
6. Profiling (facts vs. estimates, reproducible)
7. Cleaning previews, derived versions, history, rollback
8. Governance: retention metadata, deletion workflow, storage cleanup, privacy-aware logging
9. Google/GitHub login + registration; password-less account support (F1)
10. Security/regression/e2e verification; docs; final report. **Stop; no Phase 4.**

## Verification honesty
The sandbox has no network, so real FastAPI, `vite build`, real SMTP and real Google/GitHub cannot be run. As in earlier phases: tests run against the real Python code with a Starlette/pydantic-based stand-in for FastAPI, an esbuild bundle replaces `vite build`, and provider calls use a fake HTTP client. Every result in the final report is labelled accordingly.
