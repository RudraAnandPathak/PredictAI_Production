# Phase 6 implementation record

What was built, where it lives, what it reuses, and what changed in existing files. The requirement-by-requirement view is `REQUIREMENTS_REGISTER.md`; what is not done is `OUTSTANDING.md`; test results are `TESTING.md`.

## Where each module lives
| Spec module | Backend | API | Screens |
|---|---|---|---|
| 1 Research engine | `modules/research/{planner,collector,engine,evidence,bundles,runner,jobs,service}.py` | `api/v1/research.py` | New research, Session, Result (`pages/research/NewResearchPage, SessionPage, BundlePage`) |
| 2 Opt-in web research | `modules/webaccess/*` (URL guard, fetcher, HTML text, cache, Wikipedia / arXiv / SearXNG), `research/consent.py` | `research.py` (`/research/settings`, plans) | Settings, Privacy tab, plan approval in New research |
| 3 Sources and citations | `research/sources.py`, `research/citations.py` | `research.py` | Result page citation dialog, Sources tab |
| 4 Evidence bundles | `research/bundles.py`, `research/interfaces.py` (reuse by other modules) | `research.py`, `interfaces.py` | Result page, exports (JSON, Markdown, CSV) |
| 5 Context orchestration | `research/context.py` | `research.py` | "What this is based on" on the Result page |
| 6 Document retrieval | `modules/retrieval/*`, `modules/textkit/tokens.py` | `api/v1/retrieval.py` | Search documents tab |
| 7 Research memory | `research/memory.py`, `research/repository.py` | `research.py` | History tab, notes, labels, compare, saved sources |
| 8 World intelligence | `modules/world/{feeds,ingest,topics,entities,tone,cluster,events,trends,derive}.py` | `api/v1/world.py` | News and events tab |
| 9 Event and topic intelligence | `world/events.py`, `world/trends.py`, `world/cluster.py` | `world.py` | Event page, Trends |
| 10 Influence analysis | `world/series.py`, `world/influence.py` | `world.py` | Influence tab and page |
| 11 Provider routing | `modules/providers/*` | `api/v1/ai.py` | Research settings → AI providers (owner) |
| 12 Quality control | `research/quality.py`, `research/safety.py` | in every result | "Quality checks" on the Result page |
| §4 / §5 interfaces | `research/interfaces.py`, `predictive/registry_view.py` | `api/v1/interfaces.py` | none (read interfaces only) |
| §6 Explanation | `modules/explanation/*`, `research/explain.py`, `world/explain.py` | in every result | the shared **Explanation** section |

73 new routes in all (research 32, world 20, retrieval 8, AI providers 7, interfaces 6). Every one is covered by the security route sweep (`test_phase6_security.py`).

## What was reused rather than rebuilt
Accounts, sessions, CSRF, permissions, tiers, rate limiter and audit (Phase 2); projects, `AssetService.get_owned`, storage keys, extraction text, retention and deletion (Phase 3); analysis runs (Phase 4); models and evaluation (Phase 5); the Explanation mechanism and its front-end component; the job pattern (bounded pool, database cancel flag, stale-run recovery); safe XML parsing; the router, `useResource`, `Tabs`, `Field`, design tokens.

## Rules the design holds to
- **Everything outbound is off** until the server allows it, the person opts in, the project has not narrowed it, and the person approves the exact search phrases (a plan hash). Private document or dataset text is never placed in an outbound query or sent to an external AI provider.
- **Findings are quotations.** The engine does not write claims of its own; each conclusion is a passage with an anchor that is checked again against the saved source. An AI model, if the owner enabled one, may only reword, every sentence must cite existing evidence and repeat only numbers found in it, or it is dropped.
- **Every statement carries its origin** (your material, calculated, model prediction, outside source) and its kind (stated, uncertain, opinion, prediction, computed). A model's output is a forecast, never a fact.
- **Retrieved text is data.** Instruction-like sentences are removed before anything reads them; articles that are mostly instructions are held back.
- **Association is not cause.** Influence analysis reports temporal or statistical association with a corrected permutation test; `causal_status` is always "not established".
- **Confirmed means agreement.** An event is "confirmed" only when three or more outlets report matching facts in plain wording and none disagrees. That is agreement between outlets, not verification, and the screen says so.

