# Phase 3 requirements register

Status key: **Done** = implemented and covered by an automated test that ran; **Done*** = Done, but part of the evidence is a stand-in or fake (see `TESTING.md` "Not executed"); **Deferred** = out of Phase 3 by instruction; **Open** = not complete.
Tests are named by file; "e2e" = the real-browser scripts. No item is marked Done without both.

| ID | Spec § | Requirement | Status | Evidence |
|---|---|---|---|---|
| P3-01 | 3 | Create / list / open / rename / describe projects | Done | test_workspace_projects, test_workspace_api, e2e stage 3 |
| P3-02 | 3 | Archive, restore, delete project (name confirmation; cascades to files) | Done | test_workspace_projects, test_workspace_governance, e2e |
| P3-03 | 3 | Project activity history | Done | test_workspace_projects, e2e |
| P3-04 | 3 | Owner-only isolation; foreign/missing/deleted ⇒ identical 404 | Done* | test_workspace_security_api, test_workspace_api (HTTP on stand-in) |
| P3-05 | 4 | Datasets: CSV, XLSX, XLS, ODS, JSON, XML | Done* | test_workspace_extraction; **.xls only the PARSER_UNAVAILABLE path** (xlrd absent) |
| P3-06 | 4 | Documents: PDF (text + OCR), DOCX, RTF, TXT, MD | Done | test_workspace_extraction; OCR on a real scanned PDF |
| P3-07 | 5 | Chunked, resumable upload; cancel; progress; checksum; duplicate handling | Done | test_workspace_uploads, frontend uploads.test.js, e2e |
| P3-08 | 5 | Server-side validation: size, extension + signature, traversal, ZIP bombs, XML entities, row/column/page caps | Done | test_workspace_uploads, test_workspace_extraction |
| P3-09 | 5/9 | Originals written once, never modified; opaque storage keys | Done | test_workspace_uploads, test_workspace_cleaning |
| P3-10 | 6 | Traceable extraction; nothing discarded silently; per-page OCR confidence | Done | test_workspace_extraction, e2e |
| P3-11 | 7 | Profiling with fact / heuristic / estimate labels, source version + timestamp, reproducible; no predictive modelling | Done | test_workspace_profiling, e2e |
| P3-12 | 8 | Cleaning: suggest → preview → explicit confirm → derived version; 8 specified rules (+1 extra operation) | Done | test_workspace_cleaning, e2e |
| P3-13 | 8 | Version history and rollback | Done | test_workspace_cleaning, e2e |
| P3-14 | 9 | Retention dates; expiry sweep; stale upload and preview cleanup | Done | test_workspace_governance (advanced clock); not observed live |
| P3-15 | 9 | Deletion workflow with completion evidence; retry of failed deletions; orphan sweep; interrupted-processing recovery | Done | test_workspace_governance |
| P3-16 | 9 | Privacy-safe logging (no content, no tracebacks with data) | Done | test_workspace_governance (safelog) |
| P3-17 | 9 | Footprint and inventory visible to the user | Done | test_workspace_governance, e2e |
| P3-18 | 9 | Account deletion removes project data | Done | test_workspace_governance |
| P3-19 | 10 | Standard: ≤3 documents, ≤100 pages each; Special unlimited; Owner per permissions | Done | test_workspace_uploads, test_identity_access |
| P3-20 | 10 | Reuse the existing entitlement system; revocation immediate | Done | test_workspace_uploads (revoke then upload) |
| P3-21 | 11 | Register, sign in, link, unlink, re-authenticate with Google and GitHub; all 10 required behaviours of §11 (rules in `SOCIAL_AUTH.md`) | Done* | test_social_auth (23), test_social_auth_api (7, stand-in), e2e stage 9; **fake provider only, never real Google/GitHub** |
| P3-22 | 11 | Keep Account → Connected accounts | Done | e2e, social.test.js |
| P3-23 | 11 | Apple sign-in | **Deferred** | Not introduced, per instruction |
| P3-24 | — | Magic-link sign-in (master spec identity item) | **Deferred** | Not part of Phase 3 |
| P3-25 | 11/F1 | Password-less (social-only) accounts can delete, re-authenticate, unlink safely, set a password | Done* | test_social_auth, e2e stage 9 |
| P3-26 | 12 | UI: dashboard, project workspace, upload, asset detail (preview, extraction, profile, cleaning, history, retention, storage), states for loading/empty/error, keyboard use | Done | frontend tests (88), e2e (75 + 47 steps) |
| P3-27 | 13 | Security test list | Done* | `TESTING.md` mapping; HTTP checks on stand-in |
| P3-28 | 14 | Backend + frontend + regression + end-to-end testing | Done* | `TESTING.md` |
| P3-29 | 15 | Constraints: Phase 1/2 preserved, 0001 untouched, no tests deleted, ₹0, no secrets, `.env.example` current | Done | test_repo_hygiene, test_config, MIGRATIONS.md |
| P3-30 | 17 | Production build | **Open (unverified)** | `vite build` could not run offline; esbuild bundle used instead |
| P3-31 | 17 | Real FastAPI run of the HTTP tests | **Open (unverified)** | stand-in only |
| P3-32 | 17 | Real Google/GitHub and real SMTP | **Open (unverified)** | need credentials and network |

## Incomplete or unverified (explicit list)
P3-23 and P3-24 Deferred. P3-30, P3-31, P3-32 need the user's own machine. `.xls` parsing, CI, macOS, live retention expiry unverified.

## Known limitations (not defects against the spec)
Processing runs synchronously inside upload completion, so a very large scanned PDF holds the request while OCR runs. Retention is per file (no project-wide policy or legal hold). Backups are outside the application. Hosted deployments must schedule `python -m app.cli purge`. After provider re-authentication the pending action is not resumed automatically (retry it). The "set a password" email reuses the reset-password subject line. An existing account is never auto-linked by email (deliberate; see `SOCIAL_AUTH.md`).
