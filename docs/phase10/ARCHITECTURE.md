# Phase 10 — Research Intelligence: Architecture

Scope so far: **R1, R3, R4, R6, R10, R11** (core delivery) plus **R2 (discovery) and R5 (multi-source reasoning)** (completion round 1; backend, API, tests, docs). R7 (Studio), R8 (reports) and the full R9 UI (including Discover and Analysis screens) are **not built** (see STATUS.md).

## Decisions (chosen by the project owner)
| Topic | Decision |
|---|---|
| Storage | Pluggable. SQLite (FTS5 + stored embeddings) is the default. A Qdrant adapter exists and is **UNVERIFIED** against a live server. PostgreSQL is future work. |
| Workspaces | The Phase 8 `rw_*` tables are reused and extended; Phase 10 adds `ri_*` tables (migration `0009_research_intelligence.sql`). |
| Migrations | `0009_research_intelligence.sql` (core) and `0010_research_discovery_reasoning.sql` (R2/R5). 0010 widens two CHECK constraints (`ri_usage.kind`, `ri_jobs.kind`) the only way SQLite allows (copy, drop, rename); existing rows are copied unchanged (tested). |
| Money | ₹0: everything runs on free/open-source software. No paid dependency was added. |

## Modules
Backend `backend/app/modules/research_intelligence/`:
- `security/` — `urlguard` (SSRF guard), `files` (upload validation), `malware` (ClamAV if present), `injection` (prompt-injection flags).
- `ingestion/` — `extract` (PDF/DOCX/TXT/MD/CSV/XLSX/HTML → blocks), `isolated` + `extract_main` (extraction in a resource-limited subprocess), `chunker`, `intelligence` (extractive summary/keywords/statistics/claims).
- `embeddings/provider.py` — built-in hashed embedding (lexical, labelled as such) or router-provided models.
- `retrieval/` — `vectorstore` (SQLite + Qdrant), `search` (FTS5 + vector, RRF fusion, heuristic rerank, per-source cap).
- `citations/verify.py` — sentence-level answer verification.
- `reasoning/assistant.py` — conversations, scope, `ask`.
- `reasoning/facts.py`, `engine.py`, `analysis.py` — **R5**: per-source fact extraction (cached in `ri_source_facts`), pure comparison functions, and the background analysis service.
- `discovery/` — **R2**: `records` (normalised `Record`, merge, rank), `providers` (Crossref, OpenAlex, arXiv, Semantic Scholar, SearXNG adapters + `ProviderGate` pacing), `citation` (APA/BibTeX from present fields only), `service` (search, history, library, compare, import).
- `modules_bridge.py` — the only place that touches Phase 6 (read-only: reuses its SearXNG provider and fetcher builder; Phase 6 is unchanged).
- `services/` — `sources`, `jobs`, `pipeline`, `notes`, `search`.
- `core/` — `common` (limits, usage, audit events), `purge` (deletion cascade).
- `workers/worker.py` + `backend/scripts/run_research_worker.py`.
API: `backend/app/api/v1/research_intelligence.py` (53 routes under `/api/v1/research-intelligence`).
Frontend: `frontend/src/modules/research_intelligence/` (`api.js`, `labels.js`, 4 components, 2 pages) and `src/design/research-intelligence.css`.

## Data flow
1. **Add**: upload (raw body, size capped while reading, validated by magic bytes/zip-bomb/macro/traversal checks, optional malware scan) or reference (URL/DOI/arXiv). A `ri_sources` row (same id as `rw_sources`) and a job are created. Nothing heavy runs in the request.
2. **Worker** claims the job (lease + heartbeat, retry with backoff, cancel, `WORKER_LOST` recovery): extract in an isolated subprocess → chunk (≈220 words, overlap 40, with page/sheet/section locators) → flag injection-like text → FTS5 index → embed → vector store.
3. **Ask**: condense follow-up → hybrid retrieval (FTS5 BM25 + vector, reciprocal rank fusion k=60) → heuristic rerank → context selection (per-source cap) → answer.
4. **Answer modes**: `model` (every sentence must carry a valid `[Sn]` label, ≥50% content-word overlap with the cited passage, and any number must appear in it; failing sentences are removed and counted), `extractive` (exact quotes; no model wrote it), `insufficient_evidence` (model not called). Quotes are exact substrings of the stored chunk.
5. **Delete**: `purge_source/workspace/user` remove rows, FTS entries, stored files and vectors (tolerant of a down vector store).

