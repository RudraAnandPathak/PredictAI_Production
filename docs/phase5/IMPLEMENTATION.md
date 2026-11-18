# Phase 5 — Predictive Intelligence & AutoML: implementation

Phase 5 adds machine-learning preparation, problem detection, automated model training and comparison, evaluation,
explainability, single and batch prediction, model management, and a universal **Explanation** section for every result in
the application. It extends the corrected Phase 4 project; nothing from Phases 1–4 was removed or weakened.

## Architecture

```
frontend  pages/predictive/*  →  api/predictive.js  →  /api/v1/ml/* (api/v1/predictive.py, thin router + CSRF)
backend   modules/predictive/
            service.py      PredictiveService   authorise → validate → (queue | run) → store → present
                            (reuses Context / Principal / AuditLog / RateLimiter; same authorisation path as Phase 4)
            jobs.py         JobRunner           bounded thread pool, per-user + global concurrency, DB-flag cancellation,
                                                heartbeat, interrupted-job recovery at start-up
            tasks.py        assess | train | predict | predict_batch | pdp   (the only tasks a worker will run)
            worker.py       runs one task in the isolated process; _boot.py applies the OS limits first
            prep.py         dataset preparation: roles, missing values, duplicates, outliers, leakage, imbalance, suitability
            problem.py      binary / multiclass / regression detection with a stated reason; overrides validated
            catalog.py      the closed list of algorithms, options and metrics (scikit-learn only)
            pipeline.py     Pipeline([SchemaCoercer, ColumnTransformer, estimator]) — fitted inside every CV fold
            validation.py   holdout, k-fold, stratified, repeated; seeds; learning curve
            train.py        candidates → CV ranking → optional tuning → baseline → refit → report
            metrics.py      per-problem metrics, curves, overfitting verdict
            explain.py      permutation + native importance, partial dependence, local one-input-at-a-time effects
            predict.py      single + batch prediction with input checks and out-of-range notes
            repository.py   ml_jobs, ml_models, ml_predictions (every query filters by owner)
          modules/explanation/   universal Explanation mechanism (core.py) + analytics.py + predictive.py rules
          modules/analytics/ops/mixed.py   the mixed-variable association operation (registered like any other)
          db/migrations/0004_predictive.sql
```

### Rules the design follows
- **Authorisation.** Every entry point goes through `AssetService.get_owned` → dataset-version lookup (the Phase 4 path). A
  missing, foreign or deleted dataset, job, model or prediction returns the identical 404. Model and prediction tables carry
  `owner_user_id` and `project_id`; every query filters on the owner.
- **No leakage.** Imputation, encoding, scaling and rare-category grouping are steps of one scikit-learn `Pipeline` that is
  fitted on the training portion of each fold only. There is no target-aware feature selection anywhere. Rows with an unknown
  target are excluded and counted. Test-set scores are reported but never used to rank or tune.
- **The delivered model is the one that was evaluated.** The model fitted on the training split is saved; its test metrics are
  the ones shown.
- **Closed vocabulary.** A request can only pick from the catalogue (algorithms, options, metrics). No expression, code,
  module path or file path from a request is ever evaluated or opened. A static test greps the package for `eval`, `exec`,
  shell and network use.
- **Isolation.** Each task runs in a separate process (`python -I _boot.py …`) with a scrubbed environment, a private
  temporary copy of the table, CPU/memory/file limits, a wall-clock budget and a resident-memory watchdog. It can be cancelled.
  The worker cannot see application secrets.
- **Safe model storage.** A model is a pickle written only by the server under a server-generated storage key, recorded with
  its SHA-256. It is verified before every load and loaded only inside the isolated worker. A mismatch returns `409
  MODEL_INTEGRITY`. Nothing a user uploads is ever unpickled.
- **Reproducibility.** Fixed seeds (default `20240601`), stored configuration, library versions and `ML_VERSION` with every
  model; a model's full report can be re-read at any time.
- **Original data is never changed.** Preparation is a description of what a pipeline will do; the stored dataset version is
  read-only.

## The Explanation section (permanent, app-wide)

`modules/explanation/core.py` provides one reusable mechanism. A producer states each sentence with `Say`, registering every
number it cites; a number-token scan rejects any figure in the text that was not registered from a computed value. The
result is a payload titled exactly **Explanation** with `status` `available` or `unavailable` (with a reason — never blank).

- Computed on read from stored results; never stored, so wording can improve without migrating data.
- Attached **beside** `data`, outside the Phase 4 `result_hash`, so Phase 4 reproducibility is untouched.
- Wired to every existing result kind: analytics results (`attach_to_run`), insights, assistant answers, data profiles — and to
  14 predictive kinds (`ml:assessment, problem, features, ranking, baseline, evaluation, importance, overfitting,
  learning_curve, hpo, prediction, batch, pdp, model_compare`).
