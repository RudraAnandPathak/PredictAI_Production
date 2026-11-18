# Phase 6 — Research & World Intelligence: assessment and plan

Starting point: the integrated Phase 1–5 project (`PredictAI_Phase1_Phase2_Phase3_Phase4_Phase5_Integrated.zip`, 382 files). Phase 5 was copied to
`PredictAI_Phase6/`; Phase 5 and every earlier ZIP are untouched. Authoritative requirements: the Phase 6 specification and the Master Engineering
Documentation (PAI-006 "AI Research Assistant & World Intelligence", plus the security, job and testing parts).

## Existing functionality that is reused (not rebuilt)
| Need | Reused from |
|---|---|
| Sign-in, sessions, CSRF, permissions, entitlements, rate limiter, audit | Phase 2 `identity/*`, `api/deps.py` |
| Projects, ownership check, identical-404 rule, storage keys, deletion and retention | Phase 3 `workspace/*` (`AssetService.get_owned`, `Governance`, `keys`) |
| Stored document text with page numbers and headings | Phase 3 extraction (`extractions.text_key`, `parsing/documents.py`) |
| Analysis runs, saved results, evidence contract | Phase 4 `analytics/*` |
| Models, evaluation reports, training jobs | Phase 5 `predictive/*` |
| The "Explanation" mechanism (`Say`, number registry, "unavailable" with reason) | Phase 5 `explanation/core.py`, front-end `Explanation.jsx` |
| Background-job pattern (bounded pool, DB cancel flag, heartbeat, stale recovery) | Phase 5 `predictive/jobs.py` (pattern, not code) |
| Safe XML parsing | Phase 3 `parsing/xmlsafe.py` |
| UI shell, router, `useResource`, `ChartFrame`, design tokens | Phases 1–5 front end |

## What Phase 6 adds (missing before)
Nothing in Phases 1–5 searched documents semantically, planned research, fetched the web, tracked sources, kept evidence, ingested news or routed AI calls.
All twelve modules are new; the integration points above are the only things reused.

## Architecture decisions (with the alternatives that were rejected)
1. **Four new packages, no new service.** `webaccess` (safe fetching, search providers, cache), `providers` (AI routing), `retrieval` (index/search/compare/evaluate),
   `research` (planner → collector → evidence → synthesis → quality, sessions, notes, jobs), `world` (news and influence). Modular monolith, as the Master document asks.
2. **No new dependencies at all.** HTML is parsed with the standard library; semantic retrieval uses scikit-learn (already required); BM25 is implemented directly.
   Rejected: sentence-transformers/spaCy/trafilatura (heavy, not installable offline, would change the dependency footprint), a vector database (spec forbids a paid one;
   a derived JSON index is enough at this scale and is rebuildable).
3. **"Semantic" retrieval is local latent semantic analysis (TF-IDF + truncated SVD), not neural embeddings.** It finds related wording through co-occurrence *within the
   project's own documents*; the interface says so. Dense neural vectors are supported when an owner-configured local provider (Ollama `/api/embed`) supplies them.
4. **Answers are extractive and verifiable.** The engine never writes a claim of its own: findings are verbatim passages with anchors; connecting text is a template whose
   numbers are registered. A language model, if configured, may add a clearly labelled narrative; every sentence must cite existing evidence and repeat only numbers found in it, or it is dropped.
5. **Consent is layered and shown.** Web research needs (a) the server allowing it, (b) the person's own opt-in, (c) the project's opt-in, (d) the person approving the exact outbound
   queries (a plan hash). Outbound queries are built only from the question text; private document or data text found in a query blocks the search.
6. **SSRF defence in depth.** Scheme/port/credential rules, DNS resolution, address-class refusal, the connection pinned to the checked address, every redirect re-checked,
   size/time/type limits, no cookies, robots.txt honoured. Private networks can be allowed only by an explicit development flag that is refused in hosted configuration.
7. **Tiers.** Basic research is available to every account at lower limits; `intelligence.live` (Special) raises limits, as `compute.extended` does for modelling. This is a limit, not a lock.
8. **Causation is never inferred.** Influence analysis reports temporal and statistical association with a corrected permutation test; "causal" is only ever a source's own claim, attributed.
9. **Providers are configuration.** An always-available built-in provider guarantees the app works offline; Ollama (local) and an OpenAI-compatible adapter are optional; paid providers are
   off by default and need a flag, a key reference (environment-variable *name*) and a budget.

## Compatibility risks and how they are handled
- Migration `0005` is additive; `0001`–`0004` are untouched. Governance purge functions are extended (not replaced) so deleting a project/asset/account also removes research rows.
- `Perm` gains one owner permission (provider management); the permission-set tests are updated, not weakened.
- Router order in the front end is first-match; new routes are placed before parameterised ones (tested).
- The environment has no network: provider adapters and the fetcher are tested against local fake servers and injected transports; real providers are **not** claimed as verified (see TESTING.md).

## Stages
1 assessment/register · 2 migration, web access, provider routing · 3 retrieval · 4 research engine · 5 world intelligence + interfaces · 6 tests · 7 front end · 8 browser run, regression, packaging.
Traceability: `requirements.json` / `REQUIREMENTS_REGISTER.md` (generated; checked by a test). Future phases: `OUTSTANDING.md`.
