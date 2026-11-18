# Phase 8 requirement status
Status vocabulary: **Implemented** (built and tested in the sandbox), **Existing** (delivered earlier, re-verified), **Partially verified** (see `docs/phase8/TESTING.md` for what was not run), **Not built** (deliberately out of scope).

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | Register, login, logout, hashing, persistent sessions, email validation, duplicate prevention, password reset, account management | Existing; re-verified | Phase 2; `test_identity_*`, `test_p8_api` |
| 2 | Normal / Special / Owner roles with the stated limits; no self-assignment | Existing + new HTTP tests; research limits Implemented | `test_identity_access`, `test_p8_api`, `test_p8_research_workspaces` |
| 3 | Permanent special access via hashed codes with rate limiting, brute-force protection, audit | Existing; re-verified over HTTP | `identity/access_codes.py` |
| 4 | Owner from `OWNER_NAME/EMAIL/PASSWORD/ACTIVATION_CODE`; no duplicate owner, no public owner registration, no frontend credentials | Implemented | `identity/bootstrap.py`, `test_p8_bootstrap` |
| 5 | Per-user isolation; owner access controlled and audited | Existing + extended to new tables; Phase 7 deletion gap fixed | `test_p8_deletion`, `test_p8_research_workspaces` |
| 6 | Extend the database without destroying data | Implemented | migration 0007 and its upgrade test |
| 7 | Research readiness (workspaces, six source kinds, conversations with source references, per-role limits, owner-manageable) | Implemented as foundation. **No research functionality** | `researchspace/`, `/research-workspaces` |
| 8 | All existing modules respect auth | Existing; route-wide sweep covers the new routes | `test_workspace_security_api` |
| 9 | Frontend pages and navigation for future modules; no redesign | Existing pages; one Planned navigation item added | `navigation.js` |
| 10 | Security: hashing, middleware, RBAC, expiry, rate limiting, CSRF, validation, secrets, audit | Existing; new code uses the same guards | |
| 11 | Tests | Implemented | `docs/phase8/TESTING.md` |
| 12 | Deliverables | Delivered | `docs/phase8/` |

Strict rules: no credentials exposed (`repr=False` settings, no logging of secrets, tested); no hardcoded secrets (owner values come only from the environment); no duplicate database structures (`audit_events` reused, `rw_` prefix instead of reusing Phase 6 names); roles cannot be self-assigned (tested over HTTP); no owner registration through the public page (no such route, tested); no frontend-only permission checks (the interface hides, the server enforces on every request).
