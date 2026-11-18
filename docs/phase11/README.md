# Phase 11 — Pilot, hardening, security, performance, reliability and release readiness

**Numbering note.** The brief that asked for this work calls it "Phase 9". In this repository Phase 9 is the Development Workspace and Phase 10 is Research Intelligence, so the hardening round lives in `docs/phase11/` and covers Phases 1–10. Nothing from Phase 10 was started or removed by it.

**Read first:** `RELEASE_GATES.md` (the verdict), then `ENVIRONMENT_VERIFICATION.md` (what was and was not run, and your Mac procedure), then `KNOWN_LIMITATIONS.md`.

| Brief document | File |
|---|---|
| Platform architecture status | `ARCHITECTURE_STATUS.md` |
| Requirements coverage matrix | `REQUIREMENTS_REGISTER.md`, `REQUIREMENTS_COVERAGE.md` (`build_register.py`, `requirements_combined.json`) |
| Installation and configuration | `INSTALLATION_AND_CONFIGURATION.md` |
| Security review report | `SECURITY_REVIEW.md` |
| Performance baseline | `PERFORMANCE_BASELINE.md`, `perf_baseline.json` |
| Accessibility review | `ACCESSIBILITY_REVIEW.md` |
| Backup and restoration guide | `BACKUP_RESTORE.md` |
| Monitoring guide | `MONITORING.md` |
| Incident-response runbooks | `RUNBOOKS.md` (14), `INCIDENT_RESPONSE.md` |
| Release procedure / rollback procedure | `RELEASE_AND_ROLLBACK.md` |
| Controlled pilot guide | `PILOT_GUIDE.md`, results in `PILOT_RESULTS.md` / `pilot_results.json` |
| Known limitations / deferred decisions | `KNOWN_LIMITATIONS.md`, `DEFERRED_DECISIONS.md` |
| Failure and recovery testing | `FAILURE_RECOVERY.md` |
| Final verification report | `VERIFICATION_REPORT.md` |

New code: `backend/app/ops/backup.py`, `backend/app/ops/release.py`; scripts `security_scan.py`, `a11y_contrast.py`, `run_pilot.py`, `backend/tests/perf_baseline.py`; tests `test_p11_*`. Two product fixes: Development Workspace notebooks check permission before validating input; input/ghost-button/segmented-control borders now meet 3:1 contrast (`--control-border`).
