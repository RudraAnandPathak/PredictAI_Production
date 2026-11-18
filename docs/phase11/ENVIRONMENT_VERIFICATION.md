# Technical environment verification

Every verification is separated by **layer** and records the exact environment. A result at one layer never stands in for another: a mocked integration test cannot prove live connectivity; a worker-process test cannot prove container isolation; an offline security test cannot prove a production deployment is secure.

## Environment used for every result in this release candidate
Linux 6.18 (x86_64), 2 vCPU, Python 3.13, Node 22, SQLite (bundled), **FastAPI stand-in over real Starlette/pydantic**, esbuild (not Vite), no network, no Docker, no browser, no macOS, no Ollama, no GPU. Date 2026-11-15.

## Layers
| # | Layer | What it can show | Run here? | Evidence |
|---|---|---|---|---|
| 1 | Offline automated tests | logic, HTTP behaviour on the stand-in, data integrity, migrations | **Yes** | full regression in `VERIFICATION_REPORT.md` |
| 2 | Mocked integration tests | the code handles provider/webhook/email responses it is *told* about | **Yes** (fakes, canned payloads) | e.g. discovery, assistant, schedule delivery; `PILOT_RESULTS.md` |
| 3 | Local Mac integration | real FastAPI/Uvicorn, real Vite build, real browser, macOS, Python 3.14 | **No** | procedure below |
| 4 | Live provider tests | Wikipedia/arXiv/OpenAlex/Crossref/SearXNG/Ollama/Qdrant/OAuth/SMTP actually reachable and behaving | **No** | `scripts/verify_phase10_live.py` (SKIPs here; SKIP ≠ PASS) |
| 5 | Security isolation tests | the Development Workspace boundary against hostile code | **Partly** (Linux layer only) | `test_p9_security` run against `local_trusted`; Docker/macOS: **No** |
| 6 | Release-environment tests | the actual deployment: TLS, proxy, hosting, persistence, backups on the real disk | **No** | none exists |

## Procedure for your Mac (record each result with date, macOS version, Python and Node versions)
1. **Install and offline suite** — `cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt -r requirements-research.txt -r requirements-reports.txt && python -m unittest discover -s tests` (real FastAPI now replaces the stand-in; some tests may need adjustment — report any failure verbatim). `cd frontend && npm install && npm test && npm run build`.
2. **Dependency audit** — `pip install pip-audit && pip-audit -r requirements.txt` (and each `requirements-*.txt`); `npm audit` in `frontend/`.
3. **Run the app** — `python -m app.cli migrate && python -m app.cli provision-owner ... && python -m app` and `npm run dev`; sign in; walk scenarios A–F and H from `PILOT_GUIDE.md`.
4. **Accessibility** — the six steps in `ACCESSIBILITY_REVIEW.md`.
5. **Live providers** — `cd backend && PYTHONPATH=. python ../scripts/verify_phase10_live.py --json live.json` (set the optional keys/URLs it lists). Each SKIP stays UNVERIFIED.
6. **Backup/restore drill** — `python -m app.ops.backup create --out ~/pai-backups`, `verify`, `restore ... --target ~/pai-restored`; start the app on the restored copy with a different port and data path; compare a project.
7. **Upgrade/rollback drill** — copy an old data directory, take a backup, `python -m app.cli migrate`, then restore and confirm the old version still starts.
8. **Isolation (only if you intend to enable execution)** — Docker Desktop installed; run `test_p9_security` against the Docker engine configuration; from inside a job try: reading `~/.ssh`, reading `.env`, connecting to the internet and to `localhost` services, forking many processes, allocating > the memory limit, writing > the disk quota, and escaping via symlinks/mount points. **Every attempt must fail and be logged.** Until this is recorded, execution stays **off**.
9. **Performance** — `cd backend && python tests/perf_baseline.py > perf-mac.json`, then repeat with real Uvicorn and 8/16/32 concurrent clients (e.g. `hey`/`wrk`) and a browser Lighthouse run.
10. **Send results back**, and update `RELEASE_GATES.md` rows only from recorded results.

## Rule
A gate row may move from *Not tested*/*Blocked* only when a result from the matching layer, with its environment, is written down in this directory.
