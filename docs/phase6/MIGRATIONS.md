# Phase 6 migration

One additive migration: `backend/app/db/migrations/0005_research.sql`. Migrations 0001–0004 are untouched (their checksums are asserted by a test).

| Table | Holds |
|---|---|
| `research_settings` | the person's and each project's consent (web, search sites, news, outside AI, freshness, cache days) |
| `research_sessions`, `research_jobs` | questions, plans, runs, progress; one jobs table for research, news reading and influence analysis |
| `research_sources`, `evidence_bundles`, `research_notes` | sources with indicators, sealed result bundles, the person's notes and labels |
| `web_cache` | saved copies of web pages (per person and project, expiring, size-capped) |
| `doc_indexes` | derived, rebuildable search indexes for documents |
| `news_sources`, `news_articles`, `news_events` | followed feeds, articles (headline and the feed's summary only), event groups |
| `influence_analyses` | association analyses with their replayable inputs |
| `ai_providers`, `ai_usage` | provider configuration (key *names* only) and a content-free usage ledger |

Tables point at users and projects with foreign keys (enforced: a row pointing nowhere is refused); removal of rows and files is done by the existing governance code, which Phase 6 extended, and is tested. Vocabularies (status, level, kind) are `CHECK` constraints. `ai_providers` starts with one built-in row so the app works with no model.

## Verified
- A Phase 5 database (migrations 1–4, with a user, project and dataset in it) upgrades to 5: every existing row, table, index definition and checksum is unchanged; only the 14 research tables are added, empty (except the built-in provider); `PRAGMA foreign_key_check` and `integrity_check` are clean; running again applies nothing. Test: `test_workspace_projects.py` (`test_phase_five_database_upgrades_to_phase_six_keeping_every_row_and_adding_only_the_research_tables`).
- Deleting a dataset, project or account removes the research rows and files that depend on it (`test_phase6_integration.py`, `test_research_service.py::Deletion`, `test_world_service.py::Isolation`).
- Rollback: the migration only adds tables. To go back to Phase 5, restore the database copy taken before `migrate` (always take one); dropping the 14 tables by hand also works because nothing in 0001–0004 refers to them.
- Not verified: D1 itself. The SQL uses only constructs the earlier migrations use, but it has not been run on Cloudflare D1.

## Corrective release
The corrective release (research engine, web search, datasets, Wikipedia and arXiv) adds **no migration**: no table, column or index changed. The new fields (sections read, content scope, calculation records, the report) live inside the stored JSON of existing columns, and a bundle made before the release still opens (the screens fall back to its conclusions when it has no report).
