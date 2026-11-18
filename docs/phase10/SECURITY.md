# Phase 10 — Security model

| Threat | Control | Verified by |
|---|---|---|
| SSRF via URL/DOI import | Scheme allow-list, DNS resolved once and pinned, private/loopback/link-local/metadata ranges blocked, every redirect re-validated (max 4), size/time/type caps | `test_p10_security` (injected fetcher/resolver) |
| Malicious uploads | Magic-byte/extension agreement, zip-bomb ratio and entry limits, macro and path-traversal rejection, size cap while reading | `test_p10_security` |
| Malware | ClamAV when installed (`auto`), refuse when missing (`required`), or off; status stored per source | mocked scanner only |
| Parser exploits / DoS | Extraction in a subprocess with RLIMIT_AS/CPU/FSIZE/NOFILE, scrubbed env, private temp dir, wall-clock timeout | `test_p10_ingestion` |
| Prompt injection in documents | Pattern flagging per passage; passages are always quoted as data via `neutralise`/`SYSTEM_RULES`; flagged counts shown to the user; the model's output is verified sentence by sentence | `test_p10_security`, `test_p10_assistant` |
| Fabricated citations | Quotes are exact substrings of stored chunks; unsupported sentences removed; no evidence → no model call | `test_p10_assistant` |
| Data leaving the machine | Per-workspace `ai_data_policy`, default `local_only`; external needs explicit confirmation, is audited, and is shown on each answer | `test_p10_assistant`, `test_p10_api` |
| Cross-user access | Owner checks in every query and DB triggers tying sources/chunks to owner+workspace | `test_p10_api` |
| Abuse | Per-user rate limits (`ri_*` policies) and daily quotas; owner view is counts only | `test_p10_api` |
| Secrets | Qdrant and provider keys only from env at call time; never stored or logged | review + tests above |
| Deletion | Cascade purge of rows, FTS, files, vectors, library links, searches, cached facts and analyses that quote a deleted source | `test_p10_upgrade`, `test_p10_ingestion`, `test_p10_reasoning` |
| Provider keys (OpenAlex, Semantic Scholar) | Read from the environment at call time; sent only to that provider; never stored, logged, or returned (the providers endpoint reports only "configured: yes/no") | `test_p10_discovery_api` (key string absent from every response) |
| Discovery metadata forgery | Save/import take only `search_id + rid` or `library_id`; the server uses its own stored record | `test_p10_discovery` |
| Importing from discovery (SSRF) | Open-access URL from the provider passes the same URL guard as any URL import; a rejected or failing download falls back to the abstract | `test_p10_discovery` |
| Private documents sent out during analysis | Analyses are deterministic by default. A model narrative is opt-in per analysis, passes `Consent(external=…)` from the workspace policy (default `local_only`), is verified sentence by sentence, and external use is audited | `test_p10_reasoning` |
| Cross-user analysis access | Owner + workspace checks on every analysis query; a foreign source id is rejected at creation | `test_p10_reasoning`, `test_p10_reasoning_api` |
| Analysis abuse | 20 starts/hour/user, `PREDICTAI_RI_ANALYSES_PER_DAY_*` daily quota, 3 active per workspace, source cap | `test_p10_reasoning` |

## Studio, reports and exports (round 2)
- Every report, studio and export route is owner-scoped; another user's id returns 404, never 403. Mutations need the CSRF header; exports are `GET` and read-only.
- Reports contain only what the person's own analysis produced. Quotes are exact substrings of stored passages, citation labels must belong to the report, and the server rejects edits to quote/table blocks, so a client cannot forge evidence.
- Deleting a source or workspace purges (or redacts) its quotes from reports, version history, studio outputs, links and groups; tested.
- Export files are generated in memory, served with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`; file names are sanitised. Exports are rate-limited (60/h).
- Nothing in a report is sent to an external provider. The only optional model call remains the R5 narrative, governed by the workspace AI policy and verified against the quotes.
- The frontend never receives provider keys: Settings shows only whether a key is configured. Report text is rendered as React text, never as HTML.

## Limits (stated plainly)
- Subprocess isolation is **resource limiting, not a security sandbox**. Run the worker as an unprivileged user (ideally in a container) when handling untrusted files.
- Prompt-injection detection is pattern-based and can be evaded; the structural defences (quoting as data, verification, local-only default) are the real controls.
- Malware scanning is only as good as the installed ClamAV signatures; without it, sources are labelled "not scanned".
- Discovery results are third-party metadata; they can be wrong or incomplete. Open-access detection (Crossref licence + PDF link, OpenAlex/Semantic Scholar OA flags) is a best effort; the person is told which access level a source ended up with.
- Analysis grouping is lexical. A contradiction flag is a prompt to read two quotes, not a finding of fact.
- Security assessment status: reviewed by tests with fakes; **not** penetration-tested; real HTTPS pinning, real ClamAV and live providers are UNVERIFIED here.
- Report "not peer reviewed" wording is a fixed notice, not a check; the tool cannot judge a source's quality. PDF export substitutes "?" for characters outside its bundled font.
