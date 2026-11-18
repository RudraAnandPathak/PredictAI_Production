# Phase 10 — Implementation notes

## Milestones (core delivery + completion round 1)
| Milestone | Delivered |
|---|---|
| R1 Workspace & source management | Workspaces (reused `rw_*`), upload/URL/DOI/arXiv sources, include/exclude, rename, reindex, delete with cascade, limits, jobs UI states |
| R3 Ingestion & extraction | PDF (text layer), DOCX, TXT, Markdown, CSV, XLSX, HTML; page/sheet/section locators; tables as row blocks for CSV/XLSX; extractive summary/keywords/statistics/claims |
| R4 Retrieval | FTS5 + vector hybrid, RRF, rerank heuristic, scoped search, Qdrant adapter (UNVERIFIED) |
| R6 Citation-grounded assistant | Conversations, scope, ask, verification, exact-quote citations, passage click-through, notes, findings |
| R10 Security & privacy | See SECURITY.md |
| R11 Quality | 98 new backend tests + 11 frontend tests; see TESTING.md |
| R9 (minimal) | Workspace list, 3-panel workspace page, policy control |
| **R2 Discovery** (round 1) | Crossref, OpenAlex, arXiv, Semantic Scholar, SearXNG adapters; parallel search with honest per-provider status; merge/dedupe; heuristic ranking; filters; history; personal library; side-by-side compare; APA/BibTeX; import as abstract-only or lawful open-access full text; polite pacing/backoff |
| **R5 Reasoning** (round 1) | Common findings, possible contradictions and magnitude variations, methodology comparison, gaps, evidence grading, timeline, hierarchical synthesis, optional verified model narrative; background job with progress and cancel |

## Changes to existing code (round 1 additions first)
- Migration `0010`: new tables `ri_discovery_searches`, `ri_library`, `ri_library_links`, `ri_source_facts`, `ri_analyses`; `ri_usage` and `ri_jobs` rebuilt to allow the kinds `discovery`/`reasoning`/`analysis` (rows preserved). Tests updated for migration count 10 (`test_cli`, `test_workspace_projects`, `test_p9_deletion_upgrade`, `test_p10_upgrade`).
- `security/urlguard.UrlRejected` now carries HTTP `status` and `Retry-After`; `services/pipeline` gained `resolve_identifier` kind `record`, abstract fallback for discovery jobs and the `analysis` handler; `services/sources.add_discovered`; `core/purge` clears the new tables; `identity/ratelimit` gained `ri_discover:user` and `ri_analyse:user`; `config` gained the discovery and analysis settings (validated).
- Phase 6 is **not modified**: `modules_bridge.py` imports its SearXNG provider and fetcher builder read-only.
- Earlier changes:
- `Consent` gained an additive `external` field; the provider router allows external/private-network providers for private data only with it.
- `config.py`: **fix** — invalid values for Phase 8–10 environment variables were silently ignored (`return Settings(...)` before the `problems` check). It now builds first, then raises `ConfigError` listing the problems. Existing valid configurations behave the same.
- `workspace/governance.delete_all_for_user` and `researchspace/service.delete` call the Phase 10 purge (lazy import).
- Tests updated for migration 0009 (`test_cli`, `test_workspace_projects`, `test_p9_deletion_upgrade`).
- Navigation: Research Intelligence changed from *planned* to *existing*; `tests/navigation.test.js` updated accordingly (assertions kept, not weakened).

## Configuration
All optional; see `config/env.example` (Phase 10 block, including the discovery and analysis variables). Key settings: `PREDICTAI_RI_VECTOR_BACKEND` (sqlite|qdrant), `PREDICTAI_RI_QDRANT_URL`, `PREDICTAI_RI_QDRANT_API_KEY` (env only, never stored), `PREDICTAI_RI_MALWARE_SCAN` (auto|off|required), upload/page/chunk/quota limits, `PREDICTAI_RI_ALLOW_PRIVATE_URLS` (ignored when hosted).

## AI providers
Embeddings and chat go through the existing provider router (Ollama local by default; Gemini/OpenAI only when the owner configured them **and** the workspace policy is `external_allowed`). Without any model the assistant answers extractively.

## Running
```
cd backend && pip install -r requirements.txt -r requirements-research.txt
python scripts/run_research_worker.py          # continuous; --once for one pass
```
Without the worker, sources stay "Waiting" (the UI says so).

## Performance (design limits, not benchmarks)
Chunk cap 3000/source; PDF page cap 400; SQLite vector search is brute-force cosine over a workspace's chunks (adequate for personal-scale corpora; use Qdrant beyond that). No benchmarks were run.


## AI provider configuration (summary)
- Chat/embeddings: unchanged router. Local Ollama by default (`PREDICTAI_AI_PROVIDER=ollama`, `PREDICTAI_AI_MODEL`, `PREDICTAI_AI_EMBED_MODEL` if you want meaning-based search). Gemini/OpenAI only if the owner configured them **and** the workspace policy is `external_allowed`.
- No model configured → the assistant answers with exact quotes and analyses are fully deterministic.

## Academic API configuration (summary)
| Provider | Key | Notes |
|---|---|---|
| Crossref | none | Set `PREDICTAI_RI_CROSSREF_MAILTO` to a real address (polite pool) |
| OpenAlex | **free key** `PREDICTAI_RI_OPENALEX_API_KEY` | Without it the adapter tries keyless and reports what happened; sent as `api_key` query parameter, never echoed |
| arXiv | none | ≥3 s between requests (enforced) |
| Semantic Scholar | optional `PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY` | `x-api-key` header; keyless is heavily rate-limited |
| SearXNG | your instance (Phase 6 settings) | Web results are labelled as web, not as scholarly records |
Default set: `PREDICTAI_RI_DISCOVERY_PROVIDERS=crossref,openalex,arxiv,semanticscholar`. **None of these was reached live from the build sandbox**; run `scripts/verify_phase10_live.py` (see TESTING.md).

## Deployment
```
cd backend && pip install -r requirements.txt -r requirements-research.txt
python scripts/run_research_worker.py      # required for imports AND analyses (analysis jobs run in this worker)
```
Migrations 0010 and 0011 apply automatically at start-up (`migrate`). Back up the SQLite file first as for any migration; it is additive except for the two table rebuilds, which copy all rows.

## Completion round 2 (R7, R8, R9) — files
- Backend: `migrations/0011_research_studio_reports.sql`; `research_intelligence/reports/{builder,export,service}.py`; `studio/{generators,service}.py`; `core/purge.py` and `services/notes.py` extended for cascade; `identity/ratelimit.py` (`ri_report:user`, `ri_export:user`); `api/v1/research_intelligence.py` (85 routes).
- Frontend: `modules/research_intelligence/{api.js,logic.js}`, `components/{ResearchNav,Paper,CompareTable,AnalysisResult,StudioAnalyses,StudioEvidence,StudioTools,ReportBlocks}.jsx`, `pages/{ResearchHomePage,DiscoverPage,LibraryPage,ChooserPages,ResearchSettingsPage,StudioPage,ReportPage}.jsx`; `ResearchWorkspacePage`, `AssistantPanel`, `ResearchIntelligencePage` upgraded; `app/routes.jsx`, `app/navigation.js`; `design/research-intelligence.css`.
- Tests: `test_p10_reports` 29, `test_p10_studio` 16, `test_p10_studio_api` 4; `tests/ri-logic.test.js` (14), `ri-labels.test.js` (+1).
- Dependencies: reportlab, python-docx, python-pptx (all free/open source; listed in `backend/requirements-reports.txt` from Phase 7, so install `-r requirements-reports.txt` on the machine that serves exports).
