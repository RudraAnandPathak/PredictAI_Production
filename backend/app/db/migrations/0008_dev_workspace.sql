-- Phase 9: Development Workspace (code files, environments, jobs, sessions, artifacts, secrets, git, pipelines, experiments, quotas, history).
-- Create-only: nothing from Phases 1-8 is altered. Projects, assets, users, the model registry (reg_*) and the audit log are REUSED, not duplicated.
-- Every table keeps project_id and owner_user_id so reads are isolated. File contents are NOT stored here (working tree + content-addressed store on disk); this holds the index.

CREATE TABLE dv_workspaces (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  config TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(config)),
  suspended_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE dv_files (                              -- index of the project working tree: current content hash, version counter, soft delete
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 300),
  kind TEXT NOT NULL CHECK (kind IN ('code', 'notebook', 'config', 'manifest', 'text', 'other')),
  version INTEGER NOT NULL DEFAULT 1,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX dv_files_live_path ON dv_files (project_id, path) WHERE deleted_at IS NULL;
CREATE INDEX dv_files_project ON dv_files (project_id, updated_at);

CREATE TABLE dv_file_versions (                      -- append-only history; content lives in the content-addressed store under the sha256
  file_id TEXT NOT NULL REFERENCES dv_files(id),
  version INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  author_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (file_id, version)
);

CREATE TABLE dv_envs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  python_version TEXT NOT NULL,
  platform TEXT NOT NULL,
  current_version INTEGER NOT NULL DEFAULT 0,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE UNIQUE INDEX dv_envs_name ON dv_envs (project_id, name COLLATE NOCASE) WHERE deleted_at IS NULL;

CREATE TABLE dv_env_versions (                       -- immutable: a change of requirements or a rebuild is a new version
  id TEXT PRIMARY KEY,
  env_id TEXT NOT NULL REFERENCES dv_envs(id),
  version INTEGER NOT NULL,
  requirements TEXT NOT NULL,                        -- the manifest (requirements.txt text)
  lockfile TEXT,                                     -- name==version lines from the built environment
  manifest_sha256 TEXT NOT NULL,
  env_refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(env_refs)),   -- NAMES of secret references the environment may use; never values
  status TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed')),
  error TEXT,
  report TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(report)),        -- validation, conflicts, vulnerability check, comparison
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE (env_id, version)
);

CREATE TABLE dv_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('script', 'notebook', 'command', 'env_build', 'pipeline_stage')),
  status TEXT NOT NULL CHECK (status IN ('queued', 'starting', 'running', 'cancelling', 'succeeded', 'failed', 'timed_out', 'cancelled', 'blocked')),
  priority INTEGER NOT NULL DEFAULT 100,
  entry TEXT,                                        -- project-relative script or notebook path (kind script/notebook)
  argv TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(argv)),
  env_id TEXT REFERENCES dv_envs(id),
  env_version INTEGER,
  limits TEXT NOT NULL CHECK (json_valid(limits)),   -- the limits this job actually ran under
  network TEXT NOT NULL DEFAULT 'none' CHECK (network IN ('none', 'package_index')),
  secret_refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(secret_refs)),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  retry_of TEXT,
  dead_letter INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  exit_code INTEGER,
  error_class TEXT,
  blocked_reason TEXT,
  commit_sha TEXT,
  tree_sha256 TEXT,                                  -- hash of the file set the job ran against (reproducibility)
  pipeline_run_id TEXT,
  experiment_id TEXT,
  isolation TEXT,                                    -- which provider ran it, and with which guarantees
  worker_pid INTEGER,
  wall_ms INTEGER,
  cpu_ms INTEGER,
  peak_rss_bytes INTEGER,
  output_bytes INTEGER,
  heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX dv_jobs_queue ON dv_jobs (status, priority, created_at);
CREATE INDEX dv_jobs_project ON dv_jobs (project_id, created_at);
CREATE INDEX dv_jobs_owner ON dv_jobs (owner_user_id, status);

CREATE TABLE dv_job_output (
  job_id TEXT NOT NULL REFERENCES dv_jobs(id),
  stream TEXT NOT NULL CHECK (stream IN ('stdout', 'stderr')),
  text TEXT NOT NULL,                                -- redacted before it is stored
  truncated INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, stream)
);

CREATE TABLE dv_artifacts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  bucket TEXT NOT NULL CHECK (bucket IN ('outputs', 'derived', 'temporary')),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  version INTEGER NOT NULL DEFAULT 1,
  kind TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  job_id TEXT REFERENCES dv_jobs(id),
  experiment_id TEXT,
  provenance TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(provenance)),
  retention_until TEXT,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (project_id, bucket, name, version)
);
CREATE INDEX dv_artifacts_project ON dv_artifacts (project_id, created_at);

CREATE TABLE dv_sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('notebook', 'ide')),
  status TEXT NOT NULL CHECK (status IN ('starting', 'running', 'stopping', 'stopped', 'failed', 'unavailable')),
  pid INTEGER,
  port INTEGER,
  token_hash TEXT,
  command TEXT,
  reason TEXT,
  idle_timeout_s INTEGER NOT NULL,
  last_activity_at TEXT,
  started_at TEXT NOT NULL,
  stopped_at TEXT
);
CREATE INDEX dv_sessions_owner ON dv_sessions (owner_user_id, status);

