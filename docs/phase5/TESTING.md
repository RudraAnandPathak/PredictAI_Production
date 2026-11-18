# Phase 5 testing record (2026-11-12)

## Results

| Suite | Result | Notes |
|---|---|---|
| Backend, real environment (scikit-learn 1.9.1, numpy, pandas, scipy) | **740 run, OK, 7 skipped** | The 7 skipped are the HTTP-layer modules that need FastAPI + httpx, which cannot be installed offline |
| Backend, with the FastAPI stand-in | **840 run, OK** | Stand-in = real Starlette/pydantic behind a minimal FastAPI shim: **a stand-in result, not real FastAPI** |
| Frontend `node --test` | **152 / 152** | 54 new (39 interface logic + 15 real-response contract tests) |
| Production bundle | built with **esbuild**, not `vite build` | Package installs are blocked in the build environment; run `npm install && npm run build` yourself |
| Browser end-to-end (Playwright, Chromium), Phase 5 flow | **31 / 31**, no console or page errors | Against the esbuild bundle + stand-in API, on a freshly migrated database |
| Browser regression, Phase 4 flow | 31 / 31 | unchanged script |
| Browser regression, Phase 3 flows | 75 / 75 | unchanged scripts (workspace, governance, social sign-in with a fake provider) |
| Browser regression, Phase 2 flow | 47 / 47 | unchanged script, normal configuration (no sign-in provider) |
| Phase 4 → 5 database upgrade | migrations `[1,2,3]` → `[4]`, owner preserved | see `MIGRATIONS.md` |

Baselines at the corrected Phase 4: backend 415 (+6 skipped) real / 491 stand-in, frontend 98. No existing test was removed, weakened or skipped.

## New and extended backend tests (343 in 10 modules)

| Module | Tests | Covers (specification §16) |
|---|---|---|
| `test_mixed_association` | 29 | mixed-variable: numeric+numeric, categorical+numeric, categorical+categorical; assumption checks; missing data; small samples; invalid combinations; agreement with the Phase 4 functions it reuses |
| `test_explanation` | 25 | universal Explanation core: every cited number registered and checked, no invented values, "unavailable" with a reason, analytics / insights / assistant / profile coverage, technical results and `result_hash` unchanged |
| `test_explanation_ml` | 19 | the 14 predictive explanation kinds reflect the stored numbers; no causal wording; unsupported cases say so |
| `test_predictive_prep` | 73 | dataset preparation: numeric/categorical/date/text detection, missing values, duplicates, encoding, scaling, outliers, high cardinality, constant columns, leakage, imbalance, suitability, problem detection and overrides, invalid datasets |
| `test_predictive_engine` | 62 | AutoML engine: classification and regression, cross-validation, model selection, hyperparameter search (grid and randomized, bounded), metrics against hand calculation and independent recomputation, overfitting verdicts, invalid metric configurations, held-out rows never used for fitting or selection, preprocessing statistics from training rows only, determinism by seed, time budget, size limits, explainability (permutation, native, partial dependence, local effects, unsupported model types) |
| `test_predictive_predict` | 28 | prediction: schema validation, out-of-range and unseen-category notes, probabilities only where meaningful, batch limits, CSV formula safety |
| `test_predictive_service` | 45 | jobs against a real database and storage: lifecycle, cancellation, interrupted-job recovery, quotas, model management and versions, prediction history, model integrity (`MODEL_INTEGRITY` 409), audit |
| `test_predictive_api` | 25 | HTTP layer (stand-in): status codes, CSRF, validation errors, downloads, rate limits |
| `test_predictive_security` | 28 | cross-user and cross-project isolation (identical 404s), permissions, closed configuration vocabulary, hostile names and data, deletion and purge (project, account, retention), audit without data values, training sandbox (scrubbed environment, isolated interpreter, private working folder), source discipline (no dynamic code, shell or network; one reviewed model reader) |
| `test_predictive_contract` | 9 | the API's report and model shapes against real responses shared with the frontend (`shared/contracts/ml-model-report.*.example.json`) |

