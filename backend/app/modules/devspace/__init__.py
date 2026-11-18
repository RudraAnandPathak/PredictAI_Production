"""Development Workspace (Phase 9). Special/Owner only; every method re-checks the permission on the current request.
Layout on disk (under settings.dev_root, default <storage_dir>/dev): <project_id>/tree (working tree), <project_id>/objects (content-addressed versions and artifacts),
<project_id>/envs (virtual environments, one directory per environment version), <project_id>/jobs (per-job scratch, removed when the job ends)."""
