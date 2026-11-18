# Phase 7: migration notes

- One new migration: `0006_phase7.sql`. It only **creates** tables (prefixes `reg_`, `exam_`, `adv_`, `rep_`, `ws_`, `sch_`, `own_`) and seeds one `own_maintenance` row (off). No Phase 1–6 table is altered, dropped or rewritten, so existing users, projects, files, models and research are untouched.
- Back up `data/` before the first start as with any upgrade. There is no automated down-migration; to roll back, restore the backup and run the previous release.
- API: all Phase 1–6 routes and response shapes are unchanged. New routes were added under `/api/v1` (registry, reports, exam, intelligence, workspaces, schedules, dashboard, owner overview/monitoring/maintenance/quota/events).
- Behaviour that is new for existing accounts: (1) while the owner has maintenance mode on, authenticated write requests from non-owners return `503 MAINTENANCE` (reads and sign-out still work); (2) standard-account quotas can be overridden by the owner (`own_quota_policy`); with no override the configured defaults apply exactly as before.
- Navigation: the `exam` and `reports` placeholders became real pages; `models` moved from the Special-access group into Workspace and now opens the Phase 7 registry. Your trained models are still managed under Predictions; the registry adds versions and lifecycle on top.
- Rate-limit policies added: `p7_*` in `identity/ratelimit.py`.