Frontend: `predictive.test.js` (API paths, methods and CSRF, GET downloads, configuration build and validation, polling, progress and cancellation,
chart geometry, formatters, navigation and route order, and a source check that every result view renders `<Explanation value={…}`) and
`ml-contract.test.js` (the same helpers run on real server reports: problem type, titles, explanations, ranking, importance, learning curve,
confusion matrix, ROC/PR, scatter, residuals, histogram, the honest SHAP statement). Mutation check: reintroducing the original defect makes three of
these fail.

## What the browser run covers

Register and sign in, upload three datasets, Predictions listed as an existing area; objective detection with its reason and Explanation; refusal of an
impossible objective in plain words; preparation (leakage, balance, per-column decisions, "results are for earlier settings" until checked again);
invalid training settings caught before sending; a real training run with live progress; the model-count header refreshing; evaluation charts with data
tables and "Save image" (the SVG has resolved colours); ranking with the chosen model marked; importance, partial dependence and the SHAP note;
single prediction with probabilities, local effects and an out-of-range flag; invalid entries; history; batch prediction with distribution and CSV
download (41 lines for 40 rows); the original dataset unchanged; cancellation with nothing saved; a second model and saved-model comparison; refusal
to compare different targets; a regression model; rename, versions, history; another account getting "not found" in the UI and 404 on every endpoint;
delete with confirmation; the dataset footprint counting models; the Explanation section in Analytics results, Insights, Ask and the data profile;
dark theme; no horizontal overflow at 375 px on every Predictions page.

### Defects the browser run found, and what was done
1. **The model page crashed** (React error: an object rendered as text). The report's `problem` is an object (`{type, why, classes, …}`) and the evaluation
   panel treated it as a string. Fixed (`problemTypeOf`), and the same mistake can no longer pass the tests unnoticed because the frontend and backend now both
   check real server responses (the earlier unit tests used hand-written reports).
2. **The dataset header kept saying "0 models"** after a run finished. Fixed (the summary refreshes when a run completes); the browser run now asserts it.
3. Several failures were mistakes in the test script itself (hidden dropdown options matching a text search, a wrong endpoint, load timing); they were
   fixed in the script, not by loosening what is checked.

## Not verified here (stated, not assumed)
- **Real FastAPI** (0.162.5 on the owner's machine): the HTTP-layer tests ran on a stand-in, and the real-dependency run skips them. The two Phase 3 route-discovery fixes are therefore still unconfirmed on real FastAPI.
- **The real `vite build`** (esbuild produced the bundle that was tested).
- **macOS / Python 3.14**: tests ran on Linux, Python 3.13. Memory limits for training follow the Phase 4 sandbox: kernel-enforced only where the OS proves it (Linux), monitored best-effort elsewhere.
- **scikit-learn other than 1.9.1**: `requirements.txt` allows `>=1.5,<2.0`; only 1.9.1 was available. A model saved under another version loads with a visible note.
- **SHAP, LightGBM, XGBoost, statsmodels** are not installed and are not used or claimed.

## Known limitations of the feature itself
- **No SHAP.** Local explanations replace one input at a time and are labelled as such.
- **One prediction costs about 2–3 seconds** because the model is loaded in a separate, isolated process each time.
- **The delivered model is fitted on the training split** (so its test scores are honest); a model refitted on all rows could be slightly better.
- **Cross-validation scores after tuning are slightly optimistic**; the held-out test score is shown beside them and is the unbiased one.
- **Leakage screening looks at all labelled rows** to flag columns that give the answer away. It only *excludes* columns (you can override); it never picks inputs by how well they predict, and it is not used inside validation.
- **Yes/no columns appear as true/false** (the Phase 4 canonical form of the data).
- The optional reference-rows file stored beside a model is read only as data and is not hash-verified (the model file is).
- No React component-render unit tests exist (the interface is covered by pure-logic tests and the browser run).
- Research findings and generated reports (named in §9) do not exist yet; the Explanation mechanism is ready for them.

## How to run
```bash
cd backend  && pip install -r requirements-dev.txt && python -m unittest discover -s tests      # HTTP modules run when FastAPI is installed
cd frontend && npm install && npm test && npm run build
```
