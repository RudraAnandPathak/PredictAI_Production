# Phase 10 — Status (core + completion rounds 1 and 2: R1–R11 built; production verification pending)

## Fully implemented and tested in the sandbox (fakes for every network call)
- Core: R1 workspaces/sources, R3 ingestion, R4 hybrid retrieval, R6 citation-verified assistant, R10 security, R11 tests.
- **R2 discovery**: Crossref, OpenAlex, arXiv, Semantic Scholar, SearXNG adapters; merge/dedupe; ranking; filters; history; library; compare; APA/BibTeX; import (abstract or lawful open-access full text); honest per-provider status.
- **R5 multi-source reasoning**: shared findings, *possible* contradictions, magnitude differences, methods, gaps, evidence grades, timeline, synthesis, optional verified model narrative; background job with progress and cancel.
- **R7 Research Knowledge Studio (backend + UI)**: saved findings with kind and comment, connections (supports / contradicts / extends / related), groups, outlines (3 levels, 200 entries), rule-based research questions and hypotheses (each with its quoted basis, labelled unvalidated), literature-review draft, evidence explorer, activity feed. No duplicate workspace tables.
- **R8 reports (backend + UI)**: 7 report types built only from a finished analysis; PDF, DOCX, PPTX and Markdown export (each file read back in tests); exact-quote citations; references only from stored metadata; every block labelled source / analysis / author; quotes and tables immutable; editing, regeneration that keeps your own text, 20-version history with restore; source deletion redacts quotes from reports; fixed "not peer reviewed" notice.
- **R9 UI**: eight-section navigation (Research Home, My Workspaces, Discover Research, Source Library, Research Assistant, Research Studio, Research Reports, Research Settings); Discover with advanced search, per-provider status and filter/sort/page over a stored search; Library with compare and add-to-workspace; three-panel workspace with source-scope picker, conversation history, citation navigation, upload progress; Studio (analyses, findings, connections, groups, outlines, generators, report creation); report editor and exports; Settings (limits, providers, AI policy, history). Loading, error and empty states throughout; labelled controls, `aria-current`, live regions, keyboard-operable.
- **Section 8 AI integration**: no key ever reaches the browser (Settings shows only "key configured"); `local_only` default; external use needs per-workspace opt-in; the extractive fallback remains when no model is available.

## Test results (executed)
- **Full backend regression: **1901 tests, 0 failures** (755 s; run before the last two small edits, which were re-tested separately: the 503 error test and the verify script) **
- New this round: `test_p10_reports` 30, `test_p10_studio` 16, `test_p10_studio_api` 4; route list, upgrade (10→11, additive) and migration-count assertions extended.
- Frontend: `node --test` **269/269** (255 before; +14 logic/route-order tests, +1 API wrapper test, 1 contract assertion updated for the new `source_ids` field). esbuild bundles `src/app/routes.jsx` without errors.
- `scripts/verify_phase10_live.py` runs end to end here; every live check SKIPs (no network). A new check builds a report from the live analysis and exports all four formats.

## UNVERIFIED (never executed against the real thing)
- **The UI has never been rendered.** No browser and no `vite build` here: layout, CSS, focus order, keyboard use, screen-reader behaviour, responsive breakpoints and download-through-browser are untested. Only bundling and pure-logic tests ran.
- Live Crossref, OpenAlex, arXiv, Semantic Scholar, SearXNG; live Qdrant; live Ollama/Gemini/OpenAI; real HTTPS pinning; real ClamAV; real FastAPI/uvicorn; macOS / Python 3.14 / CI Python 3.12.
- Export layout quality (opening the PDF/DOCX/PPTX in real viewers) and PDF fonts for non-Latin text.
- Performance under load (no benchmarks were run).
- Run `scripts/verify_phase10_live.py` on your Mac and use the manual browser checklist in TESTING.md.

## Not built
- OCR for scanned PDFs; PDF table extraction; semantic (non-lexical) clustering for R5; load/performance benchmarks; automated browser and accessibility tests (axe/Playwright); model-written rewriting inside Studio (generators are rule-based; only the R5 narrative can use a model); the Google Stitch redesign (planned later).

## Known limitations / tech debt
- R5 grouping is lexical; contradictions are only "possible"; design/sample-size detection uses cue phrases and may be wrong or absent. Evidence grades are a reading aid.
- Abstract-only or metadata-only sources limit every analysis and report; both say so.
- Hypotheses and research questions are suggestions assembled from quotes, not validated findings.
- OpenAlex needs a free key; Semantic Scholar is rate-limited without one. Rate limiting is per process.
- Analyses need the worker running. Reports and exports need `requirements-reports.txt` (reportlab, python-docx, python-pptx); without them export returns a clear 503.
- PDF export replaces characters outside its bundled font with "?" (DOCX/PPTX/MD keep full Unicode).
- Compare in Discover works on saved papers only (it takes library ids).
- Report editing is block-level (text and bullets); there is no rich-text formatting and no collaborative editing.
- No OCR; built-in embedding is lexical unless an embedding model is configured; SQLite vector search is brute force; subprocess isolation is resource limiting, not a sandbox.

## Recommended next step
On your Mac: install requirements, run `scripts/verify_phase10_live.py`, start uvicorn + the worker + `npm run dev`, and walk the checklist in TESTING.md (especially Discover → Library → workspace → Studio → report → export). Send me the output and any screenshots; I will fix what the real browser shows before any redesign or OCR work.
