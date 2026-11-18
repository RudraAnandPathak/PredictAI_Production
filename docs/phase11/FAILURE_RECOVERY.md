# Failure and recovery testing (brief Part 12)

Each failure is mapped to the **executed** test(s) that cover it. "Gap" means no automated test exists; the manual procedure is in `ENVIRONMENT_VERIFICATION.md`. A mocked failure proves the code reacts to that failure; it does not prove real infrastructure behaves the same way.

| Failure | Evidence (test file · name) | Result / gap |
|---|---|---|
| Invalid uploads | `test_workspace_uploads` · unsupported and disguised files rejected, nothing left behind; decompression bombs and unsafe paths | covered |
| Malformed PDFs | `test_workspace_extraction` · damaged files fail with a plain explanation and are kept; damaged PDF fails cleanly | covered |
| OCR failures | `test_workspace_extraction` · PDF with no text and no OCR fails with an explanation | covered; **OCR engine itself not exercised on bad scans here** |
| Database interruption | `test_p11_failures` · readiness 503 with no path/driver leak, liveness stays up, recovers without restart; a request needing the DB fails safely | covered (simulated) |
| Interrupted write | `test_p11_failures` · `transaction()` rolls back a half-done change; plain sessions are autocommit (documented) | covered; **not every multi-statement path was audited to use `transaction()`** (90 uses in 24 files) |
| Storage interruption | `test_p9_files_access` · quota blocks writes; `test_p11_backup` · unstable/missing file fails the backup and leaves nothing | partial; **disk-full / unmounted volume gap** |
| Queue interruption | `test_p10_ingestion` · cancel queued and running, deleted source cancels its queued work | partial; queue is in-process so a restart-mid-job path is only covered by the stale-heartbeat report |
| Worker crashes | `test_p10_security` · a crash is reported, not raised; `test_p7_dashboard_owner` · stale heartbeat reported; `test_p9_engine` · no orphan processes | covered |
| Provider timeouts | `test_p10_assistant` · provider failure falls back to quotes; `test_p10_discovery` · one failing provider does not hide the others; `test_identity_platform` · provider failures become a generic audited error | covered with fakes; **live timeouts untested** |
| Research-source errors | `test_p10_reasoning` · a source that stops being ready fails the analysis clearly; `test_p10_ingestion` · unknown DOI fails clearly without retrying | covered |
| Invalid model artifacts | `test_predictive_service` · tampered model files refused before being opened | covered |
| Export failures | `test_p10_reports` · missing library gives a clear 503 | covered |
| Permission revocation during an operation | `test_p7_schedule` · revoking access cancels pending deliveries; `test_p9_security` 13–14 · revocation stops new work and terminates active jobs; `test_identity_access` · special access revoked on the very next check | covered |
| Session expiration | `test_identity_auth` · idle timeout, absolute lifetime; `test_identity_api` · `SESSION_EXPIRED` | covered |
| Resource exhaustion | `test_analytics_sandbox` · memory-limit kill; `test_p9_engine` · process limit; `test_p9_security` 10, 18 · limits and quotas; `test_p10_security` · memory hog stopped | covered per process; **whole-host exhaustion untested** |
| Cancelled jobs | `test_p10_reasoning`, `test_p10_ingestion` · cancel before start / while running | covered |
| Interrupted migrations | `test_p11_release` · interrupted migration leaves the old version | covered |
| Failed backup | `test_p11_backup` · unstable file, damaged DB, missing DB all fail cleanly | covered |
| Failed restore | `test_p11_backup` · non-empty target, tampered backup, path-traversal manifest, foreign-key damage all refused/detected | covered |
| Rollback failure | `test_p11_release` · rollback drill succeeds; a rollback from a backup that fails verification is refused (`test_p11_backup`) | partial; a rollback that fails halfway on real hardware is untested |

## What the failure tests check (brief checklist)
Failures detected ✔ (error codes, 503/4xx envelopes) · errors understandable ✔ (plain messages asserted in the tests above) · partial work handled safely ✔ (atomic rename of backups, rollback of transactions, damaged uploads kept but flagged) · no sensitive leak ✔ (`/private/path` and driver text asserted absent) · resources cleaned ✔ (no orphans, `.partial-*` cleanup) · silent corruption ✘ **not exhaustively proven** (integrity/foreign-key checks run on restore; no continuous corruption detection) · recovery possible ✔ where supported (backup/restore, re-run jobs).
