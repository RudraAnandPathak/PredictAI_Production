# Master blueprint gap analysis (Part 10A audit, no code changed)

Sources: PREDICTAI-MASTER-PROJECT-BLUEPRINT.pdf (10 pages), Part 10A Modules 12-21, docs/phase11/REQUIREMENTS_REGISTER.md (416 rows), and a code search of backend/app/modules.
Method: register rows that are not "Complete and verified" were listed, and each Part 10A capability was searched in code. "Not found" means a search of the source found nothing; it is not proof of absence.

## Already present (verify and strengthen, do not rebuild)
Profiling, cleaning, versions; analytics engine and assistant; AutoML with 12 algorithms, HPO with limits, CV, calibration, log loss / PR-AUC / balanced accuracy / MAPE; permutation importance, partial dependence, local effects; model registry with lifecycle, rollback, compare; batch drift hint at prediction time; reports and exports; collaboration; scheduling (local; email/Slack need credentials); owner ops and monitoring snapshot; world intelligence incl. tone and freshness; exam intelligence; dev workspace; unified Conversations.

## Verified gaps (feature not built)
| # | Blueprint / Part 10A item | Finding |
|---|---|---|
| G1 | Forecasting with intervals, seasonality, backtesting (Blueprint s05; Module 13) | Only descriptive trend/time-series charts exist (analytics/ops/temporal.py). No forecast model, interval, seasonal decomposition or rolling-origin backtest. |
| G2 | Ensembles: voting / stacking (Module 13) | Not in the algorithm catalogue. Only single models and tree ensembles. |
| G3 | SHAP (Module 15, "where compatible") | Library not installed; catalogue reports shap:false honestly (P5-26). Cannot add without a network install. |
| G4 | Model monitoring over time: recurring data drift (PSI/KS) against training baseline; concept drift from labelled outcomes; performance decay alerts (Module 17) | Only a per-batch "possible_drift" note at prediction time. No stored baselines, no scheduled drift check, no outcome feedback. |
| G5 | Magic-link and Apple sign-in (Blueprint s04) | Deferred by design (P3-23, P3-24). |
| G6 | Explicit what-if simulation endpoint (Blueprint) | Single-row local effects exist; a user-facing multi-column what-if/scenario comparison was not found. Partial. |
| G7 | Database/warehouse connectors (Blueprint s04, "optional") | Not found in code or register. Needs confirmation. |

## Unverified or blocked (built, not proven here)
Real Vite production build, real FastAPI/Uvicorn run, real Google/GitHub/SMTP/Slack, Docker isolation for untrusted code (stays disabled), browser rendering of Conversations UI, accessibility audit completeness, CVE audit. Register: 6 "not tested", 3 "blocked", 11 "partial", 4 "deferred", 1 "not implemented" (SHAP).

## Intended differences from the blueprint
Blueprint proposes D1 / object storage / Cloudflare; the build is local-first SQLite at zero cost. Not a gap, a decision to confirm before any hosted deployment.

## Status after Phase 10A
G1 forecasting, G2 ensembles, G4 monitoring, G5 magic link and G6 what-if are now built (see `PHASE10A.md`). G3 SHAP and G7 connectors remain unbuilt. Apple sign-in stays deferred.

## Suggested order (Part 15B: verify, then strengthen) — original plan
1. G4 model monitoring and drift (additive migration, reuses registry and ownerops).
2. G1 forecasting with intervals and backtest (extends the predictive engine, no second AutoML).
3. G2 voting/stacking ensembles in the existing catalogue.
4. G6 what-if scenarios on the existing explain module.
5. G5 magic link (needs working email; SMTP unverified).
G3 and G7 wait for a network install / a decision.
Naming: this is Phase 10A work; Phase 11 is not begun (the earlier hardening folder keeps its name).
