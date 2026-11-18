# Security review report

**Scope and honesty.** This is an offline review of the code and configuration in this tree, run in the build sandbox. It is **not** a penetration test, DAST scan, dependency-CVE audit, container/OS review or production-deployment review. Offline security tests cannot prove a production deployment is secure. Findings below were reproduced by a test or a scan; nothing is listed that was only suspected.

## Method (all re-runnable)
| Tool | What it does | Result |
|---|---|---|
| `test_p11_security_sweep` (4 tests) | enumerates **every registered API route** (445 method+path pairs on the stand-in) and probes it: anonymous callers must get 401 outside a reviewed public list; an ordinary signed-in user must get 403 on every owner and every Development-Workspace route; no probe may produce a 5xx; error bodies carry no stack traces or secrets. A new unreviewed public route fails the test. | pass (after fix F-1) |
| `scripts/security_scan.py` + `test_p11_security_scan` | secrets and key material, risky calls (`eval`/`exec`/`pickle`/`shell=True`/unsafe YAML…), config-template secrets, dependency lines without an upper bound | **0 high, 0 medium, 4 low** (bare `except: pass`), 13 individually reviewed allowances with a reason each, 0 unbounded dependencies |
| Existing suites | identity, workspace isolation, SSRF, injection, sandbox, Development Workspace | see per-area table |

## Findings
| ID | Severity | Finding | Status |
|---|---|---|---|
| F-1 | Medium | Development Workspace notebook create/load validated input **before** checking entitlement, so an ordinary user probing these routes received a validation error (422) instead of 403. No data was exposed, but permission must come first. | **Fixed** (`NotebookService` now calls `gate(principal)` first). Reproduced by the sweep; regression-guarded by it. |
| F-2 | Low | Four bare `except: pass` blocks (`analytics/service.py`, `intelligence/methods.py`, `workspace/parsing/datasets.py`, one test). They swallow errors around best-effort steps. | Open, accepted; listed by the scan. |
| F-3 | Info | `local_trusted` Development Workspace execution is Linux-namespace/rlimit confinement, **not** isolation against hostile code. | By design; see "Development" below. Gate stays Blocked. |
| A-1 | Accessibility | (non-security) control borders below 3:1 | Fixed — see `ACCESSIBILITY_REVIEW.md` |

False positives were investigated and recorded as allowances, not suppressed silently (e.g. the Dev kernel's `eval` of notebook cells inside the restricted worker, SHA-1 used only for non-security identifiers, test fixtures).

## Coverage by area (brief §5)
| Area | Evidence in this tree | Not covered |
|---|---|---|
| **Authentication** | hashed passwords, lockout with `Retry-After`, session idle/absolute expiry, single-use reset tokens, step-up for owner actions, no owner via the web: `test_identity_auth`, `test_identity_api`, `test_identity_registration`, `test_p8_bootstrap`; responses never leak secrets (`test_responses_never_leak_secrets`) | real SMTP, real Google/GitHub OAuth, TLS/HSTS at a reverse proxy |
| **Authorization** | route sweep (above); roles, grants, revocation taking effect on the next check: `test_identity_access`, `test_p7_collab`, `test_workspace_security_api`, foreign ids give the same 404 as missing ones | — |
| **Files and documents** | disguised/unsupported files rejected and nothing left behind, decompression bombs and unsafe paths (`test_workspace_uploads`); zip formats refuse bombs, traversal, macros; malware-detection hook; hanging/memory-hog/crashing parsers contained (`test_p10_security`); formula injection neutralised in downloaded CSV | real antivirus engine (hook tested with a fake), OCR hardening |
| **AI and research** | SSRF: private/rebinding/redirect-hop checks and pinned addresses (`test_p10_security`, `test_webaccess`); instruction-like text treated as data (`test_retrieval`, `test_world_service`); no secrets pasted/echoed (`test_providers`); per-person fetch rate limits; private-looking searches refused before sending (`test_research_combined`); citations verified as exact quotes | live providers, prompt-injection by a real model (tested with fakes and rules, not against real LLMs) |
| **Reports and exports** | headings, citations, versions; ownership and revocation of downloads (`test_p10_reports.Security`, `test_p7_collab`); missing library → clean 503 | viewing exports in real PDF/Word/PowerPoint viewers |
| **Development Workspace** | 20 release-blocking tests (`test_p9_security`: entitlement, cross-project/user isolation, no host secrets in the worker, network restrictions, limits, cancel/timeout, revocation stops and terminates jobs, git credentials protected, webhook signatures, quotas, logs free of private data) | **Docker and macOS isolation never run; no escape testing against hostile code** |
| **Dependencies and secrets** | scan: no secrets in tree or templates, all requirement lines bounded; release packager excludes `.env*`, databases, keys, `dev-secret-key` (`test_p11_release`) | **known-CVE audit (`pip-audit`, `npm audit`) — not run, no network → UNVERIFIED**; git history (this tree is not a repository) |

## Conclusions
- No reproduced high or medium issue remains open in the tested scope.
- **The security gate cannot be "Passed" for any release that depends on untrusted code execution, or on a dependency set nobody has audited for CVEs.** Both are listed as blocked/not tested in `RELEASE_GATES.md`.
- Before any external exposure: run `pip-audit -r backend/requirements*.txt` and `npm audit` on a networked machine; put the API behind TLS; set `PREDICTAI_ENV=production` with a strong `PREDICTAI_SECRET_KEY` (start-up refuses otherwise); run the sweep and scan on the exact build being released.
