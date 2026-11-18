# Phase 10A — monitoring, forecasting, ensembles, what-if, magic link

Built from the gaps found by the master-blueprint audit (`BLUEPRINT_GAP_ANALYSIS.md`). Everything extends an existing module; no second AutoML engine, report engine or assistant was created. Phase 11 has not begun. Requirement rows: `PHASE10A_REQUIREMENTS.md` (10A-01 to 10A-13).

## What was built
| Capability | Where | Notes |
|---|---|---|
| Input drift | `backend/app/modules/monitoring/drift.py`, `service.py` | PSI (training-quantile bins) and KS for numbers; PSI and unseen categories for categories. Bands 0.10 / 0.25 are a rule of thumb and the response says so. Under 30 usable values on either side gives `insufficient_data`, never a figure. |
| Outcome-based performance | same | The owner records the real outcome for a stored single prediction. Judged only with at least 30 outcomes and a recorded hold-out score: accuracy 10 points below, or MAE 1.5 times above, is flagged as a prompt to look, not proof. |
| Forecasting | `predictive/forecast.py` (maths), `forecasting/service.py` | Six methods (repeat last value, repeat last season, straight line, exponential smoothing, damped trend, linear trend plus seasonal pattern), rolling-origin backtest (up to 5 folds), best by mean MASE. Intervals from backtest errors with the coverage actually observed. Series under 12 periods is refused; under 2 folds is not backtested and says so. |
| Ensembles | `predictive/catalog.py` | `voting_ensemble` and `stacking_ensemble`, built only from the existing closed list of scikit-learn models. Opt-in (`auto=False`): automatic runs are unchanged. No tunable parameters. Importance comes from permutation importance. |
| What-if | `PredictiveService.what_if` | Up to 5 scenarios through the normal prediction path; nothing saved; says it shows model behaviour, not real-world effect. |
| Magic link | `identity/authentication.py` | Verified mailbox and active account only; 15 minutes; single use; token hashed at rest; same answer for any address; limits 3 per address and 10 per IP per hour; audit events `login.magic_link_*`. Link carries the token in the URL fragment and is consumed by POST. |
| Migration | `0012_model_monitoring.sql` | New `mon_checks` and `mon_outcomes` only. |

## API (all under `/api/v1`, CSRF enforced)
- `GET /registry/versions/{id}/monitoring` summary
- `POST|GET /registry/versions/{id}/monitoring/drift` run or list checks; `GET /monitoring/checks/{id}`
- `POST /registry/versions/{id}/monitoring/outcomes`; `GET /registry/versions/{id}/monitoring/performance`
- `POST /assets/{id}/forecast` body: `date`, `measure`, `agg`, `freq`, `horizon` (1 to 24)
- `POST /ml/models/{id}/what-if` body: `values`, `scenarios` (1 to 5, each with `changes`)
- `POST /auth/magic-link/request` (always 202), `POST /auth/magic-link/consume`

## Interface
Registry version card (owner): Monitoring panel. Predictions: Forecast page (`/predictions/forecast`) and a "What if…" tab on each model. Sign-in: "Email me a sign-in link" (`/magic-link/request`, `/magic-link`).

## Tests (counts are from executed runs)
Full backend regression: 2014 tests, OK (0 failed, 0 skipped; 846 s). Frontend: 285/285. Security scan: 0 high, 0 medium. Contrast check: 74/74. Register is current (429 rows).

`test_p12_monitoring` 13, `test_p12_forecast` 13, `test_p12_ensembles` 5, `test_p12_whatif` 4, `test_p12_magic_link` 9; frontend `monitoring.test.js` 6, `magiclink.test.js` 2. Existing tests changed because migration 12 and the public magic-link routes are real changes: hard-coded migration lists now include 12 (the Phase 10 upgrade tests pin migration 11 so they keep their meaning, and a new test proves 12 is additive), and the two anonymous-route allow-lists name the two magic-link routes. No test was deleted or weakened. Mutation checks: loosening the PSI threshold, softening outcome corruption and dropping the verified-mailbox rule each made tests fail.

## Limitations (stand as stated)
- Monitoring runs only on request. There is no background watcher, alerting or automatic retraining (10A-12).
- Drift reads a sample of at most 20,000 rows per dataset. Date and free-text inputs are listed as not compared.
- Forecasting is in-process numpy (bounded: 1,000 periods, 24 steps). Methods are classical and simple; there is no external regressors, holiday or event handling. Intervals can be too narrow with few folds; the observed coverage is printed beside them.
- Ensembles use a fixed member list; they are slower to train and cannot be tuned.
- SHAP is not available (library not installed). Database and warehouse connectors are not built.
- Magic link: email delivery is verified only through the in-memory outbox. With `PREDICTAI_REQUIRE_EMAIL_VERIFICATION` off, no account has a verified mailbox, so no link is sent; this is deliberate. Mailbox compromise gives sign-in, as it already does for password reset. There is no second factor.
- The new screens have never been rendered in a browser; accessibility of them is unaudited.
- Everything else in `docs/phase11/RELEASE_GATES.md` still applies: nothing here was run on real FastAPI/Uvicorn, with Docker, or against live providers.
