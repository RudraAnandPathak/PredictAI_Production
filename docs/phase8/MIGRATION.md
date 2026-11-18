# Phase 8: migration
`backend/app/db/migrations/0007_phase8_research_foundation.sql`. Create-only: no existing table, index, trigger or row is altered or removed (proved by `test_phase_seven_database_upgrades_to_phase_eight_adding_only_empty_rw_tables`).

Upgrade: `cd backend && python -m app.cli migrate` (also automatic on start-up when `PREDICTAI_AUTO_MIGRATE` is on). Back up `data/predictai.db` first. It is applied once, in a transaction, with a checksum.

| Table | Purpose |
|---|---|
| `rw_workspaces` | Research workspaces: owner, unique name per owner (case-insensitive), status active/archived |
| `rw_sources` | Sources: kind ∈ pdf, docx, web_article, academic_paper, csv, xlsx; optional `ref_asset_id` (an existing uploaded file; no foreign key so deleting the file is never blocked), `url`, `content_hash` (unique per workspace), page/byte counts, status |
| `rw_conversations`, `rw_messages` | Questions and responses; `source_refs` is validated JSON; messages are append-only by sequence |
| `rw_notes` | Notes, optionally tied to one source of the same workspace |

Triggers refuse any child row whose owner (or workspace) differs from its parent's, so an application bug cannot attach one person's data to another's workspace.

Rollback is manual and destroys Phase 8 data: drop `rw_notes`, `rw_messages`, `rw_conversations`, `rw_sources`, `rw_workspaces` (their triggers go with them), delete the `schema_migrations` row for version 7. No automatic down-migration exists on purpose.
