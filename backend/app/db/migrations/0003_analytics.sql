-- Phase 4: analytical runs, insights and assistant conversations. Large results (and the evidence contract) live in storage under
-- p/<project>/a/<asset>/n/<run>.json; these rows hold metadata and small summaries only, never dataset rows.
-- Plain SQLite SQL (D1-compatible). Migrations 0001 and 0002 are untouched.

CREATE TABLE analysis_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,                       -- dashboard | descriptive | relationships | compare | hypothesis | insights | chart | question
  status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
  evidence_status TEXT NOT NULL CHECK (evidence_status IN ('VERIFIED', 'INCOMPLETE')),
  plan TEXT NOT NULL,                       -- the typed analysis plan (JSON; column names, filters, parameters)
  plan_hash TEXT NOT NULL,
  result_hash TEXT,
  result_key TEXT,                          -- storage key of the full result + evidence contract
  summary TEXT,                             -- small JSON for lists
  error_code TEXT,
  calc_version TEXT NOT NULL,
  rows_used INTEGER,
  duration_ms INTEGER,
  parent_run_id TEXT,                       -- set when this run re-executes another (reproducibility check)
  label TEXT,
  saved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_runs_asset ON analysis_runs (asset_id, created_at);
CREATE INDEX idx_runs_owner ON analysis_runs (owner_user_id, created_at);
CREATE INDEX idx_runs_version ON analysis_runs (version_id, kind, plan_hash);

CREATE TABLE insights (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  category TEXT NOT NULL,
  statement TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'saved', 'dismissed')),
  evidence_status TEXT NOT NULL CHECK (evidence_status IN ('VERIFIED', 'INCOMPLETE')),
  payload TEXT NOT NULL,                    -- the full insight record (JSON: evidence, method, limitations, filters …)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_insights_dedupe ON insights (version_id, dedupe_key);
CREATE INDEX idx_insights_asset ON insights (asset_id, state, created_at);

CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_conversations_asset ON conversations (asset_id, updated_at);

CREATE TABLE conversation_messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  run_id TEXT,
  payload TEXT,                             -- assistant: interpretation, plan, clarification, verification (JSON)
  created_at TEXT NOT NULL
);
CREATE INDEX idx_messages_conversation ON conversation_messages (conversation_id, created_at);
