# Phase 6 test record

This file has two parts: the **corrective release** (research engine, web search, datasets, Wikipedia and arXiv), which is the current state, and below it the original **Phase 6 record**, kept as it was.

## Corrective release: what was run

Everything below ran in an offline Linux sandbox (Python 3.13, Node, Chromium). No real Wikipedia, arXiv, SearXNG or AI provider was reachable, so **none of the new tests contacts an external service**: they use recorded response shapes and local fake servers. The real `fastapi` package and `vite` could not be installed (no network); read "Not verified" before relying on any of this.

| Check | Result |
|---|---|
| Full backend suite (`python -m unittest discover -s tests`, Phase 1–6 plus corrective tests), final tree | **1394 tests, OK** (591 s). Phase 6 baseline was 1201: **193 new tests**, none removed or weakened |
| New backend test files | `test_research_queries` 28, `test_research_datasets` 30, `test_research_report` 19, `test_research_combined` 20, `test_research_corrective_api` 6, `test_phase6_corrective_contract` 2, `test_phase6_corrective_register` 10, `test_webaccess_wikipedia` 20, `test_webaccess_arxiv` 35, `test_webaccess_searxng` 22, plus 1 added to `test_webaccess_providers` |
| What those cover | web search (connectivity, parsing, invalid answers, timeouts, unavailable providers, duplicates, metadata); question understanding, multi-step planning, evidence, citations, conflicts, insufficient evidence; dataset research (numeric, categorical, mixed, missing values, large data, two datasets, statistical correctness against independent calculations, unauthorised access); dataset + web, document + web, dataset + document, dataset + model; Wikipedia (search, sections, tables, redirects, disambiguation, failures); arXiv (search, metadata, HTML and PDF full text, long and scanned papers, versions, rate limits, robots.txt); SSRF, injection, isolation and consent on the new endpoints; the requirements mapping and the release archive |
| Frontend (`node --test`) | **220 pass, 0 fail** (206 before; 14 new in `research-corrective.test.js`) |
| Frontend production build (`vite build`) | **Not run.** Vite is not installed and there is no network. The whole app was bundled with esbuild as a stand-in; that is not the production build |
| Browser run, Phase 6 screens (`scripts/e2e/s10.mjs`, Chromium) | **28 of 28 steps pass, no console errors** (the file as shipped) |
| Browser run, corrective screens (`scripts/e2e/s11.mjs`, Chromium) | **11 of 11 steps pass, no console errors**: dataset research, dataset + outside sources, citations that open a calculation or the original page, follow-up, search tools, small screens, isolation. It reads the fixture server's own request log to show only topic phrases and the one read page were requested, nothing from the data |
| Browser regression of earlier phases on the corrected build | e2e 47/47 (run with no sign-in provider configured, as that check intends; with fake OAuth credentials the one check "connected accounts are honest when no provider is configured" fails, as noted in the original record below), s3 11/11, s4 11/11, s5 11/11, s6 8/8, s7 15/15, s8 8/8, s9 11/11, a4 31/31, a5 62/62, all with no console errors |

The full backend run (1394) was made on the tree just before two last small edits: the contract test's check for private addresses (it no longer names a real address) and one line in `requirements-dev.txt` (`pypdf`). After them, the affected files and the registers were re-run, and a set of 183 backend tests plus the 220 frontend tests were run **from the extracted release ZIP** and passed.

The browser runs used a **stand-in** for FastAPI (same Starlette and pydantic behaviour as the earlier phases), an esbuild bundle in place of `vite build`, and local fixtures in place of real services.

### Bugs the tests and browser runs found, and the fixes
The list is in `CORRECTIVE_REPORT.md` section 1. Notably: a packaging rule that would have dropped the test fixtures from the ZIP (now a test), a contract gap that would have printed `[object Object]` on the plan screen, file/credential URLs failing with a server error, and two query-construction flaws found by the new query tests.

### Not verified
1. Live Wikipedia, live arXiv and a real SearXNG container (OUT-13). The first real check is **Research → project → Search tools → Test now**.
2. The real `fastapi` package, `npm install && npm run build` (real Vite) and macOS with Python 3.14 (OUT-14).
3. PDF extraction on real papers (OUT-15), answer quality on real questions (OUT-22), screen readers, browsers other than Chromium, performance under load.

### How to repeat
```
cd backend  && pip install -r requirements-dev.txt && python -m unittest discover -s tests
cd backend  && python -m unittest test_phase6_corrective_register test_research_queries test_research_combined test_research_datasets
cd frontend && npm install && npm test && npm run build
node scripts/e2e/s10.mjs ; node scripts/e2e/s11.mjs        # need the stack and fixtures in scripts/e2e/README.md
python docs/phase6/build_corrective_register.py            # after editing the mapping
python scripts/package_release.py ../PredictAI_Phase1_Phase2_Phase3_Phase4_Phase5_Phase6_Corrected_Integrated.zip
```

---

# Original Phase 6 record (before the corrective release)


Everything here was run in an offline Linux sandbox (Python 3.13). There was no network, so: no real search site, news feed or AI provider was contacted; the real `fastapi` package and `vite` were not installed; macOS and Python 3.14 were not available. Read the "Not verified" list before relying on any of this.

