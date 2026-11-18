# Phase 6 corrective report: research engine, web search and dataset intelligence

**Status: implemented and tested offline. Not declared production-ready.** The comprehensive quality assessment of the platform stays scheduled after Phase 12. Everything below was verified in an offline sandbox against local fakes (no real Wikipedia, arXiv, SearXNG or AI provider was reachable); the first thing to do on your machine is in section 6.

Requirement-by-requirement mapping: `CORRECTIVE_REQUIREMENTS.md` (each row names its code and tests). Setup and troubleshooting: `WEB_RESEARCH_SETUP.md`. Test record with counts: `TESTING.md`.

## 1. Root causes

| Reported problem | Root cause found | Evidence |
|---|---|---|
| **Web search is broken** | A stock SearXNG lists only `html` in `search.formats` and answers **403** to every `format=json` request; its bot limiter answers **429** to automated calls. PredictAI reported both as "blocked", so the person was sent to check the wrong thing. There was no shipped configuration and no way to test a provider from the app. | The original `searxng.py` (one fetch, no diagnosis). Now `docker/searxng/settings.yml` fixes both, and 403, 429, 404, refused, HTML-instead-of-JSON and garbage each have their own message with the fix (`test_webaccess_searxng.py`) |
| **Weak, irrelevant answers** | (1) The planner sent **one pile of loose keywords plus filler words** ("definition background", "facts statistics data") to every provider. Wikipedia ranked pages that merely contain those words; arXiv received `all:a AND all:b AND ...` with six or more terms, which almost never matches. (2) Wikipedia returned only the **lead paragraph** and arXiv only the **abstract**, so there was little to quote. (3) Every collected page was kept, relevant or not. | The original `planner.py`, `wikipedia.py`, `arxiv.py`, `collector.py`. Now `webaccess/queries.py` (28 unit tests), whole-article and whole-paper reading, relevance filtering in the collector |
| **Datasets not supported** | A selected dataset contributed only its stored profile text, kept when its words overlapped the question's words. Nothing was calculated **for the question** (no relationships with the named outcome, group comparisons, missing-value checks, or comparison of two datasets), so "which factors are most associated with X" could only be answered if some stored text happened to say so. | The original `context.py`. Now `research/datasets.py` runs the existing Phase 4/5 services |

Other defects found while doing the work, each now fixed and covered by a test:
- The plan screen would have printed `[object Object]` for outbound phrases once they became structured (a server/frontend contract gap, caught by the new contract examples).
- The direct "open a page" endpoint turned `file://` and credentialed URLs into a server error (502); they are now refused with 422 or 403 before anything is fetched.
- The SearXNG self-test judged a private local address differently from the real search path.
- Two research jobs running at once could break arXiv's one-request-at-a-time rule (the old spacing was per job); a process-wide gate now spaces every arXiv request.
- While building follow-up questions: a one-line follow-up found nothing because the planner split it into a fixed outline and evidence needed two matching words (a follow-up is now one part answered as asked); and a follow-up carried the earlier datasets even when it was not about data (they are now left out, and the plan says so).
- A question such as "summarise this document" sent the word "document" to every provider, and a comparison's "differences" part searched a phrase with the shared qualifier twice. Both were found by the new query tests and fixed.
- **The packaging script skipped any folder named `data`, which would have silently dropped the new test fixtures from the ZIP and made those tests fail on your machine.** The fixtures folder is now `backend/tests/fixtures`, and a test fails if the archive ever drops a file the tests read.

## 2. What was built

**Web search** (`webaccess/`): SearXNG request, parsing and diagnosis with named failures; per-provider query construction (`queries.py`); structured Wikipedia reading through the MediaWiki API (`providers/wikipedia.py`, `structure.py`: sections, tables, lists, equations, references, redirects, disambiguation, spelling suggestion, categories, revision, licence, labelled "encyclopedia (tertiary source)"); arXiv through its API (`providers/arxiv.py`: field queries, identifiers, versions with the latest marked, preprint label) with full text from arXiv's HTML version or the PDF (`papers.py`), scanned and heading-less PDFs reported as such; a process-wide arXiv gate (`gate.py`: 3 s for the API, 15 s for website pages, cool-down after a refusal). Failures are data: they appear on the result with a reason and never become invented results.

