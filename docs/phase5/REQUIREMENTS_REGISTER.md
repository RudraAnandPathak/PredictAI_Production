# Phase 5 requirements register

Status words: **Implemented+tested** = code, automated tests and (for anything a person sees) a real-browser check. **Partial** = implemented
with a stated gap. **Not included** = deliberately absent, and said so in the product. Section numbers follow the Phase 5 specification.

| § | Requirement | Status | Where it lives / evidence |
|---|---|---|---|
| 3 | Automatic feature detection; numeric/categorical/date/text classification | Implemented+tested | `predictive/prep.py`; `test_predictive_prep` |
| 3 | Target identification (candidates offered; unusable targets refused with a reason) | Implemented+tested | `predictive/problem.py`, `/assets/{id}/ml/schema`; prep tests; browser step "date cannot be the target" |
| 3 | Missing-value analysis and handling (impute inside the pipeline, columns with too much missing data left out) | Implemented+tested | `prep.py`, `transformers.py`; prep + engine tests |
| 3 | Duplicate detection (reported, with why it matters; never silently dropped from the stored file) | Implemented+tested | `prep.py` duplicates block |
| 3 | Categorical encoding; rare-category grouping; high-cardinality handling | Implemented+tested | `transformers.py`; prep tests |
| 3 | Numeric scaling / transformation (auto per algorithm family, configurable) | Implemented+tested | `pipeline.py`, config `scaling`; engine tests |
| 3 | Outlier identification (reported; optional clipping) | Implemented+tested | `prep.py` |
| 3 | Constant / near-constant feature detection | Implemented+tested | `prep.py` |
| 3 | Date and time feature extraction | Implemented+tested | `transformers.py` date features; engine tests |
| 3 | Feature compatibility checks (types that cannot be used as inputs, identifiers) | Implemented+tested | `prep.py` role decisions |
| 3 | Data-leakage detection (suspected answer columns excluded unless overridden; no target-aware selection anywhere) | Implemented+tested | `prep.py` leakage block; `test_predictive_prep`, `test_predictive_engine`; browser step on the `leaky` column |
| 3 | Class-imbalance detection with a metric recommendation | Implemented+tested | `prep.py`, `problem.py` metric choice |
| 3 | Dataset suitability assessment (ready / caution / blocked, with reasons) | Implemented+tested | `prep.py` suitability; API + browser |
| 3 | Preprocessing decisions explained, reviewable and configurable; original dataset preserved; reproducible | Implemented+tested | role overrides + settings in the UI; "Check again"; `test_training_never_changes_the_original_dataset`; seeds + stored config |
| 4 | Binary / multiclass / regression detection with a reason; override; invalid configurations refused in plain words | Implemented+tested | `problem.py`; prep tests; browser steps (recommendation, impossible override) |
| 5 | AutoML pipeline: evaluate → detect → prepare → select algorithms → train → evaluate → compare → rank → pick best → save model + pipeline | Implemented+tested | `train.py`, `pipeline.py`; engine + service tests; browser training run |
| 5 | Algorithms: 9 classification and 9 regression families from the specification, only when compatible with the data | Implemented+tested | `catalog.py` (12 families, size guards with stated reasons) |
| 5 | Train/test split, cross-validation, stratification where appropriate, seeds, configurable strategies | Implemented+tested | `validation.py`; engine tests |
| 5 | Progress, cancellation, time tracking, resource limits, error handling | Implemented+tested | `jobs.py`, `worker.py`, sandbox; service + security tests; browser cancel step |
| 5 | No leakage in preprocessing, feature selection or evaluation | Implemented+tested | preprocessing fitted per fold; `test_held_out_rows_never_influence_fitting_or_selection` (a pattern that reverses in the held-out rows must score badly) and `test_preprocessing_statistics_come_from_training_rows_only` |
| 6 | Classification metrics: accuracy, precision, recall, F1, ROC-AUC, PR-AUC, log loss, confusion matrix, classification report | Implemented+tested | `metrics.py`; tests against independently computed values |
| 6 | Regression metrics: MAE, MSE, RMSE, R², adjusted R² (only when appropriate), MAPE (only when appropriate) | Implemented+tested | `metrics.py`; metrics shown only when valid, with the reason when not |
| 6 | Metric choice follows problem and class balance; accuracy is not automatically primary | Implemented+tested | `problem.py` metric rules; "Score used to rank models" |
| 6 | Model comparison: algorithm, metrics, CV results, duration, generalisation, overfitting indicators, size; ranking with explanation | Implemented+tested | report `ranking`, Comparison panel, `/ml/models/compare`; service + browser |
| 7 | Permutation importance, model-native importance, partial dependence, local explanations, limitations; importance is never presented as causation | Implemented+tested | `explain.py`; `test_predictive_engine`, `test_explanation_ml`; browser step |
| 7 | **SHAP** "where practical and compatible" | **Not included** | The library is not installed here. The catalogue reports `shap: false` with a note, the UI says so, and local explanations are labelled "one input at a time (not SHAP)". |
| 8 | Mixed-variable analysis (num–num, cat–num, cat–cat) with assumptions, missing data, effect sizes, uncertainty, invalid combinations refused, integrated into the Phase 4 engine without duplicating it | Implemented+tested | `analytics/ops/mixed.py` (one registered operation reusing Phase 4 functions); `test_mixed_association` |
| 9 | Universal **Explanation** section: reusable mechanism; every analytical output; based on real results; no invented numbers; "unavailable" with a reason when it cannot be written reliably; technical results untouched | Implemented+tested | `modules/explanation/*`; `test_explanation`, `test_explanation_ml`; front-end component + source-coverage test; browser step across Analytics, Insights, Ask, Profile and all Phase 5 views |
| 9 | Coverage of *research findings* and *generated reports* | **Partial (by design)** | Those modules do not exist yet (later phases). The mechanism is generic and registered by kind; they can adopt it without changes to it. |
| 10 | Prediction interface: choose a model, enter values, predict; batch from a file; download; history; probability shown only when meaningful and never as a guarantee; input schema + pipeline preserved | Implemented+tested | `predict.py`, Predict and Batch panels; service/API tests; browser steps |
| 11 | Model management: list, details, training history, evaluation, open, predict, delete, compare versions; full metadata | Implemented+tested | `/ml/models*`; Models page; service + API + browser; metadata fields verified in `test_model_metadata_covers_every_required_field` |
| 11 | Access follows ownership and project rules | Implemented+tested | `test_predictive_security`; browser isolation step |
| 12 | Grid and randomized search; configurable; bounded; parameters evaluated, best parameters, best score, duration displayed | Implemented+tested | `train.py` HPO; engine + service tests; report `hpo` |
| 13 | Visualisations: confusion matrix, ROC, PR, importance, actual vs predicted, residuals, learning curves, comparison, prediction distribution — titled, labelled, interpreted, with a data table | Implemented+tested | `MlCharts.jsx`, `charts.js` (pure geometry); frontend + contract tests; browser screenshots |
| 14 | Ten interfaces following the existing design system, with progress, error, loading and empty states | Implemented+tested | `pages/predictive/*`; browser run; mobile (375 px) overflow check on every page |
| 15 | Auth, RBAC, project permissions, ownership, quotas/entitlements, audit; background jobs with status, cancel, recovery, limits, safe storage, validation, isolation; no generated code executed; no cross-project/account exposure; Phase 1–4 guarantees kept | Implemented+tested | service/security/API tests; subprocess sandbox; SHA-256-verified models; `SourceDiscipline` static tests (no eval/exec/shell/network; one reviewed model reader) |
| 16 | Mandatory tests in each listed area | Implemented+tested | see `TESTING.md` |
| 17 | Free/open-source only; no AI coding agent other than the one delivering this; no secrets exposed, overwritten or deleted; original Phase 4 and prior ZIPs untouched | Implemented | `requirements.txt` additions are scikit-learn + joblib only; `.env` never read or written; packaging check excludes secrets |
| 18 | Acceptance: full workflow; backend + frontend tests; **production frontend build**; security and regression | **Partial** | Everything except the real Vite build was run (see `TESTING.md`): the bundle was produced with esbuild because package installs are blocked here. Run `npm install && npm run build` once on your machine. |

## Decisions the owner should know about

1. **Model registry (`/models`) stays "Planned".** Your own models are listed, compared, renamed and deleted under **Predictions**. The professional
   "Model registry" entry is reserved for cross-project registry features (experiments, lineage) that were not part of this specification.
2. **The model that is delivered is the one that was evaluated.** It is fitted on the training split, so the reported test scores describe exactly
   that model. A model refitted on all rows could be slightly better but would then have no honest held-out score.
3. **Tuned cross-validation scores are slightly optimistic** (the same folds pick the settings). The test-set score is the unbiased one and is shown
   beside it; the report says so.
4. **Time budget and size limits are enforced with stated reasons.** An algorithm that would not finish in the budget is skipped and listed, not
   silently dropped.
