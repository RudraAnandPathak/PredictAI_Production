# Phase 10 — API reference
Base: `/api/v1/research-intelligence`. All routes require sign-in; mutating routes need the CSRF header. Responses use the standard `{data, meta}` / `{error}` envelope. Every object is scoped to its owner; another user's id returns 404.

| Method & path | Purpose |
|---|---|
| GET `/limits` | Your limits and today's usage |
| GET/POST `/workspaces` | List / create |
| GET/PATCH/DELETE `/workspaces/{id}` | Overview / rename, archive / delete with cascade |
| GET/PUT `/workspaces/{id}/settings` | AI data policy (`local_only` \| `external_allowed`, the latter needs `confirm:true`, audited) |
| GET `/workspaces/{id}/sources` | List sources |
| POST `/workspaces/{id}/sources/upload?filename=` | Raw bytes body (octet-stream) |
| POST `/workspaces/{id}/sources/reference` | `{reference}`: URL, DOI or arXiv id |
| GET/PATCH/DELETE `/workspaces/{id}/sources/{sid}` | Detail / `included`,`title` / delete |
| POST `…/sources/{sid}/reindex` | Read again |
| GET `…/sources/{sid}/chunks?offset=` | Passages |
| GET `/workspaces/{id}/passages/{chunk_id}` | Passage + neighbours |
| GET `/workspaces/{id}/jobs`; POST `/jobs/{jid}/cancel`, `/jobs/{jid}/retry` | Job control |
| POST `/workspaces/{id}/search` | `{query, mode: hybrid\|keyword\|semantic, source_ids?, k?}` |
| GET/POST `/workspaces/{id}/conversations`; GET/PATCH/DELETE `…/{cid}`; PUT `…/{cid}/scope` | Conversations |
| POST `/workspaces/{id}/ask` | `{question, conversation_id?, source_ids?}` → `{conversation, message{body, citations[], answer{mode, provider, model, external, removed_unsupported, retrieval}}}` |
| GET/POST `/workspaces/{id}/notes`; PATCH/DELETE `…/{nid}` | Notes |
| GET/POST `/workspaces/{id}/findings`; DELETE `…/{fid}` | Saved evidence |
| GET `/owner/usage` | Owner only: aggregate counts, never content |
| GET `/discovery/providers` | Each provider: usable?, key configured? (never the key), pacing, what is missing and how to fix it |
| POST `/discovery/search` | `{query?, title?, author?, doi?, year_from?, year_to?, field?, type?, open_access?, sort?, providers?, workspace_id?}` → `{search_id, query, providers[{provider,status,count,message,fix}], notes[], items[], total, page…}`. Duplicates across providers are merged; results are ranked by a transparent heuristic |
| GET `/discovery/searches/{id}?page&page_size&sort&open_access&year_from&year_to&author&type&field&provider` | Page / filter / sort a stored search without asking the providers again |
| GET/DELETE `/discovery/searches`; DELETE `/discovery/searches/{id}` | History (trimmed to `PREDICTAI_RI_DISCOVERY_HISTORY_MAX`) |
| POST `/discovery/library` `{search_id, rid}` | Save a result to your library (the server copies its own stored record; the browser never sends metadata) |
| GET `/discovery/library`; GET/DELETE `/discovery/library/{id}` | Library; delete keeps sources already imported |
| POST `/discovery/compare` | `{ids[2-6]}` (library ids of saved papers) → side-by-side titles, bibliographic data, distinguishing and shared words. Titles/abstracts/metadata only |
| POST `/workspaces/{id}/discovery/import` | `{library_id, full_text?}` → a source (origin `discovery`). Full text only when the provider reported a lawful open-access PDF **and** you asked; otherwise abstract only. Duplicates (DOI/arXiv id/URL) → 409 |
| POST `/workspaces/{id}/analyses` | `{title?, question?, source_ids?, use_model?}` → **202** `{id, status:"queued", job_id…}`. Compares ≥2 ready sources in the background |
| GET `/workspaces/{id}/analyses` | List (no results) |
| GET `/workspaces/{id}/analyses/{aid}` | Progress; when `status:"succeeded"`, `result{sources, findings{common, contradictions, variations}, methods, gaps, evidence, timeline, synthesis, generated, limitations}` |
| POST `/workspaces/{id}/analyses/{aid}/cancel`; DELETE `…/{aid}` | Cancel queued/running; delete a finished one (409 while active) |

`GET /limits` additionally reports `analyses_per_day`, `analyses_today`, `analysis_max_sources`.

