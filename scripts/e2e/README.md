# Phase 6 browser runs

Two scripts: `s10.mjs` (the original Phase 6 screens, 28 steps) and `s11.mjs` (the corrective work: research on uploaded datasets, dataset + outside sources, citations, follow-ups, search tools; 11 steps).

`s10.mjs` drives the Phase 6 screens in a real browser (Playwright, Chromium) through 28 steps: research, citations and exports, notes, document search, history, consent, web research
(with a prompt-injection page), project privacy, news feeds, events, trends, influence analysis, cancellation, AI providers, isolation between two people, small screens, dark theme and deletion.
It prints `ok` or `FAIL` per step and fails the run on any browser console error.

It talks only to local fixtures: `fake_web.py` serves a SearXNG-style search, two article pages (one containing an injected instruction and a number that disagrees with the other),
five RSS feeds and an Ollama-style model server on ports 9100 and 9101. Nothing here contacts the real internet.

## Run it
1. Backend (development settings, fresh database), with the fixtures allowed:
   `PREDICTAI_RESEARCH_WEB_ENABLED=true PREDICTAI_RESEARCH_ALLOW_PRIVATE_NETWORK=true PREDICTAI_SEARXNG_URL=http://127.0.0.1:9100/searx PREDICTAI_RESEARCH_CONTACT=you@example.com`
   then `python -m app.cli migrate`, create an owner with `python -m app.cli provision-owner --email owner@example.com --display-name Owner --password-stdin` (choose a password and export it as `E2E_OWNER_PASSWORD` for the script) and start the API on port 8000.
2. Frontend on `http://localhost:5173` proxying `/api` to port 8000 (`npm run dev`).
3. `python scripts/e2e/fake_web.py`
4. `npm i -D playwright && npx playwright install chromium` (once), then `node scripts/e2e/s10.mjs` and/or `node scripts/e2e/s11.mjs`.

The run registers new throwaway accounts each time (the sign-up rate limit applies: use a fresh database for each run). It is not part of CI.
The Phase 6 build recorded 28/28 steps passing against a stand-in web server in an offline sandbox; run it against the real stack on your machine to confirm.

## `s11.mjs` — corrective run
- Uploads `fx/airline.csv` (400 rows; its `note` column carries a marker string so the run can prove no cell value ever reaches an outside service) and a short text note.
- Only **SearXNG** (pointed at the fixtures) is switched on. Wikipedia and arXiv are never contacted by this run; their behaviour is covered by the mocked backend tests instead.
- `fake_web.py` also serves an airline-inventory page and a `/__seen` endpoint that records every query and page the fixtures were asked for. The run reads `/__seen` to check that only topic phrases and the one read page were requested.
- Run it against a fresh database, as with `s10.mjs`.
- The wording of the plan step changed in the corrective work (phrases are now listed per provider); `s10.mjs` was updated to match.

## What was and was not verified
- The Phase 6 corrective build ran `s10.mjs` 28/28 and `s11.mjs` 11/11, both with zero console errors, in an offline sandbox. That run used a **stand-in** for FastAPI (the sandbox could not install the real one) and the local fixtures above, and an esbuild bundle instead of `vite build`.
- Nothing here has been run against real Wikipedia, real arXiv, a real SearXNG container, a real AI provider, FastAPI itself, `vite build`, or macOS with Python 3.14. Run both scripts on your machine to confirm.
