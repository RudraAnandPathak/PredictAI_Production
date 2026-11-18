# Phase 8: known limitations (read before relying on it)

## Not verified in the build environment
Same list as Phase 7: **real FastAPI** (tests ran against a stand-in), **`vite build`** (an esbuild import check passes, which is not a build), **any browser or accessibility audit**, **macOS / Python 3.14**, **live email, Slack and research providers**. In addition, **the owner bootstrap was never run against a real uvicorn start-up**: its logic and CLI are tested, the `lifespan` hook is covered only by import and by the shared service tests.

## Product limits
- **Research Intelligence does not exist yet.** There is no upload-to-workspace flow, no retrieval, no question answering, no notes screen. The API manages workspaces and their limits only. The sources, conversations, messages and notes tables are empty structures that Phase 10 will fill; nothing writes to them today except the tests.
- **No frontend for research workspaces.** The navigation shows "Research Intelligence" as Planned. Creating a workspace is possible through the API only; this is deliberate, to avoid a screen that does nothing useful.
- **Owner bootstrap code is a seed, not a shared secret.** One account, once, within 30 days. A weak code is refused rather than accepted.
- **Env values are only read at start-up.** Changing `.env` needs a restart (or `bootstrap-owner`). After the owner exists the values do nothing, by design.
- **Old residue is not cleaned.** The deletion fix applies to deletions from now on. Phase 7 data left behind by deletions made before this release (registry models/objects, reports, exports, schedules, exam material, shares) stays in the database and storage. It is invisible to everyone (the owning project is gone) but still occupies space. A one-off orphan sweep is recommended for Phase 9.
- **Share rows are revoked, never deleted** when what they point at is deleted. This keeps the permission history; it also means those rows remain.
- **Account deletion removes the person's own research workspaces, templates and memberships, but not collaborative workspaces others still use** (their membership is marked removed). A workspace created by a deleted person remains for its other members.
- **Audit retention.** `audit_events` and `own_events` are kept after account deletion because they hold identifiers and outcomes only. No retention limit is enforced yet.
- **The owner overview does not yet show research-workspace counts.** Counts-only reporting for the new tables belongs to the Phase 9 dashboard.
- **Special access is not "unlimited" for research.** Safeguard ceilings apply (defaults 200 workspaces, 500 sources per workspace) and are set by the server operator in `.env`, not in the website.
- **Source limits are enforced only where Phase 10 calls `ensure_source_slot`.** Nothing adds sources today, so no code path can exceed them yet, but the check exists and is tested.
- **Hosted `PREDICTAI_ENV` rules were not changed.** Behaviour of bootstrap under a hosted configuration (HTTPS-only cookies, production checks) relies on the existing config validation.

## Corrections to earlier documentation
- Phase 7 `KNOWN_LIMITATIONS.md` says rate limits are in-process counters. The code (`identity/ratelimit.py`) stores them in the database and shares them across processes. The code is right; that sentence is wrong.
- Phase 7 documentation implied project and account deletion was complete. It was not (see `IMPLEMENTATION.md`, "Phase 7 defect found and fixed").
