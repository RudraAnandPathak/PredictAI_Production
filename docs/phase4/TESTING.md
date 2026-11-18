# Phase 4 testing record (2026-11-12)

| Suite | Result | Notes |
|---|---|---|
| Backend, real environment | 395 run, OK, 6 skipped | The 6 skipped are the HTTP-layer modules that need FastAPI (not installable offline) |
| Backend, with the FastAPI stand-in | 471 run, OK | Stand-in = real Starlette/pydantic with a minimal FastAPI shim: **a stand-in result, not real FastAPI** |
| Frontend `node --test` | 98 / 98 | 10 new (API wrappers, chart math, filters, routing) |
| Production bundle | built with **esbuild**, not `vite build` | Offline; run `npm run build` yourself |
| Browser e2e (Playwright, Chromium) | Phase 4 flow 31/31; Phase 2 regression 47/47; Phase 3 flows 75/75 | Against the esbuild bundle + stand-in API |

New backend tests: engine 28 (reference-value truth against independent computation, edge cases, determinism, subprocess equivalence,
environment scrubbing, time and memory limits, oversize refusal, plan validation, static no-eval check, evidence contract),
service 13, assistant 29, HTTP 9 (stand-in), plus a Phase-3→4 upgrade test.
Security: cross-user and cross-project guessing of dataset, run, rows, export, insight and conversation ids returns an identical 404
(unit tests and browser).

## Not verified here
Real FastAPI 0.162.5 · real `vite build` · live Ollama · the macOS memory watchdog (written and logic-tested here; the real macOS behaviour needs your run; the
row/cell/time limits do) · Google/GitHub/SMTP (unchanged from Phase 3).

## Correction after the macOS run (2026-11-12)
Two tests failed on macOS (Python 3.14): memory isolation and `.env` hygiene.

**Memory isolation — root cause.** The analysis subprocess relied only on `RLIMIT_AS`. macOS does not enforce it (the call may succeed or fail and
allocations are never refused), so a 40 MB limit stopped nothing. The old code also swallowed the refusal silently and still listed "address space" as applied.
**Fix:** `analytics/sandbox.py` + `_boot.py`.
1. A one-time **probe** checks whether the kernel really refuses an allocation under `RLIMIT_AS`; the kernel limit is applied, and reported, only if it does (Linux: yes).
2. A **parent-side watchdog** samples the child's resident memory every 20 ms (`/proc` on Linux, `ps` elsewhere) and kills its whole process group above the limit. This works on macOS.
3. A **table-size preflight** refuses a table that cannot fit (3x its in-memory size vs the limit) before computing.
4. An **after-run check** discards a result whose reported peak memory exceeded the limit (catches spikes between samples).
5. Limits are now applied in the worker before heavy imports (no `preexec_fn`, which is unsafe with threads); a kernel-limit failure while importing is classified as `ANALYSIS_MEMORY`.

**Is memory protection enforced?** *Hard* only where the probe proves the kernel limit works (Linux). On macOS it is *monitored, best effort*: runs are killed when sampled above the limit,
but an allocation that outruns a 20 ms sample can overshoot briefly. The evidence block and `/analytics` isolation report state which mode applied.
Time (CPU and wall-clock), file-size and open-file limits, row/cell caps, the scrubbed environment and the private table copy are unchanged.

**`.env` hygiene — root cause.** The test required the working directory to contain no `.env`, which fails for any developer who legitimately has one.
**Fix:** the test now verifies what matters: git ignores `.env*`, databases, data and keys while templates stay trackable; no secret/database file is tracked; and the new
`scripts/package_release.py` never packages them (tested with a fake `.env` that is left unmodified). Templates are still checked for placeholder-only values.
Build a release with `python scripts/package_release.py`.

Results here: backend 491 OK with the FastAPI stand-in (Linux, Python 3.13). New tests: sandbox 15, engine +3, hygiene +4. macOS itself could not be run here.