- Frontend: one component, `components/ui/Explanation.jsx`, used by every result view; a pure module
  (`features/explanation/explanation.js`) decides the available/unavailable view. A frontend test asserts that each result
  view source renders `<Explanation value={…}`.
- It never states causation. Correlation, importance and partial-dependence explanations say what the model relies on, not
  what causes an outcome.

## Mixed-variable analysis (§8)

One registered operation, `mixed_association`, dispatches to the existing Phase 4 functions (correlation, association,
group differences, tests). Numeric–numeric, categorical–categorical and numeric–categorical pairs each use the appropriate
measure with an effect size and an interval; missing data is handled per pair and stated; no statistic is re-implemented.

## Backend API (`/api/v1`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/ml/catalog` | algorithms, options, metrics, limits, what explainability is included |
| GET | `/assets/{id}/ml/schema` | columns of a dataset and which can be predicted |
| POST | `/assets/{id}/ml/assess` | preparation preview + problem detection + suitability (nothing is changed) |
| POST | `/assets/{id}/ml/jobs` | start AutoML training (background) |
| GET | `/assets/{id}/ml/jobs` | training jobs for a dataset |
| GET | `/ml/jobs/{id}` | progress and outcome |
| POST | `/ml/jobs/{id}/cancel` | cancel a queued or running job |
| GET | `/ml/models` | list (optionally for one dataset), paged |
| POST | `/ml/models/compare` | compare saved models (same target and dataset) |
| GET / PATCH / DELETE | `/ml/models/{id}` | details / rename / delete (with report and prediction history) |
| GET | `/ml/models/{id}/report` | full report with an Explanation per section |
| POST | `/ml/models/{id}/partial-dependence` | one input's effect, others held typical |
| POST | `/ml/models/{id}/predict` | one case |
| POST | `/ml/models/{id}/predict-batch` | every row of a dataset in the same project |
| GET | `/ml/models/{id}/predictions` | history |
| GET | `/ml/predictions/{id}` and `/download` | one prediction / its CSV |

Every mutating call requires the CSRF header; every call requires a signed-in user. Rate limits (hit **before** validation, so
malformed requests cannot be used to probe): `ml:user`, `ml_train:user`, `ml_predict:user`, `ml_export:user`. Audit rows hold
ids, counts and outcomes — never feature values, row contents or target values.

## Frontend (the ten interfaces)

| # | Interface | Where |
|---|---|---|
| 1 | Predictive Analytics Dashboard | `/predictions` |
| 2 | Dataset Preparation | `/predictions/data/:assetId` → tab "Data preparation" |
| 3 | Prediction Objective Selection | same page → tab "Objective" |
| 4 | AutoML Training | same page → tab "Training" (progress, cancel, earlier runs) |
| 5 | Model Comparison | a model's "Model comparison" tab; `/predictions/compare?ids=a,b` for saved models |
| 6 | Model Evaluation | `/predictions/models/:modelId` |
| 7 | Explainable AI | model → "Explainable AI" |
| 8 | Individual Prediction | model → "Predict one case" |
| 9 | Batch Prediction | model → "Predict a dataset" (CSV download) |
| 10 | Model Management | `/predictions/models` (list, rename, delete, select up to 5 to compare) |

Charts (all SVG, each with a "Data table" and "Save image"): confusion matrix, ROC, precision–recall, importance, actual vs
predicted, residuals (scatter + histogram), learning curve, model-comparison bars, partial dependence, prediction
distribution, class-probability bars, local-effect bars. Geometry is computed in a pure module (`features/predictive/charts.js`)
so it is unit-tested without a browser.

## Algorithms (scikit-learn only)

Twelve algorithm families, each offered only for the problem types it supports (the list matches the Phase 5 specification):

| Family | Classification | Regression |
|---|---|---|
| Logistic regression | yes | — |
| Linear / Ridge / Lasso regression | — | yes (three families) |
| Decision tree | yes | yes |
| Random forest | yes | yes |
| Extra trees | yes | yes |
| Gradient boosting | yes | yes |
| Histogram gradient boosting | yes | yes |
| Support vector machine (SVC / SVR) | yes | yes |
| k-nearest neighbours | yes | yes |
| Naive Bayes | yes | — |

Every run also fits a baseline (majority class / mean) so a model is always shown next to "just guessing". Size limits remove an
algorithm *with a stated reason* instead of letting it run out of time: SVM above 10,000 training rows, k-NN above 60,000, and
classic gradient boosting above 3,000,000 cells. No algorithm outside this closed catalogue can be requested.

## What is not included

SHAP is not installed in this environment and is **not claimed**: the catalogue reports `shap: false` with a note, the UI says
so, and local explanations are labelled "one-input-at-a-time replacement (not SHAP)". See `TESTING.md` for the full list of
limitations.