## Results
| Check | Result |
|---|---|
| Full backend suite (`python -m unittest discover -s tests`, Phase 1–6) | **1201 tests, OK** (341 s) |
| of which new in Phase 6 | 360 tests in 14 files (research engine 47, research service 52, research API 8, world units 40, world service 50, world API 9, retrieval 39, providers 36, web access 27 + 21, integration 13, security 8, contract 1, register 9), plus one new migration test |
| Security tests | `test_phase6_security.py` (route sweep: every Phase 6 route refuses anonymous callers, needs CSRF when it changes state, never answers for foreign or missing ids, never fails with a server error on hostile ids; logs hold no headlines, questions or secrets) and the SSRF, injection, isolation, secret and deletion tests inside the module files; all pass |
| Integration tests | `test_phase6_integration.py` (documents, datasets, Phase 4 findings, Phase 5 models; predictions labelled forecasts; no row-level data or storage keys in a bundle; other projects and people never receive evidence; deleting a dataset removes what was built from it): 13 pass |
| Frontend (`node --test`) | **206 pass, 0 fail** (Phase 6: 31 + 17 + 6 new tests; includes a test that the screens' filter names match the server's signature) |
| Frontend production build | **Not run.** `vite` is not installed and there is no network. The whole app was bundled with esbuild as a stand-in (compiles, 720 KB); that is not the production build |
| Browser run for Phase 6 (`scripts/e2e/s10.mjs`, Chromium) | **28 of 28 steps pass, no console errors** |
| Browser regression of earlier phases (same browser, Phase 6 build and backend) | e2e 47/47, s3 11/11, s4 11/11, s5 11/11, s6 8/8, s7 15/15, s8 8/8, s9 11/11, a4 31/31, a5 62/62 |
| Migration (Phase 5 database → 0005) | verified by test: rows, tables, indexes and checksums unchanged; only the research tables added; integrity clean; idempotent (`MIGRATIONS.md`) |
| Requirements register | `test_phase6_register.py` checks every implementation path, test name, status rule and generated file; 184 rows: Complete 177, Partial 4, Missing 2 (the Phase 7 deliverables), Unverified 1 (the production build) |

The earlier e2e script has one check ("connected accounts are honest when no provider is configured") that fails if the test server is started with fake OAuth credentials; run with none configured (as that check intends) it passes 47/47.

## What the browser run found (fixed)
The privacy form for a project started from the wrong baseline (unticking web research did nothing); the history search box had no accessible name; document search had no screen. Other early failures were mistakes in the script itself (strict selectors, assumptions about ordering) and were fixed in the script, not by loosening any assertion. Details: `IMPLEMENTATION.md`.

## How to repeat
```
cd backend  && python -m unittest discover -s tests
cd backend  && python -m unittest test_phase6_register test_phase6_contract test_phase6_security test_phase6_integration
cd frontend && npm test
node scripts/e2e/s10.mjs        # needs the stack and fixtures described in scripts/e2e/README.md
python scripts/package_release.py ../PredictAI_Phase1_Phase2_Phase3_Phase4_Phase5_Phase6_Integrated.zip
```

## Not verified (carried to `OUTSTANDING.md` as OUT-11)
1. `npm install && npm run build` (real Vite) and `npm test` under a clean install.
2. The real `fastapi` package: HTTP tests ran on a stand-in with the same Starlette and pydantic behaviour used by the earlier phases.
3. macOS and Python 3.14 (your machine) and Python 3.12 CI.
4. Any real service: Wikipedia, arXiv, a SearXNG instance, real RSS/Atom feeds, Ollama or another provider. Adapters, the fetcher and feed reading were tested against local fake servers and recorded responses of the formats those services document. A real service may behave differently.
5. Accuracy of the news heuristics (topic, entity, event grouping, tone) on real articles. They are labelled as heuristics in the product and have not been measured.
6. Cloudflare D1 (the SQL uses only constructs earlier migrations use).
7. Browser rendering other than Chromium; screen readers (labels and roles were checked by selector, not with assistive technology).
8. Performance under load: limits and bounded pools are tested for correctness, not for throughput.

## Critical Research Intelligence Upgrade (2026-11-14)

Run in the build sandbox (Linux, Python 3.13, no network; modelled Wikipedia/arXiv/SearXNG, stand-in PDF):

| Suite | Result |
|---|---|
| Full backend `unittest discover` (Phase 1–6 plus the new tests available at that time) | 1421 tests, OK (634 s) |
| `test_research_intelligence` (A–F and real-socket SearXNG) | 27 OK |
| `test_research_units` | 16 OK |
| `test_phase6_corrective_register` (after adding IU-01…IU-10) | 10 OK |
| Frontend `node --test` | 225 pass, 0 fail |

Tests changed on purpose: `test_research_queries.py` and `test_research_combined.py` (`arxiv_mode="always"`, two new plan keys). Test A–F assertions on Louis XVI years and the "half of the royal budget" wording were relaxed to what the stored passages guarantee (reign start 1774, no other king's years; a fiscal statement).

**Not run / unverified:** real FastAPI, real `vite build`, macOS and Python 3.14, live Wikipedia / arXiv / SearXNG / AI providers (use `scripts/live_research_check.py`), the real `french rev.pdf`, browser runs of the new screens. Details: `INTELLIGENCE_UPGRADE.md`.