## Honesty rules built into the design
- The built-in embedding (`builtin-hash-384`) is **lexical**; only router-provided models count as "semantic", and the UI/API say which one is used.
- The rerank score is a transparent heuristic, **not a probability**.
- Metadata-only sources (DOI without abstract) cannot be searched or cited; the UI says so.


## R2 — Discovery
1. `POST /discovery/search` runs the chosen providers **in parallel threads**. Each adapter turns the provider's JSON into normalised records and reports an honest status. `ProviderGate` (process-wide) enforces each provider's polite interval (arXiv ≥3 s), waits at most 4 s, otherwise reports `rate_limited` with `retry_after`, and cools a provider down after a 429/5xx.
2. Records are merged by DOI, arXiv id, or normalised title + first author (±1 year); the merged record remembers every provider that returned it. Ranking = reciprocal-rank agreement across providers + query-term coverage + metadata completeness + a small log-citation bonus. It is a heuristic, **not a probability** (the API says so).
3. Filters that a provider cannot apply server-side (author, year, type, field, open access) are applied afterwards; records with unknown values are **hidden, not guessed**, and the count is reported in `notes`.
4. The merged top 150 are stored in `ri_discovery_searches`; paging, filtering and sorting read that row. Saving to the library and importing take only `search_id + rid`/`library_id`: the server copies its own stored record, so the browser can never inject metadata.
5. Import creates a normal source (`origin='discovery'`) and a job. Full text is fetched only when the provider reported an open-access PDF (Crossref: a Creative Commons licence **and** a PDF link) and the person asked; any failure falls back to the abstract with an explicit warning. Nothing is ever fetched from a paywalled or unknown location.
6. Provider facts: OpenAlex now asks for a free API key (`PREDICTAI_RI_OPENALEX_API_KEY`; without it the adapter tries keyless and says so); Semantic Scholar's key is optional; Crossref/OpenAlex use the polite `mailto` from `PREDICTAI_RI_CROSSREF_MAILTO`. Keys go in a header/query parameter only at call time and are never stored, logged or returned.

## R5 — Multi-source reasoning
An analysis is a background job (`ri_jobs.kind='analysis'`, same queue, lease, cancel and recovery as ingestion). Stages with progress: read sources (facts cached per source version) → common findings and contradictions → methods → gaps → evidence grades → timeline → synthesis → optional model narrative.
- **Facts** (`facts.py`): exact sentences classified by cue phrases into findings, methods and limitations, plus polarity (up/down/no-effect), percentages, sample size and study design. Flagged (injection-like) passages are never used.
- **Grouping** (`engine.py`): findings are grouped across sources by shared content-word stems (Jaccard ≥ 0.40 and ≥ 3 shared). A group with opposing polarity across sources is a *possible contradiction*; same direction with a >50% difference in a shared percentage is a *magnitude variation*; otherwise it is an agreement. Different wording alone is never a contradiction.
- **Evidence grade**: from the stated design (systematic review/RCT strong, cohort/quasi moderate, survey/benchmark/qualitative limited, theory weak, not stated → ungraded), downgraded for very small samples or abstract-only access. Theme confidence needs ≥3 agreeing sources with ≥2 strong/moderate designs and no dispute.
- **Synthesis** is hierarchical (per source → themes → computed overview). A model narrative is optional (`use_model:true`): it goes through the router with `Consent(external=…)` taken from the workspace policy, then through the same `verify_answer` as assistant answers; unsupported sentences are removed, and if nothing survives the deterministic result stands alone.
- Every item carries exact quotes with source label, chunk id and locator (tested: each quote is a substring of the stored chunk). Deleting a source deletes the analyses and cached facts that quote it.
- Honest limits are in every result (`limitations`): grouping is lexical, not semantic; contradictions are only "possible".