CREATE TABLE dv_secrets (                             -- encrypted values (Fernet, key derived from the server secret); never returned by any API
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  purpose TEXT NOT NULL CHECK (purpose IN ('job', 'git', 'webhook')),
  ciphertext TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);
CREATE UNIQUE INDEX dv_secrets_name ON dv_secrets (project_id, name) WHERE revoked_at IS NULL;

CREATE TABLE dv_repos (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL CHECK (provider IN ('github', 'gitlab', 'selfhosted', 'local')),
  remote_url TEXT,
  branch TEXT NOT NULL DEFAULT 'main',
  protected_branches TEXT NOT NULL DEFAULT '["main","master"]' CHECK (json_valid(protected_branches)),
  consent_at TEXT NOT NULL,
  consent_scope TEXT NOT NULL CHECK (consent_scope IN ('read', 'read_write')),
  credential_secret_id TEXT REFERENCES dv_secrets(id),
  webhook_secret_id TEXT REFERENCES dv_secrets(id),
  status TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'revoked')),
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE dv_webhook_deliveries (                  -- replay protection: a delivery id is accepted once
  repo_id TEXT NOT NULL REFERENCES dv_repos(id),
  delivery_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY (repo_id, delivery_id)
);

CREATE TABLE dv_pipelines (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  definition TEXT NOT NULL CHECK (json_valid(definition)),
  triggers TEXT NOT NULL DEFAULT '["manual"]' CHECK (json_valid(triggers)),
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE UNIQUE INDEX dv_pipelines_name ON dv_pipelines (project_id, name COLLATE NOCASE) WHERE deleted_at IS NULL;

CREATE TABLE dv_pipeline_runs (
  id TEXT PRIMARY KEY,
  pipeline_id TEXT NOT NULL REFERENCES dv_pipelines(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'commit', 'pull_request')),
  commit_sha TEXT,
  branch TEXT,
  tree_sha256 TEXT NOT NULL,
  env_id TEXT,
  env_version INTEGER,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'blocked')),
  approval_state TEXT NOT NULL DEFAULT 'none' CHECK (approval_state IN ('none', 'pending', 'approved', 'rejected')),
  approved_by TEXT,
  approved_at TEXT,
  rollback_target TEXT,
  record TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(record)),
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX dv_pipeline_runs_project ON dv_pipeline_runs (project_id, created_at);

CREATE TABLE dv_pipeline_stages (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES dv_pipeline_runs(id),
  position INTEGER NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
  job_id TEXT,
  result TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(result)),
  started_at TEXT,
  finished_at TEXT,
  UNIQUE (run_id, position)
);

CREATE TABLE dv_experiments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  objective TEXT,
  task_type TEXT NOT NULL DEFAULT 'other' CHECK (task_type IN ('binary_classification', 'multiclass_classification', 'regression', 'forecasting', 'other')),
  asset_id TEXT,                                     -- the Data Workspace file (no foreign key: deleting the file must not be blocked)
  dataset_version_id TEXT,
  commit_sha TEXT,
  tree_sha256 TEXT,
  env_id TEXT,
  env_version INTEGER,
  params TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
  feature_set TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(feature_set)),
  preprocessing TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(preprocessing)),
  split_strategy TEXT,
  seed INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'running', 'trained', 'evaluated', 'failed', 'archived')),
  job_id TEXT REFERENCES dv_jobs(id),
  metrics TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metrics)),
  limitations TEXT,
  reg_version_id TEXT REFERENCES reg_versions(id),   -- the Phase 7 registry version this experiment produced (the registry is NOT duplicated)
  approval_state TEXT NOT NULL DEFAULT 'none' CHECK (approval_state IN ('none', 'approved', 'revoked')),
  approved_by TEXT,
  approved_at TEXT,
  rollback_reg_version_id TEXT REFERENCES reg_versions(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX dv_experiments_project ON dv_experiments (project_id, created_at);

CREATE TABLE dv_quota_overrides (                     -- owner overrides, always with a reason; the audit trail is dv_events + audit_events
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL CHECK (scope IN ('user', 'project')),
  scope_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value INTEGER NOT NULL,
  set_by TEXT NOT NULL REFERENCES users(id),
  reason TEXT NOT NULL,
  set_at TEXT NOT NULL,
  cleared_at TEXT
);
CREATE UNIQUE INDEX dv_quota_overrides_live ON dv_quota_overrides (scope, scope_id, key) WHERE cleared_at IS NULL;

CREATE TABLE dv_events (                              -- consolidated project history: who, what, when, to what, with what outcome (no source text, no secrets)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  actor_user_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  outcome TEXT NOT NULL DEFAULT 'success',
  detail TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail)),
  created_at TEXT NOT NULL
);
CREATE INDEX dv_events_project ON dv_events (project_id, id);