Error codes of note: `URL_PRIVATE_ADDRESS`, `URL_TOO_MANY_REDIRECTS`, `URL_TOO_LARGE`, `URL_TYPE_NOT_ALLOWED`, `URL_HTTP_ERROR`, `URL_FETCH_FAILED`, `URL_TIMEOUT`, `EXTRACT_TIMEOUT`, `EXTRACT_RESOURCE_LIMIT`, `EXTRACT_FAILED`, quota/limit denials (HTTP 429/403 with a plain message).

Discovery statuses per provider: `ok`, `skipped` (not enabled / no key where one is required), `needs_key`, `key_rejected`, `rate_limited` (our polite pacing or the provider's 429; includes `retry_after`), `unavailable`, `unreachable`, `bad_answer`. A provider failing never fails the search; the other providers' results are returned and the reason is shown.
Analysis error codes: `ANALYSES_RUNNING` (409, 3 active per workspace), `DAILY_LIMIT_REACHED` (429), `ANALYSIS_ACTIVE`/`ANALYSIS_FINISHED` (409), `TOO_FEW_SOURCES` (job failure when sources stopped being ready), validation errors (422) for fewer than two sources, unknown/foreign source ids, over-long question.

## Knowledge Studio (R7) and reports (R8) — added in completion round 2
| Method & path | Purpose |
|---|---|
| GET `/home` | Research Home: recent workspaces, activity, sources, findings, reports |
| GET `/workspaces/{id}/activity` | Recent analyses, reports and studio outputs (revisit sessions) |
| PATCH `/workspaces/{id}/findings/{fid}` | `{comment?, kind?}` (`finding`\|`highlight`\|`evidence`) |
| GET `/workspaces/{id}/studio/evidence?source_id&kind&q` | Evidence explorer: findings with connection counts, per-source tallies |
| GET/POST `/workspaces/{id}/studio/links`; DELETE `…/links/{lid}` | Connect two findings: `{from_id, to_id, relation: supports\|contradicts\|extends\|related, note?}` |
| GET/POST `/workspaces/{id}/studio/groups`; PATCH `…/{gid}`; PUT `…/{gid}/items` `{finding_ids}`; DELETE | Finding groups (deleting a group keeps its findings) |
| GET/POST `/workspaces/{id}/studio/outlines`; GET/PATCH/DELETE `…/{oid}` | Outlines: `{title, items:[{heading, notes?, finding_ids?, children?}]}`, at most 3 levels and 200 entries |
| POST `/workspaces/{id}/studio/generate` | `{kind: research_questions\|hypotheses\|literature_review, analysis_id, source_ids?}` → **201**, saved. Rule-based, each item carries its quoted basis, labelled "not validated" |
| GET `/workspaces/{id}/studio/generated`; GET/DELETE `…/{gid}` | Saved studio outputs |
| GET `/report-types` | The 7 report types and their sections |
| GET `/reports` | All your reports (newest first) |
| GET/POST `/workspaces/{id}/reports` | List / create `{analysis_id, report_type, title?, source_ids?}` (only from a **succeeded** analysis) |
| GET/PATCH/DELETE `/workspaces/{id}/reports/{rid}` | Read / edit `{title?, sections?}` / delete |
| POST `…/reports/{rid}/regenerate` | `{source_ids?, keep_author_text?: true}` — rebuild generated blocks; author text kept |
| GET `…/reports/{rid}/versions`; POST `…/versions/{vid}/restore` | History (20 most recent) and restore (the current version is snapshotted first) |
| GET `…/reports/{rid}/export?format=pdf\|docx\|pptx\|md` | File download (`Content-Disposition: attachment`, `nosniff`, `no-store`) |

Report model: `{id, report_type, title, version, edited, notice, sources[{label, source_id, text, doi, access}], sections[{id, heading, kind, blocks[]}]}`. A block is `text`, `bullets`, `quote` or `table` with `origin` = `source` (exact quote) \| `analysis` (computed) \| `author` (written or edited by the person), `cite` (labels that must belong to the report's sources). **Quote and table blocks are immutable** (removable only); editing text turns the block into `author` with `edited_from`.

Report error codes of note: `ANALYSIS_GONE` (409, regenerate after a source it used was deleted), `ANALYSIS_NOT_READY`, `DAILY_LIMIT_REACHED`/429 from `ri_report:user` (120/h) and `ri_export:user` (60/h).

Route count: **85** under `/api/v1/research-intelligence` (36 core + 12 discovery + 5 analysis + 32 studio/reports/home/activity/finding patch).
