# Phase 8: owner setup and special-access activation

## 1. Creating the first owner (server operator only)
There is no owner registration in the website or API. The owner is created on the server, in one of three ways.

**A. From environment variables (new in Phase 8).** In your private `.env` at the repository root (never commit it, never put it in `config/env.example`):
```
PREDICTAI_OWNER_NAME=Your Name
PREDICTAI_OWNER_EMAIL=you@example.com
PREDICTAI_OWNER_PASSWORD=<a long passphrase>
PREDICTAI_OWNER_ACTIVATION_CODE=PAI-XXXXX-XXXXX-XXXXX-XXXXX     # optional, see below
```
The plain names `OWNER_NAME`, `OWNER_EMAIL`, `OWNER_PASSWORD`, `OWNER_ACTIVATION_CODE` also work; the `PREDICTAI_` names win if both are set. Then either start the API (it bootstraps once at start-up, after migrations) or run `cd backend && python -m app.cli bootstrap-owner`.

Behaviour you can rely on:
- Does nothing unless both email and password are set.
- Creates the owner only when **no active owner exists**. If one exists, the values are ignored: a changed `OWNER_PASSWORD` does **not** reset the password and a different `OWNER_EMAIL` does **not** add a second owner.
- If the email already belongs to a normal account, that account is promoted and its password is left unchanged.
- The password is hashed like every other password and must pass the normal strength rules. It is never logged, printed or returned.
- Once the owner exists, **remove the owner password from `.env`**.

**B. Interactive CLI (Phase 2):** `python -m app.cli provision-owner --email you@example.com` (hidden password prompt).

**C. Break-glass reset:** `python -m app.cli recover-owner --email you@example.com`.

## 2. The activation code seed
`PREDICTAI_OWNER_ACTIVATION_CODE` must look like `PAI-XXXXX-XXXXX-XXXXX-XXXXX`, with the 32 characters `0-9 A-Z` minus I, L, O, U (100 bits). Anything weaker, such as `special123`, is refused and nothing is created. It is stored only as a hash, labelled "owner bootstrap", can be used by **one** account once, and expires after 30 days. Prefer generating individual codes in Owner controls (below): each is random, single use, can be bound to one person and revoked.

## 3. Giving someone Special access
**With a code:** the person signs in, opens *Account → Special access*, enters the code. Special access is permanent for that account until an owner revokes it; revoking takes effect on the very next request. Wrong codes are rate limited per person and per IP, and every attempt is audited. All failures look identical, so codes cannot be probed.

**Owner side (Owner controls, password-confirmed):** create an individual code (shown once), optionally bound to one email/user and given an expiry; or grant Special access directly; or revoke an unused code or an active entitlement.

## 4. What each role gets
| | Normal | Special | Owner |
|---|---|---|---|
| Documents / pages per document | 3 / 100 (owner-adjustable) | unlimited | unlimited |
| Research workspaces / sources per workspace | 5 / 20 (owner-adjustable) | up to safeguard ceiling 200 / 500 | up to safeguard ceiling |
| Other people's data | no | no | **no content access**; counts and account status only, audited |
| Manage accounts, codes, limits, maintenance | no | no | yes |

Roles can only be changed by an owner or by redeeming a code. No profile, registration or API field changes a role (tested over HTTP).
