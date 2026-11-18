# Phase 5 plan: Predictive Intelligence & AutoML

Starting point: the corrected Phase 4 integrated project (unchanged copy). Everything below is additive; Phase 1–4 behaviour is kept.

## What the inspection found (and what it means)

| Finding in Phase 4 | Decision for Phase 5 |
|---|---|
| Authorisation path `AssetService.get_owned` -> `DataViews._version`; foreign/missing/deleted resources are an identical 404 | Every new service entry point goes through the same path (`PredictiveService._open`). New tables carry `owner_user_id` + `project_id` and every query filters by owner. |
| Analysis engine is a closed vocabulary of operations; subprocess + watchdog + scrubbed env (`sandbox.run_limited`, `_boot.py`) | Training, assessment, prediction and explanation run as *tasks* in the same kind of subprocess (`predictive/worker.py`). `run_limited` gains an optional `cancel` callback (default off, so Phase 4 behaviour is identical). `_boot.py` gets an optional, allow-listed worker module argument. |
| Statistical machinery already exists (`hypothesis`, `relationships`) | The mixed-variable analysis is **one new registered operation (`mixed_association`)** that dispatches to those functions; the predictive workflow reuses the same pair-measure core. No test or effect size is re-implemented. |
| No job framework exists | New `JobRunner` (bounded thread pool, per-user and global concurrency, heartbeat, cancel, interrupted-job recovery at start-up). |
| Evidence contract + `result_hash` | Explanations are attached *beside* `data`, outside the result hash, so Phase 4 reproducibility is untouched. |
| Libraries available offline: scikit-learn 1.9.1, joblib, numpy, pandas, scipy. **No SHAP.** | scikit-learn only. SHAP is not claimed; local explanations use a labelled single-feature-replacement method. |

## Architecture

```
app/modules/explanation/   universal "Explanation" system (rules + number check + registry), used by analytics, insights, assistant, profiling and ML
app/modules/predictive/    prep.py problem.py catalog.py transformers.py metrics.py train.py explain.py predict.py worker.py
                           service.py repository.py jobs.py schemas/constants/errors
app/modules/analytics/ops/mixed.py   mixed_association operation (registered in ops/__init__.py)
db/migrations/0004_predictive.sql    ml_jobs, ml_models, ml_predictions
api/v1/predictive.py                 /api/v1/ml/*
frontend: pages/predictive/*, components/predictive/*, api/predictive.js, explanation component used by every result view
```

Key rules: preprocessing is a scikit-learn `Pipeline` fitted inside every cross-validation fold (no leakage); no target-aware feature selection exists; the delivered model is the one the reported test metrics describe; models are only ever unpickled from server-written storage keys, after a SHA-256 check, inside the isolated worker.

## Build order
1. Explanation system + mixed-variable analysis (+ wiring into Phase 4 outputs)
2. ML dataset preparation and problem detection
3. AutoML engine: candidates, validation strategies, HPO, evaluation, explainability, model storage, worker
4. Jobs, service, migration 0004, API, security (rate limits, audit, purge, quotas)
5. Frontend (10 interfaces, charts, Explanation panels) and tests
6. Regression across Phase 1–4, docs, package, verify