## Changes to existing files
| File | Change |
|---|---|
| `backend/app/main.py`, `api/deps.py`, `api/v1/__init__.py` | build and mount the Phase 6 services and routers |
| `core/config.py` | nine new settings (all default off); `core/logging.py`: document-reading and HTTP libraries are held at WARNING so they cannot write file text or URLs into logs |
| `modules/identity/constants.py`, `ratelimit.py` | one owner permission (`owner.providers.manage`); new rate-limit buckets |
| `modules/workspace/{governance,keys,repository,extraction}.py` | deletion and retention now also remove research rows and files; new storage-key helpers (all under the project prefix); `document_json` accessor for extracted text with pages |
| `modules/analytics/service.py` | one new read accessor, `open_dataset`, for other modules (same ownership rules; no behaviour change) |
| `backend/tests/test_cli.py`, `test_workspace_projects.py` | expect migration 5 and the new tables; **plus** a new test that a Phase 5 database upgrades keeping every row |
| `frontend/src/api/index.js`, `app/routes.jsx`, `app/navigation.js`, `design/index.css` | Phase 6 API client, routes, "Research" is no longer "Planned", research styles |
| `frontend/tests/navigation.test.js` | expected list of live sections now includes Research. Navigation phase labels of the still-planned Conversations, Exam tools and Model registry entries were corrected to Phase 7 (they said 6 and 5, which were no longer true) |
| `.env.example`, `config/env.example`, `config/env.production.example` | Phase 6 settings block (commented, defaults off) |

No test was removed, skipped or weakened. Phase 1–5 behaviour was not changed.

## Dependencies and configuration (inspected)
- `backend/requirements*.txt`, `frontend/package.json` and `vite.config.js` are byte-identical to Phase 5: **no Python or npm dependency was added**, nothing paid.
- Semantic search is local latent semantic analysis (scikit-learn, already required), not a pretrained model; HTML, RSS and Atom are parsed with the standard library (XML with entity definitions refused).
- New settings, all optional: `PREDICTAI_RESEARCH_WEB_ENABLED`, `PREDICTAI_SEARXNG_URL`, `PREDICTAI_RESEARCH_CONTACT`, `PREDICTAI_RESEARCH_WORKERS`, `PREDICTAI_RESEARCH_CACHE_HOURS`, `PREDICTAI_RESEARCH_CACHE_MAX_MB`, `PREDICTAI_RESEARCH_FETCH_TIMEOUT_SECONDS`, `PREDICTAI_RESEARCH_ALLOW_PRIVATE_NETWORK` (refused when hosted), `PREDICTAI_AI_ALLOW_PAID`. Provider API keys are never stored: a provider names an environment variable, and its value is read at call time.

## Defects found by the browser run and fixed
1. The project privacy form showed every switch off even when the project simply followed the person's settings, so unticking "Allow web research" changed nothing. It now starts from the settings the project follows.
2. The history search box had no accessible name; it now has one, and the results list is a labelled region.
3. Document retrieval had a backend and no screen; a **Search documents** tab was added.

## Corrective release (research engine, web search, datasets, Wikipedia and arXiv)
Read `CORRECTIVE_REPORT.md` first. New backend modules: `webaccess/queries.py` (per-provider search phrases), `webaccess/structure.py` (article HTML to sections, tables, lists, equations), `webaccess/papers.py` (PDF and arXiv HTML to a paper's sections), `webaccess/gate.py` (process-wide arXiv request spacing), `research/datasets.py` and `research/intents.py` (dataset research by calculation), `research/report.py` (the structured answer). Rewritten: `webaccess/providers/{searxng,wikipedia,arxiv}.py`, `research/{planner,collector,engine,evidence,citations,quality,service}.py`. New endpoints: `POST /research/sessions/{id}/follow-up`, `POST /projects/{id}/research/providers/{diagnose|search|open}`. New frontend: `ReportCard`, `FollowUpForm`, `SearchToolsPanel`, `features/research/{report,searchtools}.js`. Also new: `docker/searxng/` and the `phase6c.*` contract examples. The request/response shapes are in `shared/contracts/phase6c.*.example.json` (real service responses, checked by `test_phase6_corrective_contract.py`).


## Critical Research Intelligence Upgrade

New modules `research/understanding.py` (who/what/when the question is about), `relevance.py` (identity-based source and sentence judgement), `outlines.py` (task-aware parts), `queryplan.py` (per-provider queries, arXiv fit), `synthesis.py` (explanation), `workspace/parsing/pdfclean.py` (headers, footers, page-spanning sentences). Full change list, limits and install steps: `INTELLIGENCE_UPGRADE.md`.
