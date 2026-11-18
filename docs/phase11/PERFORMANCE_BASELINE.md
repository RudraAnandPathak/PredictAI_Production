# Performance baseline

**What this is:** a measured baseline of the application stack **in-process on the build sandbox** (2 vCPU, Linux, Python 3.13, FastAPI *stand-in* over real Starlette, local-disk SQLite, synthetic data). Script: `backend/tests/perf_baseline.py`; raw numbers: `perf_baseline.json`.
**What this is not:** a measurement of the real Uvicorn/FastAPI server, a Mac, a network, a browser, live providers, a model, or a load test. Do **not** extrapolate beyond the tested concurrency (max 8 simultaneous clients) or data sizes. **One run; two runs on the same machine differed by 10–40 % on tail latencies** (an earlier run, before a timing fix in the script, is not comparable). Use it to detect regressions on the same machine, and to know where to look first.

## API latency (sequential, 100 requests each)
| Endpoint | p50 | p95 | max |
|---|---|---|---|
| `GET /health` | 2.13 ms | 2.55 ms | 3.75 ms |
| `GET /research-intelligence/limits` | 6.43 ms | 7.89 ms | 9.47 ms |
| `GET /research-intelligence/workspaces` | 6.75 ms | 7.86 ms | 8.29 ms |
| `GET /research-intelligence/home` | 6.54 ms | 8.5 ms | 10.01 ms |
| `GET /projects` | 7.06 ms | 8.54 ms | 9.58 ms |
| `POST` note (write) | 7.96 ms | 9.68 ms | 11.35 ms |

## Concurrency (each client alternates one read and one write; all clients use the same account in separate sessions; latency is per request, read and write timed separately)
| Clients | Requests | Throughput | p50 | p95 | max | Errors |
|---|---|---|---|---|---|---|
| 1 | 50 | 118.8 req/s | 8.01 ms | 9.39 ms | 9.5 ms | 0 |
| 4 | 200 | 142.9 req/s | 26.78 ms | 36.87 ms | 46.11 ms | 0 |
| 8 | 400 | 127.9 req/s | 59.34 ms | 85.83 ms | 117.0 ms | 0 |

**Reading:** throughput stays at roughly 120–145 req/s from 1 to 8 clients while latency grows roughly in proportion — the signature of a single-process, CPU-bound server in which requests queue behind each other. This is an inference from the shape of the data, not a profiled finding. Writes are serialised by SQLite regardless.

## Documents, research and exports
| Operation | Size | Time |
|---|---|---|
| Ingest + chunk + index a synthetic document | 87 k characters → 86 chunks | 0.146 s |
| Hybrid search over it | 50 queries | p50 2.9 ms, p95 3.73 ms |
| Analysis across 3 sources | — | 0.016 s |
| Report build | — | ~1 ms |
| Export Markdown / DOCX / PDF / PPTX | — | 0.001 / 0.128 / 0.087 / 0.095 s (7.5 / 38.7 / 70.6 / 47.0 KB) |
| Backup + restore-and-verify | 5 files, 2.2 MB | 0.026 s / 0.011 s |

## Tabular data
| Rows | File | Read + profile | Process peak RSS |
|---|---|---|---|
| 1 000 | 0.02 MB | 0.02 s | 238 MB |
| 100 000 | 3.05 MB | 1.513 s | 300 MB |

(RSS includes the interpreter and all loaded modules; it is a ceiling for the whole process, not the cost of the file.)

## Frontend (measured by bundling, not in a browser)
| Item | Size |
|---|---|
| One JavaScript bundle (esbuild, minified, ESM) | **960 KB** raw, **264 KB** gzip |
| CSS | 38.6 KB raw, 8.3 KB gzip |
Built with esbuild, not `vite build`; Vite/Rollup output will differ somewhat. **No route-level code splitting is used** (one dynamic import in the source), so every user downloads every module, including Research Intelligence, Development Workspace and Owner pages. **Recommendation (not implemented):** lazy-load route groups. Time-to-interactive, render times and large-table behaviour were **not measured** (needs a browser).

## Bottleneck analysis
1. **Single process + SQLite:** the first limit to appear as users grow; fine for a handful of concurrent users, untested beyond 8.
2. **Large first download** (above).
3. **CPU-bound work** (training, parsing, PDF export) runs in the app host; limits exist (workers, per-person quotas) but were not stress-tested.
4. **Not measured at all:** large PDFs/OCR, multi-hundred-MB datasets, model training time, vector search at scale, report export of very large reports, database size growth.

## Untested and why
Real server throughput (needs uvicorn), browser performance, network latency, live AI/search providers, long soak runs, memory growth over time, > 8 concurrent users.
