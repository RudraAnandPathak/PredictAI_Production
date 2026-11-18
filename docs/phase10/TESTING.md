# Phase 10 — Testing record
Environment: Linux sandbox, Python 3.13, FastAPI **stand-in** (real Starlette/pydantic), no network, esbuild import check (not `vite build`).

| Suite | Tests | Result |
|---|---|---|
| `test_p10_security` | 18 | passed |
| `test_p10_ingestion` | 26 | passed |
| `test_p10_retrieval` (incl. Qdrant against a local fake following the documented REST shapes) | 22 | passed |
| `test_p10_assistant` | 22 | passed |
| `test_p10_api` | 6 | passed |
| `test_p10_upgrade` (now 9→10 upgrade + data preservation + CHECK widening) | extended | passed |
| `test_p10_discovery` (R2: records, merge, ranking, each provider adapter with canned payloads, pacing/backoff, statuses, history, library, import) | 38 | passed |
| `test_p10_discovery_api` (R2 over HTTP, key never in a response) | 3 | passed |
| `test_p10_reasoning` (R5 engine rules + whole flow: quotes are exact stored text, cache, cancel, isolation, quotas, policy, model failure fallback, deletion) | 41 | passed |
| `test_p10_reasoning_api` (R5 over HTTP) | 4 | passed |
| `test_p10_reports` (R8: builder rules, quotes exact, references from metadata only, edit validation, immutable evidence, regenerate keeps author text, versions/restore, source deletion redaction, 4 exports read back, missing library → 503, isolation) | 30 | passed |
| `test_p10_studio` (R7: findings, links, groups, outlines, generators, evidence explorer, activity, home, cascade, isolation) | 16 | passed |
| `test_p10_studio_api` (R7/R8 over HTTP incl. export headers) | 4 | passed |
| Frontend `node --test` (logic, navigation, route order, API wrappers, labels) | 269 | passed |
| esbuild bundle of `src/app/routes.jsx` (all pages) | — | bundles (not `vite build`) |
| Full backend regression | 1901 | **passed (0 failures)** |

Commands:
```
cd backend && PYTHONPATH=.:tests python3 -m unittest discover -s tests -p "test_*.py"
cd frontend && node --test
```
## Not executed (UNVERIFIED)
Real FastAPI/uvicorn, `vite build`, any browser, macOS/Python 3.14, live Qdrant, live Gemini/OpenAI/Ollama, live Crossref/arXiv, real HTTPS fetch pinning, real ClamAV. Tests for these use fakes or injected fetchers.

## Live verification (you run this; I could not)
The sandbox has no network, so every provider/Qdrant/Ollama/browser check above used fakes. To turn UNVERIFIED into verified, on your Mac:
```
cd backend
export PREDICTAI_RI_CROSSREF_MAILTO=you@example.com            # a real address
export PREDICTAI_RI_OPENALEX_API_KEY=...                       # free key from OpenAlex (optional but needed for OpenAlex)
export PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY=...               # optional
export PREDICTAI_RI_QDRANT_URL=http://127.0.0.1:6333           # e.g. docker run -p 6333:6333 qdrant/qdrant
export PREDICTAI_AI_MODEL=<chat model you pulled>  PREDICTAI_AI_EMBED_MODEL=nomic-embed-text
cd frontend && npm install && cd ../backend
PYTHONPATH=.:tests python ../scripts/verify_phase10_live.py --json /tmp/verify.json
```
It checks: Crossref/OpenAlex/arXiv/Semantic Scholar searches and a DOI lookup; Qdrant upsert, filtered search, owner isolation and delete on a temporary collection; Ollama reachability, embeddings and chat; a full live flow on a throw-away database (discovery → save → import → worker → analysis); `node --test`; `npm run build`. Output is PASS / FAIL / SKIP per check; **SKIP stays UNVERIFIED**. Send the output back.

Manual checklist the script cannot do (browser/uvicorn): start `uvicorn app.main:app` and `python scripts/run_research_worker.py`; sign in; create a workspace; upload a real PDF; confirm it becomes Ready; ask a question and click a citation; confirm a source with no relevant text gives "not enough evidence"; check the layout at phone width and with keyboard only. Discovery and Analysis have **no UI yet** (R9 later round), so use the API (`/docs`) for those until then.

In-sandbox state of the script: it runs end to end and every live check SKIPs with its reason (verified here); no live PASS exists yet.

## Browser checklist (UNVERIFIED — I could not run a browser)
Start `uvicorn app.main:app`, `python scripts/run_research_worker.py` and `npm run dev`; then:
1. Research section bar shows eight links; the current page is highlighted; Tab reaches every link; it wraps or scrolls at narrow widths.
2. Discover: search keywords; each provider shows its own status; narrow by words / year / open access without a new request (watch the network tab: only `GET /discovery/searches/{id}`); Save to library.
3. Library: tick 2–6 papers → Compare shows a table; Add to workspace (try with and without full text).
4. Workspace: upload a real PDF (progress bar), wait for Ready, ask a question, click a citation `[1]` → exact passage; Save as evidence; pick a subset of sources and confirm the status line says so; reopen an earlier conversation.
5. Studio: select ≥2 sources → Start analysis → watch progress → read themes; save findings, connect two, group them; create an outline; Generate research questions / hypotheses; Create report.
6. Report: edit a text block (its label changes to "Written or edited by you"); confirm quotes and tables show no edit box; Save; Regenerate (your text stays); Restore an old version; click `[n]` → source card.
7. Export PDF, Word, PowerPoint and Markdown; open each: notice present, quotes marked, references numbered, no invented reference.
8. Delete a source used by a report: its quotes disappear from the report; regenerate is refused with a clear message.
9. Screen reader / zoom 200% / keyboard-only pass over steps 1–7; check colour contrast in light and dark themes.