**Research engine** (`research/`): a topic-phrase planner with the level shapes, comparisons, multi-step splitting and follow-ups; relevance filtering in the collector; evidence extraction by section; per-claim citations re-checked against the saved source; five new quality checks (relevance, coverage, numerical accuracy, calculated-versus-external separation, source quality) beside the existing ones; a structured report (`report.py`): "in short", parts, what was calculated, outside sources by kind, a side-by-side comparison, uncertainty.

**Dataset research** (`datasets.py`, `intents.py`): the question's words decide which verified operations run on the selected dataset, through the owner's own access check: summary, quality and missing values, relationships (with the outcome the question names), correlation, column statistics, group comparisons, frequencies, trained models with importance and evaluation (Phase 5), earlier saved results. Every sentence is a fixed template over a stored result. The citation records the run, and the check rebuilds the sentence from it, so an edited result or sentence fails. Only aggregates reach the answer; a dataset too large for one run is sampled and the sample is stated; two datasets can be compared.

**Combined research**: dataset, Wikipedia, arXiv, web, documents, models and earlier bundles in one session. Calculated findings and outside claims are kept in separate lines, with a comparison that says it matches **words, not meaning**. An outside source never changes a calculated number, and an association is never called a cause.

**Frontend** (existing design system): the New research page offers datasets, documents and every available provider, and shows the exact phrases each provider would be sent. The result page has a report card, an outside-source list, citations that open the calculation or the original page, and a follow-up form. A **Search tools** tab tests each provider, searches one on its own and opens an article or paper section by section.

**Security and privacy** (same architecture, extended): all dataset access goes through the existing `get_owned` checks; web research needs the server, person and project switches and the approved phrases; only words of the question can form a phrase (never a row, cell, file name or document text); retrieved text is quoted, never obeyed; the new direct endpoints refuse private-looking phrases and private-address, `file` and credentialed URLs before anything is sent.

**No migration, no new runtime dependency, nothing paid.** One test-time change to flag: `pypdf` (free, already listed as optional in `requirements-data.txt`) was added to `backend/requirements-dev.txt`, because the tests that read PDFs (uploaded documents and arXiv papers) cannot run without it, and CI installs only the dev file. Provider defaults on the New research page changed: all available, allowed providers are now selected by default (you can untick any), so a question uses Wikipedia and arXiv as well as the web when you allow them.

## 3. How it was verified

Counts and results are in `TESTING.md`. In short, all of this ran in the offline sandbox:
- the full backend suite (Phase 1–6 plus the corrective tests) and the full frontend suite;
- the corrective tests use recorded response shapes and local fake servers; none contacts an external service;
- two browser runs in Chromium against the built app, a stand-in API server and local fixtures: `s10.mjs` (the Phase 6 screens) and `s11.mjs` (the corrective screens), both with zero console errors. The second also reads the fixture server's own log to show that no value from the dataset reached any outside request.

## 4. Not verified

1. **Live Wikipedia, live arXiv and a real SearXNG container.** Their behaviour is tested from the documented response formats only. A real service may differ.
2. **The real `fastapi` package, `vite build` (the production build) and macOS with Python 3.14.** The sandbox had no network, so none could be installed. The browser runs used a stand-in for FastAPI and an esbuild bundle in place of `vite build`; they are not a substitute.
3. Answer quality on real questions (there is no reference set), PDF extraction on real papers, and screen readers.

## 5. Limitations

PDF papers have no maths markup (equations are approximate) and multi-column pages can interleave lines. The arXiv gate is per process. The comparison of your data with an outside source matches words, not meaning. A column is recognised by its full name in a question. A follow-up with web research off can only use the sections the earlier question read. General web search needs a SearXNG you run. Answers are quoted passages and fixed sentences: accurate and cited, but plain. The full list is `OUTSTANDING.md` (OUT-13 to OUT-22).

## 6. Next step

On your machine, once: `pip install -r backend/requirements-dev.txt`, `npm install && npm run build`, run both test suites, start SearXNG (`docker/searxng`), set the three `.env` values in `WEB_RESEARCH_SETUP.md`, then open **Research → a project → Search tools → Test now**. That click is the first real check of Wikipedia, arXiv and SearXNG. Phase 7 has not been started.
