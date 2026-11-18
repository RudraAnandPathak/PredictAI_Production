# Phase 4 — Core Intelligence: implementation

Phase 4 adds descriptive analytics, relationship discovery, statistical testing, automated insights, a natural-language
assistant and interactive charts. It extends the Phase 3 app; nothing from Phases 1–3 was removed or weakened.

## Architecture
```
frontend  pages/analytics/*  →  api/analytics.js  →  /api/v1 (api/v1/analytics.py, thin router + CSRF)
backend   modules/analytics/
            service.py        AnalyticsService   authorise → plan → execute → store → present   (reuses Context/Principal/audit/limiter)
            insights.py       InsightService     derives findings from stored runs, never from raw data
            assistant_service.py + assistant/    question → typed plan → run → independent check → answer
            plan.py           typed AnalysisPlan: closed vocabulary of 15 operations, validated parameters, plan hash
            executor.py/worker.py  isolated execution (subprocess via `_boot.py`/`sandbox.py`: rlimits, probed kernel memory limit, resident-memory watchdog, wall-clock timeout, scrubbed env, `-I`, private temp copy)
            ops/              descriptive, relationships, hypothesis, charts, temporal  (the ONLY place statistics are computed)
            evidence.py       evidence contract; drill.py  chart selection → ordinary filters
            repository.py     analysis_runs, insights, conversations, conversation_messages
```
- **Authorisation:** every call goes through `AssetService.get_owned` and the dataset-version lookup. A missing, foreign or
  deleted dataset/run/insight/conversation returns the identical 404.
- **Python computes, the model only explains.** The browser formats numbers; it calculates no statistic. The assistant's
  language step is deterministic; an optional local model may only reword already-verified figures.
- **No generated code is ever executed.** Plans are data; the operation vocabulary is closed (a static test greps the module
  for `eval`, `exec`, shell and network use).
- **Deterministic:** fixed seeds; `result_hash` is a fingerprint of the result so a re-run can prove it reproduced.
- **Privacy:** audit rows hold ids and counts only, never question text or values. No data leaves the server unless the person
  opts in per request *and* a provider is configured (see `AI_PROVIDER.md`).

## What each module does
| Module | Where |
|---|---|
| A Descriptive statistics, missing values, outliers, quality | `ops/descriptive.py`, Overview tab |
| B Relationships (Pearson/Spearman/Kendall, association, ranked discovery with BH adjustment and stability) | `ops/relationships.py`, Relationships tab |
| C Tests with assumptions, effect sizes, CIs, post-hoc (Holm), multiple-testing note | `ops/hypothesis.py`, Tests tab |
| D Insights (10 required fields, save/dismiss/restore, stable across regeneration, "Not enough evidence.") | `insights.py`, Insights tab |
| E Assistant (typos, aliases, filters, clarification, 9-step workflow, independent verification) | `assistant/`, Ask tab |
| F Charts: bar, scatter, heatmap, histogram, time series; hover, sort, filter, brush, drill-down, trend overlays, export | `ops/charts.py`, `components/analytics/*`, Charts tab |
| G Evidence contract (VERIFIED / INCOMPLETE) | `evidence.py`, `EVIDENCE_CONTRACT.md` |
| H Computation architecture | `plan.py`, `executor.py`, `worker.py` |

## API (all under `/api/v1`, session + CSRF)
`GET /assets/{id}/analytics/schema` · `POST /assets/{id}/analytics/run | presets/{dashboard|relationships} | hypothesis` ·
`GET /assets/{id}/analytics/runs` · `GET /analytics/runs/{id}` · `POST /analytics/runs/{id}/save | rerun | rows | export` ·
`POST /assets/{id}/insights/generate` · `GET /assets/{id}/insights` · `GET /insights/{id}` · `POST /insights/{id}/state` ·
`POST /assets/{id}/assistant/ask` · `GET /assets/{id}/assistant/conversations` · `GET /assistant/conversations/{id}` · `GET /assistant/provider`.

## Limits and safeguards
Standard accounts: 200,000 rows / 4M cells / 20 s / 1.5 GB per analysis; Special/Owner (extended compute): 1,000,000 rows / 20M cells /
90 s / 4 GB. Oversized datasets are refused with a clear message, never silently sampled. Rate limits: 120 analyses/min and 60
assistant questions/min per user. Row drill-down pages are capped; exports are audited, rate-limited and neutralise CSV
formula injection (`=`, `+`, `-`, `@` prefixes).

## Data lifecycle
Result documents are stored under the asset's storage prefix, so the Phase 3 deletion and retention purge removes them with
the dataset; database rows for runs, insights and conversations are purged in the same transaction.
