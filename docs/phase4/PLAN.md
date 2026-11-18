# Phase 4 plan — Core Intelligence

## Stage 1 findings (from the real Phase 3 code)
| # | Finding | Decision |
|---|---|---|
| B1 | Baseline = `PredictAI_Phase3` folder (Phase 3 + the OAuth and security-sweep corrections). Open items from that round: the sweep fix is **unconfirmed on real FastAPI 0.162.5**, and the OAuth "not set up" root cause on the user's machine is unknown (diagnostics added). | Neither blocks Phase 4: analytics is additive and has its own route sweep through the same discovery helper. Reported again in the final report. |
| B2 | Datasets are normalised once into a canonical UTF-8 CSV per *version* (`table.csv`) with declared column types, missing and invalid counts in the extraction summary. `DataViews._version` + `AssetService.get_owned` are the only authorisation path. | Analytics reads **only** the canonical table of an owner-checked asset version through `AssetService.get_owned` — the dataset snapshot is (asset, version, checksum). No new access path. |
| B3 | Profiling (`profiling.py`) is a single-pass, stdlib-only descriptive profiler with fact/heuristic/estimate labels and reproducible hashes. | Kept as-is (Phase 3 behaviour must not change). Phase 4 *extends* it: the analytics engine reuses the same value classification (`coltypes.classify/to_float/parse_date/is_missing`) and links each descriptive run to the Phase 3 profile of the same version for cross-checking; no competing missing/type definitions. |
| B4 | Version lineage exists (`asset_versions.parent_version_id`, `transformations`). | Evidence "transformation lineage" is read from it. |
| B5 | Storage keys under `p/<project>/a/<asset>/…` are removed by asset/project deletion by prefix; row purge lists tables explicitly. | Results live under `…/n/<run>.json`; the purge lists and footprint get the new tables/kind. |
| B6 | Permission `analytics.advanced` already exists (Special/Owner) and `compute.extended`. | Core analytics needs only `data.manage_own` (every account). `compute.extended` raises the row/time caps; revocation is immediate (permissions are recomputed per request). `analytics.advanced` stays unused (nothing in Phase 4 is gated to Special). |
| B7 | Only fastapi/uvicorn/dotenv are required; pandas 3.0, numpy 2.5, scipy 1.18 are present in this sandbox, plotly is not, and no network exists. | numpy/pandas/scipy become required (`requirements.txt`); the unit/engine tests run on the **real** libraries here. Charts are drawn in the React app as SVG (no new JS dependency, nothing to install offline). scikit-learn/matplotlib/plotly are not used; documented, not silently dropped. |

## Architecture
New module `app/modules/analytics` (framework-free; routers stay thin in `app/api/v1/analytics.py`):
```
plan.py        typed AnalysisPlan / Operation / Filter; closed operation vocabulary; validation (no eval, no code strings)
frame.py       snapshot loader: canonical CSV -> typed DataFrame (index = source row number), filters, resource caps
ops/           pure functions: descriptive, relationships, hypothesis, temporal, charts  (the only place numbers are computed)
evidence.py    the evidence contract, completeness rules, hashing
executor.py    runs a plan: caps, deadline, subprocess isolation (rlimits) or in-process (tests), captures warnings/errors/stdout
worker.py      subprocess entry point
insights.py    insight engine (builds plans, calls the executor, applies evidence rules)
assistant/     language.py (question -> plan, deterministic), verify.py (independent recomputation), providers.py (explainer abstraction)
service.py     use cases: authorise -> snapshot -> plan -> execute -> persist run (+evidence) -> present
repository.py  all SQL for 0003_analytics.sql
```
Rules: the LLM (optional, local) only rephrases an already-verified result and every number it writes is checked against the result; question understanding is deterministic. Nothing generated is executed: plans are data validated against a closed vocabulary.

## Stages
2 foundation (plan, frame, evidence, executor, migration, service skeleton) · 3 descriptive + relationships · 4 hypothesis tests · 5 insights · 6 assistant · 7 charts · 8 frontend · 9 security/correctness · 10 integration, docs, ZIP. Stop after Phase 4.
