# Release readiness gates

Assessed individually. Status words: **Passed · Passed with limitations · Blocked · Failed · Not tested.** A gate marked Blocked or Not tested is never reported as Passed. **No gate is plain "Passed"**, because every result was produced in the build sandbox (stand-in server, no browser, no Docker, no network, no macOS) and the target environment has not been tested — see `ENVIRONMENT_VERIFICATION.md`.

| # | Area | Status | Evidence | Why not a plain pass |
|---|---|---|---|---|
| 1 | Functionality | **Passed with limitations** | full backend regression and frontend tests (`VERIFICATION_REPORT.md`); scenarios A–F, H in `PILOT_RESULTS.md`; 416-row register | stand-in server; no browser run; live providers mocked |
| 2 | Performance | **Passed with limitations** | measured baseline + bottleneck analysis: `PERFORMANCE_BASELINE.md` | in-process, ≤ 8 clients, synthetic data; no real server, browser or large-data test |
| 3 | Accessibility | **Not tested** | static review + contrast checker + one fix (`ACCESSIBILITY_REVIEW.md`) | the brief requires a *completed audit*; keyboard, screen-reader, zoom, touch and chart alternatives were never run |
| 4 | Security | **Passed with limitations** | route sweep over 445 routes, scan 0 high/medium, one reproduced finding fixed with regression test (`SECURITY_REVIEW.md`) | no CVE audit, no DAST, no TLS/deployment review, no git-history scan |
| 5 | Identity | **Passed with limitations** | authentication, lockout, expiry, RBAC, revocation tests (`test_identity_*`, `test_p9_security` 13–14) | real SMTP and Google/GitHub OAuth unverified |
| 6 | Data | **Passed with limitations** | per-user isolation, deletion cascades, retention, integrity checks (`test_workspace_*`, `test_p8_deletion`, restore checks) | small synthetic data; no large-scale or corruption-detection test |
| 7 | AI and research | **Passed with limitations** | relevance, provenance (exact-quote citations), privacy, uncertainty tests incl. the Louis XVI corpus (`test_research_intelligence`, `test_p10_*`) | recorded/canned providers; no live web, no real model; answer quality not judged by people |
| 8 | Model execution | **Passed with limitations** | evaluation, leakage guards, tamper refusal, memory/time limits (`test_predictive_*`, `test_analytics_sandbox`) | subprocess limits only; large-data training not measured |
| 9 | Development | **Blocked** | 20 release-blocking tests pass for the Linux `local_trusted` layer (`test_p9_security`) | **Docker and macOS isolation never run; no hostile-code escape testing.** Untrusted multi-user execution stays **DISABLED**. |
| 10 | Storage | **Passed with limitations** | backup, verify, restore drill, secret exclusion, integrity (`BACKUP_RESTORE.md`, `test_p11_backup`, `test_p11_restore_drill`) | no encryption or off-site copy; not run on a real disk/Mac; disk-full path untested |
| 11 | Monitoring | **Passed with limitations** | health/ready, owner snapshot, honest "unavailable" vocabulary (`MONITORING.md`, `test_p11_failures`) | no alerting, history, or CPU/memory collection |
| 12 | Incident response | **Passed with limitations** | 14 runbooks, incident record template, severity (`RUNBOOKS.md`, `INCIDENT_RESPONSE.md`); recovery shown for DB failure, backup/restore, rollback, maintenance mode | runbooks were never exercised by a person on a real deployment; some steps are untested (marked) |
| 13 | Release | **Passed with limitations** | immutable-archive manifest with SHA-256, migration checksums, secret/database refusal, additive-migration check (`RELEASE_AND_ROLLBACK.md`, `test_p11_release`) | version still `0.1.0`; no git commit recorded; no CI pipeline run |
| 14 | Rollback | **Passed with limitations** | upgrade-then-rollback drill with data, files and re-upgrade (`test_upgrade_with_data_then_roll_back_by_restoring_the_pre_upgrade_backup`) | sandbox only; data after the backup is lost by design |
| 15 | Documentation | **Passed with limitations** | the 14 required documents exist in `docs/phase11/` and the README; a test checks structure and that no unimplemented capability is described as operational | install steps not validated on a clean machine |
| 16 | Pilot | **Not tested** | automated scenarios A–F, H pass with limitations; G blocked (`PILOT_RESULTS.md`) | **no pilot with people has been run** |

Totals: **Passed 0 · Passed with limitations 13 · Blocked 1 · Failed 0 · Not tested 2** (the table is authoritative; `test_p11_docs` recomputes this line).

## Release recommendations (separate decisions; none promotes the product automatically)
| Decision | Recommendation | Conditions / reasons |
|---|---|---|
| **Continued local development** | **Proceed** | nothing blocks it. Keep Development execution at `off` or `local_trusted` on your own machine only |
| **Restricted internal pilot** (3–10 trusted people, your Mac) | **Proceed with conditions** | (1) Development execution `off`; (2) run `pip-audit` and `npm audit` and record the result; (3) Mac steps 1, 3, 6, 7 of `ENVIRONMENT_VERIFICATION.md` recorded; (4) first backup restored successfully; (5) external `/health/ready` poll and daily backup; (6) participants told it is pre-release, no sensitive data |
| **Controlled external pilot** | **Not yet** | open: accessibility audit (gate 3), CVE audit, TLS/hosting verification (no layer-6 test exists), live-provider verification, a completed internal pilot with no open SEV1/SEV2, a chosen version/commit, encrypted off-site backups |
| **Public production release** | **No** | the above plus: verified isolation if any code execution is offered (gate 9), load testing on the real server beyond 8 clients, alerting, incident drills, privacy/legal review (not covered here) |

**A blocked security gate prevents any release that depends on that capability:** arbitrary untrusted multi-user code execution remains **disabled** in every recommendation above until isolation is verified in the actual target environment and the `test_p9_security` suite plus an escape test pass against it.
