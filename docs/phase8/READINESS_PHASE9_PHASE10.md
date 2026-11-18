# Phase 8: readiness for Phase 9 and Phase 10

## Phase 9: Owner-Only Admin Dashboard: **Ready, with named gaps**
**Already in place (reuse, do not rebuild):** owner role and permissions with step-up (re-entering the password); user search, block/unblock, entitlement grant/revoke and code management (Phase 2); the audit log with filters (Phase 2); Phase 7 owner overview, monitoring, maintenance mode, quota policy and owner event history; per-account usage summary; metadata-only owner views that cannot read user content; a first-owner bootstrap that needs no manual database work; research limits that the owner can change.

**Gaps for Phase 9 to close:**
1. Counts of research workspaces/sources in the owner overview (not added in Phase 8 to keep this phase to what was authorised).
2. A one-off orphan sweep for Phase 7 data left by deletions made before the Phase 8 fix (rows with a deleted project, objects under `registry/` and `reports/` with no row).
3. Audit-log retention and export policy.
4. Owner-managed safeguard ceilings (today they are server settings).
5. Real host metrics are intentionally unavailable (no agent is installed); decide whether a dashboard should ever show them.
6. Owner-visible access to a person's content must stay absent unless an explicit, audited, time-limited support-access design is approved. Phase 8 did not build one.

## Phase 10: Research Intelligence: **Foundations ready; engine not started**
**Ready:** account-owned workspaces (several per person, unique names, archive/restore/delete); source table covering PDF, DOCX, web article, academic paper, CSV and XLSX; conversations and messages storing questions, responses and `source_refs`; notes; per-role limits that the owner can change; ownership enforced in the service, by 404 isolation and by database triggers; deletion and account-deletion cleanup of every child row; deletion of a file removes sources that point at it; audit events and rate limiting; a navigation entry.

**Decisions Phase 10 must make (not made here on purpose):**
1. **Where source files live.** `rw_sources.ref_asset_id` points to an existing project asset, so files reuse Phase 3 upload, parsing, page counting and quotas. If Phase 10 wants uploads that belong to no project, it needs a new storage prefix and the deletion hooks extended to match.
2. **Web article and academic paper ingestion** can reuse Phase 6 web access, Wikipedia and arXiv providers and their opt-in/consent rules; none of that is wired to the new tables.
3. **Retrieval.** Phase 6 has a document index (`doc_indexes`) and evidence bundles; decide whether workspace retrieval extends them or has its own index. Either way, a new index table must be added to `phase7_purge`.
4. **The `source_refs` locator format** (page, section, row range) and how citations render. The column validates JSON only.
5. **Whether a workspace can be shared** (Phase 7 sharing covers other resource types, not this one).
6. **AI provider usage and cost per role**; Phase 6 provider settings and `ai_usage` exist; per-workspace budgets do not.
7. **Source content size and storage limits** beyond the count limit.
8. **Citations must never invent a source.** The foundation can store a reference only to a source row that exists in the same workspace and owner (enforced for notes by trigger; messages store JSON, so Phase 10 must validate it in the service).

**Risks to carry forward:** a model answer without a stored `source_refs` entry should be treated as unsupported; chunk text copied into new tables must be covered by deletion; conversation logs can contain sensitive content and need the same isolation tests as documents.
