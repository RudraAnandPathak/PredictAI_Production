# Google / GitHub: registration, sign-in and linking

One service (`backend/app/modules/identity/oauth.py`, `OAuthService`) and one flow serve every use. Nothing parallel was added.
Authorization-code flow with PKCE (S256) and a single-use, 10-minute `state`; identity is read from the provider's userinfo endpoint over TLS (no id_token is consumed).

| Intent | Who starts it | `state` is bound to | Result |
|---|---|---|---|
| `link` (Phase 2, unchanged) | signed-in user | that user | adds an identity to the account |
| `reauth` (new) | signed-in user | that user | proves control of a connected provider; rotates the session and refreshes the step-up window |
| `signin` (new) | anonymous visitor on **Login** | the browser (nonce cookie) | signs in if the provider account is already connected; otherwise explains and creates nothing |
| `register` (new) | anonymous visitor on **Register** | the browser (nonce cookie) | creates an account from a provider-verified email, or signs in if already connected |

## Endpoints (all under `/api/v1/auth/oauth`)
`GET /available` (public: id, label, configured) · `POST /{provider}/signin {intent: login|register}` · `POST /{provider}/reauth` · `GET /{provider}/callback` · plus the Phase 2 `GET ""`, `POST /{provider}/link`, `POST /{provider}/unlink`.
Account additions: `/me` now reports `has_password` and `connected_providers`; `POST /me/delete` and `/unlink` accept no password for password-less accounts.

## Security rules (each has a test)
* **State**: random, hashed at rest, single use, 10-minute expiry, bound to provider and intent. A link state cannot sign anyone in and a sign-in state cannot link.
* **Login-CSRF**: anonymous states carry a hash of a random nonce; the nonce lives in an `HttpOnly`, `SameSite=Lax` cookie (`__Host-` prefix when hosted) set by the start request and deleted on the callback. A callback arriving in a different browser is refused.
* **Account creation** only from an email the provider reports as verified (Google `email_verified`; GitHub primary email `verified`). Otherwise `OAUTH_EMAIL_UNVERIFIED` and nothing is stored.
* **No merging, ever, by email.** If a PredictAI account with that email exists, whatever its verification state, the attempt is refused (`OAUTH_EMAIL_EXISTS`), nothing is attached, no session is issued, and the existing owner gets a security email. This is stricter than the minimum in the specification (which forbids merging on an *unverified* match) because merging on a verified match is still how provider-side email recycling takes accounts over. The owner connects a provider deliberately from Account → Connected accounts.
* **Sessions** are issued by the existing `AuthService.issue_session`; roles, tiers and entitlements are untouched (a new account is `user`/`standard`).
* **Secrets**: client secrets are read by the backend only. They never appear in responses, the audit log, redirects or the frontend bundle. Provider tokens are used once and never stored.
* **Redirects** carry only fixed error codes (`?oauth_error=oauth_email_exists`), never provider text; the UI maps codes to fixed sentences.
* **Rate limits**: `oauth_login:ip` (30 per 15 minutes) on start and callback; `oauth:user` on link/reauth.
* Suspended or deleted accounts cannot sign in this way (`ACCOUNT_NOT_ACTIVE`).

## Phase 2 defect F1 (found in Stage 1, fixed here)
Accounts created through a provider have no password, but `delete_account`, `unlink`, re-authentication and recovery all required one.
* **Step-up without a password**: `confirm_password` falls back to the existing step-up window (`PREDICTAI_STEP_UP_MINUTES`) — a provider sign-in just happened, so deleting the account works immediately; later, `REAUTH_REQUIRED` is returned and the UI offers "Confirm with Google/GitHub" (`/reauth`).
* **Set a password**: Account → Password shows "Email me a link to set a password". It reuses the single-use emailed recovery token; `request_recovery` is now also available to accounts with a verified email but no password, and `complete_recovery` creates the password identity. The response stays uniform (no enumeration).
* **Unlink** is allowed whenever another sign-in method remains (a password or another provider); otherwise `LAST_SIGN_IN_METHOD`.
* `change_password` on a password-less account returns `NO_PASSWORD` with instructions.
* After a password reset the SPA now drops its stale session immediately (the server ended every session).

## Setting up the providers (free; no billing)
1. Google: Cloud Console → APIs & Services → Credentials → OAuth client ID → *Web application*. GitHub: Settings → Developer settings → OAuth Apps.
2. Authorized redirect / callback URL: `<PREDICTAI_OAUTH_REDIRECT_BASE_URL>/api/v1/auth/oauth/<google|github>/callback` (local default `http://localhost:5173/api/v1/auth/oauth/google/callback`).
3. Put the client id and secret in `.env` (`PREDICTAI_OAUTH_GOOGLE_CLIENT_ID/_SECRET`, `PREDICTAI_OAUTH_GITHUB_CLIENT_ID/_SECRET`), restart the API. A provider shows as enabled only when both values are set.
4. Google may require the consent screen to be configured (external, testing mode is fine for development).

## Troubleshooting: "… is not set up on this server yet"
The buttons are enabled only when the API reports the provider as configured, i.e. **both** `PREDICTAI_OAUTH_<GOOGLE|GITHUB>_CLIENT_ID` and `_CLIENT_SECRET` are non-empty in the API process. Run `cd backend && python -m app.cli oauth-status` (also logged at API start-up): it names the missing variables and the usual causes, never values.
1. **Names**: only the `PREDICTAI_OAUTH_…` names are read; `GOOGLE_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_ID` and the like are ignored (the command lists such near-misses).
2. **Location**: only the repository-root `.env` is read (not `backend/.env`, not `frontend/.env`; the Vite frontend never needs these secrets).
3. **Restart**: settings are read once; `uvicorn --reload` does not watch `.env`. Stop and start the API.
4. A real environment variable (even an empty one) overrides `.env`; a half-set pair (id without secret) stops the API with a clear message.
5. The redirect URI registered with the provider must equal `<PREDICTAI_OAUTH_REDIRECT_BASE_URL>/api/v1/auth/oauth/<provider>/callback`, and in development open the app at the **same host** as that URL (`localhost`, not `127.0.0.1`), because the sign-in nonce cookie is host-specific.
Button wording follows the page: "Sign up with Google" on Register, "Sign in with Google" on Login.

## What is and is not verified
Verified with a fake provider HTTP client (service tests, HTTP tests on the FastAPI stand-in, and a real-browser run with the provider redirect intercepted): all rules above. **Not verified against real Google or GitHub** — no credentials or network were available. First run with real credentials should confirm: the redirect URI matches exactly, GitHub's `/user/emails` returns the primary verified address (needs the `user:email` scope, already requested), and Google's `openid email` scopes return `email_verified`.
Apple is intentionally absent (Deferred in the register). Magic links are likewise not part of this phase.
