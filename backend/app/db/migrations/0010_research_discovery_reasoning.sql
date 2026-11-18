-- Phase 10 completion: R2 discovery and R5 reasoning. Additive: new ri_* tables only, plus two CHECK widenings done the only way SQLite allows (copy, drop, rename):
-- ri_usage gains the kinds 'discovery','reasoning'; ri_jobs gains the kind 'analysis'. Existing rows are copied unchanged.

-- ---------------------------------------------------------------- widen ri_usage
CREATE TABLE ri_usage_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('ai_request','import','embed_units','retrieval','upload_bytes','discovery','reasoning')),
  units INTEGER NOT NULL DEFAULT 1,
  detail TEXT,
  created_at TEXT NOT NULL
);
INSERT INTO ri_usage_new (id, owner_user_id, workspace_id, kind, units, detail, created_at) SELECT id, owner_user_id, workspace_id, kind, units, detail, created_at FROM ri_usage;
DROP TABLE ri_usage;
ALTER TABLE ri_usage_new RENAME TO ri_usage;
CREATE INDEX ri_usage_owner ON ri_usage (owner_user_id, kind, created_at);

-- ---------------------------------------------------------------- widen ri_jobs
CREATE TABLE ri_jobs_new (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT REFERENCES ri_sources(id),
  kind TEXT NOT NULL CHECK (kind IN ('ingest','embed','reindex','fetch_url','resolve_identifier','analysis')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelling','cancelled')),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  stage TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  payload TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
  error_code TEXT,
  error_message TEXT,
  run_after TEXT NOT NULL,
  locked_by TEXT,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
INSERT INTO ri_jobs_new SELECT id, workspace_id, owner_user_id, source_id, kind, status, progress, stage, attempts, max_attempts, payload, error_code, error_message, run_after, locked_by, locked_until, created_at, started_at, finished_at FROM ri_jobs;
DROP TABLE ri_jobs;
ALTER TABLE ri_jobs_new RENAME TO ri_jobs;
CREATE INDEX ri_jobs_queue ON ri_jobs (status, run_after);
CREATE INDEX ri_jobs_ws ON ri_jobs (workspace_id, owner_user_id, created_at);

-- ---------------------------------------------------------------- R2 discovery
-- A search keeps its merged, ranked results so paging, filtering and "save" never re-query the providers (and never trust metadata sent back by the browser).
CREATE TABLE ri_discovery_searches (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  query TEXT NOT NULL CHECK (json_valid(query)),
  providers TEXT NOT NULL CHECK (json_valid(providers)),
  total INTEGER NOT NULL DEFAULT 0,
  results TEXT NOT NULL CHECK (json_valid(results)),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_discovery_searches_owner ON ri_discovery_searches (owner_user_id, created_at);

-- The person's saved papers (their library). One row per distinct work (duplicate detection key), independent of any workspace.
CREATE TABLE ri_library (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  dedupe_key TEXT NOT NULL,
  title TEXT NOT NULL,
  year INTEGER,
  doi TEXT,
  record TEXT NOT NULL CHECK (json_valid(record)),
  created_at TEXT NOT NULL,
  UNIQUE (owner_user_id, dedupe_key)
);

-- Which workspace sources came from which library entry.
CREATE TABLE ri_library_links (
  source_id TEXT PRIMARY KEY REFERENCES ri_sources(id),
  library_id TEXT NOT NULL REFERENCES ri_library(id),
  workspace_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_library_links_lib ON ri_library_links (library_id);

-- ---------------------------------------------------------------- R5 multi-source reasoning
-- Facts extracted from one source (findings, methods, limitations as exact sentences). Cached; recomputed when the source's version or content changes.
CREATE TABLE ri_source_facts (
  source_id TEXT PRIMARY KEY REFERENCES ri_sources(id),
  workspace_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  cache_key TEXT NOT NULL,
  facts TEXT NOT NULL CHECK (json_valid(facts)),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_source_facts_ws ON ri_source_facts (workspace_id, owner_user_id);

-- One analysis of several sources of a workspace. `result` is written once, when the job succeeds.
CREATE TABLE ri_analyses (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  question TEXT,
  source_ids TEXT NOT NULL CHECK (json_valid(source_ids)),
  use_model INTEGER NOT NULL DEFAULT 0 CHECK (use_model IN (0,1)),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  stage TEXT,
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  job_id TEXT,
  result TEXT CHECK (result IS NULL OR json_valid(result)),
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX ri_analyses_ws ON ri_analyses (workspace_id, owner_user_id, created_at);
