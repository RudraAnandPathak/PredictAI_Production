# Phase 10A — Requirements & status
Scope: gaps found by the master-blueprint audit (`docs/phase12/BLUEPRINT_GAP_ANALYSIS.md`), built inside the existing modules (no second AutoML, report or assistant engine). Phase 11 is not begun.

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 10A-01 | Model monitoring: input drift (PSI, KS, categorical shift) of a registered version against its training data; stored checks | Implemented, tested | test_p12_monitoring |
| 10A-02 | Concept drift from owner-recorded outcomes only; judged only with at least 30 outcomes and a recorded hold-out baseline | Implemented, tested | test_p12_monitoring |
| 10A-03 | Monitoring is owner-only, same-project, never compares another user's data | Implemented, tested | test_p12_monitoring |
| 10A-04 | Migration 0012 is additive (new mon_* tables only) | Implemented, tested | test_p11_release, test_p11_backup |
| 10A-05 | Forecasting with six transparent methods, rolling-origin backtest, intervals with observed coverage, seasonality test, limits stated | Implemented, tested | test_p12_forecast |
| 10A-06 | Voting and stacking ensembles in the existing catalogue; opt-in; not part of automatic selection | Implemented, tested | test_p12_ensembles |
| 10A-07 | What-if scenario comparison through the normal prediction path; nothing saved | Implemented, tested | test_p12_whatif |
| 10A-08 | Magic-link sign-in: verified mailbox only, 15 minutes, single use, no enumeration, rate-limited, audited | Implemented, tested; real email delivery UNVERIFIED | test_p12_magic_link |
| 10A-09 | UI for monitoring, forecast, what-if and magic link | Implemented, tested (formatting logic and bundle); never rendered in a browser | monitoring.test.js, magiclink.test.js |
| 10A-10 | SHAP values | Not implemented: the library is not installed and cannot be installed offline | — |
| 10A-11 | Database and warehouse connectors | Not implemented: not requested as a build item; needs a design decision | — |
| 10A-12 | Scheduled or background drift monitoring and automatic retraining | Not implemented by design: monitoring runs only on request; no model is replaced automatically | — |
| 10A-13 | Apple sign-in | Deferred: no provider credentials to verify against | — |
