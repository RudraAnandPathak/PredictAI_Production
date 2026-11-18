# Phase 8: API reference (new routes)
Base path `/api/v1`. Every route needs a session cookie; changes also need the CSRF token (`X-CSRF-Token`) and a trusted `Origin`. Responses use the standard envelope. Another person's workspace answers `404 NOT_FOUND`, identical to a missing one.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/research-workspaces/limits` | Your tier, `max_workspaces`, `max_sources_per_workspace`, `workspaces_used`, `supported_source_kinds` | `tier` is standard / special / owner; `safeguard_applies` is true for special and owner |
| GET | `/research-workspaces?status=active\|archived` | Your workspaces with counts (sources, conversations, notes) and your limits | |
| POST | `/research-workspaces` | Create. Body `{name (1–120), description? (≤2000)}` | 201; 409 `WORKSPACE_NAME_TAKEN` (unique per person, ignoring case); 403 `WORKSPACE_LIMIT_REACHED`; 422 validation |
| GET | `/research-workspaces/{id}` | One workspace you own | |
| PATCH | `/research-workspaces/{id}` | `{name?, description?, status? "active"\|"archived"}` | |
| DELETE | `/research-workspaces/{id}` | Delete the workspace and everything in it | Irreversible |

Owner limits (existing Phase 7 routes, two new keys): `GET /owner/quota-policy`, `PUT /owner/quota-policy/{key}` with `{value}`, `DELETE /owner/quota-policy/{key}`. New keys: `standard_max_research_workspaces` (1–100), `standard_max_research_sources` (1–500). Password-confirmed; lowering a limit never deletes existing items.

Not routes: owner bootstrap has **no HTTP route** (`python -m app.cli bootstrap-owner` and start-up only).

Audit events: `rw.workspace.created`, `rw.workspace.changed`, `rw.workspace.deleted`, `rw.quota.denied`. Metadata holds identifiers, status and limits only, never names or descriptions.

Rate limit: `rw_write:user`, 300 changes per hour per account.
