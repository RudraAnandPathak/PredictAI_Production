-- Projects, data workspace, governance. Timestamps are UTC ISO-8601 text; JSON columns hold metadata only
-- (never file contents). Plain SQLite SQL, so it also runs on Cloudflare D1. Migration 0001 is untouched.

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT,
  description TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'archived', 'deleting', 'deleted')),
  metadata TEXT,
  retention_class TEXT NOT NULL DEFAULT 'standard',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  deleted_at TEXT
);
CREATE INDEX idx_projects_owner ON projects (owner_user_id, status, updated_at);

CREATE TABLE project_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES projects(id),
  actor_user_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  subject_id TEXT,
  detail TEXT,
  occurred_at TEXT NOT NULL
);
CREATE INDEX idx_project_events ON project_events (project_id, id);

CREATE TABLE assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('dataset', 'document')),
  name TEXT NOT NULL,
  detected_format TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed', 'deleting')),
  status_code TEXT,
  status_detail TEXT,
  source_checksum TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  current_version_id TEXT,
  page_count INTEGER,
  page_count_estimated INTEGER NOT NULL DEFAULT 0,
  row_count INTEGER,
  column_count INTEGER,
  retention_class TEXT NOT NULL DEFAULT 'source',
  retain_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_assets_project ON assets (project_id, status, created_at);
CREATE INDEX idx_assets_owner ON assets (owner_user_id, kind, status);
-- A retried or repeated upload of the same bytes can never create a second live asset in a project.
CREATE UNIQUE INDEX uq_assets_dedupe ON assets (project_id, source_checksum) WHERE status IN ('processing', 'ready');

CREATE TABLE asset_versions (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  version_number INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('original', 'derived')),
  parent_version_id TEXT,
  transformation_id TEXT,
  storage_key TEXT NOT NULL,
  canonical_key TEXT,
  checksum_sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  format TEXT NOT NULL,
  row_count INTEGER,
  column_count INTEGER,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (asset_id, version_number)
);
CREATE INDEX idx_versions_asset ON asset_versions (asset_id, version_number);

CREATE TABLE uploads (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,
  declared_size INTEGER NOT NULL,
  declared_checksum TEXT,
  chunk_size INTEGER NOT NULL,
  total_chunks INTEGER NOT NULL,
  received_bytes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('open', 'assembling', 'completed', 'cancelled', 'expired', 'failed')),
  error_code TEXT,
  asset_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX idx_uploads_owner ON uploads (owner_user_id, status);
CREATE INDEX idx_uploads_expiry ON uploads (status, expires_at);

CREATE TABLE upload_chunks (
  upload_id TEXT NOT NULL REFERENCES uploads(id),
  chunk_index INTEGER NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  PRIMARY KEY (upload_id, chunk_index)
);

CREATE TABLE extractions (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('ready', 'partial', 'failed')),
  parser TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  summary TEXT,
  warnings TEXT,
  text_key TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_extractions_asset ON extractions (asset_id, version_id);

CREATE TABLE profiles (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  profiler_version TEXT NOT NULL,
  source_checksum TEXT NOT NULL,
  result_key TEXT NOT NULL,
  summary TEXT,
  generated_at TEXT NOT NULL
);
CREATE INDEX idx_profiles_version ON profiles (version_id, generated_at);

CREATE TABLE transformations (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('clean', 'rollback')),
  status TEXT NOT NULL CHECK (status IN ('previewed', 'applied', 'discarded', 'expired', 'failed')),
  source_version_id TEXT NOT NULL,
  result_version_id TEXT,
  operations TEXT NOT NULL,
  explanation TEXT,
  preview TEXT,
  summary TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  applied_by TEXT,
  applied_at TEXT
);
CREATE INDEX idx_transformations_asset ON transformations (asset_id, created_at);

-- Completion evidence for deletions. Counts and identifiers only: no names, no contents.
CREATE TABLE deletion_records (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK (scope IN ('project', 'asset', 'account', 'retention')),
  resource_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
  counts TEXT,
  error_code TEXT,
  requested_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX idx_deletion_records ON deletion_records (status, requested_at);
