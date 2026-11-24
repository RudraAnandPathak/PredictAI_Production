# Personal API tokens and the PredictAI agent skill

Status: implemented and tested locally. **Not deployed.** Deploying needs the owner's approval because it applies a database migration to production (Turso).

## Why

PredictAI authenticated people only with a browser session cookie, a CSRF token and an Origin check. An AI agent running a script cannot use that. The Agensi skill (`predictai/`, published separately) needs a credential that is:
- scoped to analytics, ML and report work only
- easy to revoke and limited in time
- never usable for deletion, account changes or administration

## What changed in the app

| Area | Change |
|---|---|
| Database | New migration `backend/app/db/migrations/0017_api_tokens.sql`: table `api_tokens` storing only the SHA-256 hash, a 10-character display prefix, expiry, last use and revocation. Additive; no existing table changes. |
| Identity | New `modules/identity/api_tokens.py` covering create (needs the account password), list, revoke, authenticate, and the **route allow-list**. New `api_token_revocation.py`. |
| Automatic revocation | Tokens are revoked on password change, password reset, owner recovery and account block. Account deletion deletes them. |
| Principal | `Principal.api_token_id` (None for sessions). |
| Request auth (`api/deps.py`) | `Authorization: Bearer pai_…` is accepted **only** on allow-listed routes. Elsewhere it gets 403 `TOKEN_NOT_ALLOWED`. Bearer requests skip CSRF/Origin, since a bearer header cannot be sent cross-site by a browser. Cookie requests are unchanged. The maintenance guard applies to both. |
| API | `GET/POST /api/v1/me/api-tokens` and `DELETE /api/v1/me/api-tokens/{id}`, browser session only. `/me` returns `via_api_token`, and `csrf_token` is null for token callers. |
| Rate limits | `api_token_create:user` 20/hour. `api_token_auth:ip`: 30 failed token logins per 10 min. |
| Frontend | Account page → **API tokens** section: create with name, expiry 30/90/180 days and password; the token is shown once with Copy; list; Revoke. |
| Bug fix | `modules/reports/service.py`: reports built from dataset analyses (`source_kind` `dataset`/`combined` with `run_ids`) crashed with a 500 (`sqlite3.Row` has no `.get`). This also affected the browser. One-line fix plus regression tests. |

### Limits

- At most 10 active tokens per user.
- Expiry of 30, 90 or 180 days (default 90).
- Token format `pai_` followed by 48 URL-safe characters.
- `last_used_at` is updated at most once a minute.

### Allowed token routes (all under `/api/v1`, GET/POST only)

- **Account and projects:** `/me`, `/projects`, `/projects/{id}`.
- **Uploads:** `/projects/{id}/uploads`, `/uploads/{id}`, `/uploads/{id}/chunks/{n}`, `/uploads/{id}/complete`.
- **Assets:** `/projects/{id}/assets`, `/assets/{id}` with its extraction, preview and profile.
- **Analytics:** `/assets/{id}/analytics/{schema,run,presets/dashboard,presets/relationships,hypothesis,runs}`, `/analytics/runs/{id}` (and `/save`).
- **Insights:** `/assets/{id}/insights[/generate]`.
- **ML jobs:** `/ml/catalog`, `/assets/{id}/ml/{schema,assess,jobs}`, `/ml/jobs/{id}[/cancel]`.
- **ML models and predictions:** `/ml/models`, `/ml/models/compare`, `/ml/models/{id}` with its `report`, `partial-dependence`, `predict`, `predict-batch`, `what-if` and `predictions`; `/ml/predictions/{id}[/download]`.
- **Reports:** `/reports/templates`, `/projects/{id}/reports`, `/reports/{id}`, `/reports/{id}/exports`, `/report-exports/{id}/download`.

**Never allowed:**
- any DELETE, PATCH or PUT
- `/auth/*`, `/me/*` sub-routes, `/owner/*`
- research, Notebook, AI brain, reviews and conversations

The allow-list lives in `ALLOWED_ROUTES` in `api_tokens.py`. A test asserts that every entry matches a real route and that no unsafe route is matched.

## Tests

- `backend/tests/test_p18_api_tokens.py` (12 tests) covers:
  - creation needs a password; the token is shown once and only its hash is stored
  - bearer works without cookie, CSRF or Origin, and is refused on every non-allow-listed route
  - invalid, revoked and expired tokens are rejected
  - password change, account deletion and block revoke tokens
  - cross-user isolation and the 10-token limit
  - cookie requests still need CSRF
  - allow-list integrity
- `backend/tests/test_p18_dataset_reports.py` (3 tests): dataset and combined reports build and export; another user's run is refused.
- Migration-list assertions were updated in `test_cli.py`, `test_workspace_projects.py`, `test_p9_deletion_upgrade.py`, `test_p12_monitoring.py` and `test_p13_exam_prediction.py` for migration 17.
- Full backend suite: 2,178 passed; the migration assertions above were then fixed and the affected modules re-run (124 passed). One failure predates this work: `test_identity_api.py::test_login_sets_a_hardened_cookie…` expects `SameSite=Lax`, but commit 1caa75e changed the cookie to `SameSite=None`. It fails on an unmodified checkout too.
- `frontend/tests/apitokens.test.js` covers the form logic.
- The skill's own tests and an end-to-end run against a local server are recorded in the skill package's README.

## Deploying (needs approval)

1. Merge the changes. The migration runs on start-up (or run `python -m app.cli migrate`). On Turso, take a backup or point-in-time marker first (see `docs/phase11/BACKUP_RESTORE.md`).
2. No new environment variables are needed.
3. Check that the production CORS and proxy setup forwards the `Authorization` header to the API. Render does by default.
4. After deploy: sign in, create a token in Account → API tokens, then run `PREDICTAI_BASE_URL=<api url> PREDICTAI_API_TOKEN=<token> python3 predictai/scripts/predictai.py check`.

**Rollback:** revert the code. The `api_tokens` table can stay, since it is unused by older code. To disable tokens immediately without a deploy, run `UPDATE api_tokens SET revoked_at = <now>, revoked_reason = 'disabled'`.
