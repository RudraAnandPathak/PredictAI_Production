-- Phase 5: predictive modelling. Training jobs, saved models and prediction history. Model files, reports and batch outputs live in storage under
-- p/<project>/a/<asset>/m/<model>/...; these rows hold metadata and small summaries only, never dataset rows.
-- Plain SQLite SQL (D1-compatible). Migrations 0001-0003 are untouched. Every table carries owner_user_id and project_id; every read is filtered by owner.

CREATE TABLE ml_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL DEFAULT 'train' CHECK (kind IN ('train')),
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
  config TEXT NOT NULL,                     -- the validated training configuration (JSON; closed vocabulary, no code)
  config_hash TEXT NOT NULL,
  extended INTEGER NOT NULL DEFAULT 0,      -- entitlement captured at submission, so limits cannot change under a running job
  label TEXT,
  progress TEXT,                            -- JSON: phase, message, fraction, elapsed
  error_code TEXT,
  error_message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  model_id TEXT,
  heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  duration_ms INTEGER
);
CREATE INDEX idx_ml_jobs_owner ON ml_jobs (owner_user_id, status, created_at);
CREATE INDEX idx_ml_jobs_asset ON ml_jobs (asset_id, created_at);
CREATE INDEX idx_ml_jobs_status ON ml_jobs (status, heartbeat_at);

CREATE TABLE ml_models (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  version_id TEXT NOT NULL REFERENCES asset_versions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  job_id TEXT,
  name TEXT NOT NULL,
  model_version INTEGER NOT NULL,           -- 1, 2, 3 ... per dataset and target
  target TEXT NOT NULL,
  problem_type TEXT NOT NULL CHECK (problem_type IN ('binary_classification', 'multiclass_classification', 'regression')),
  algorithm TEXT NOT NULL,
  tuned INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready')),
  primary_metric TEXT NOT NULL,
  cv_score REAL,
  test_score REAL,
  seed INTEGER NOT NULL,
  ml_version TEXT NOT NULL,
  config TEXT NOT NULL,                     -- full training configuration (JSON)
  feature_config TEXT NOT NULL,             -- features used and dropped, with the reason and treatment of each (JSON)
  preprocessing_config TEXT NOT NULL,       -- preprocessing settings (JSON)
  hyperparameters TEXT NOT NULL,            -- the fitted estimator's parameters (JSON)
  metrics TEXT NOT NULL,                    -- headline held-out metrics (JSON)
  input_schema TEXT NOT NULL,               -- the columns a prediction needs and how each is read (JSON)
  dataset_snapshot TEXT NOT NULL,           -- dataset asset, version, checksum, row counts at training time (JSON)
  model_key TEXT NOT NULL,
  model_sha256 TEXT NOT NULL,               -- verified before a model file is ever opened
  model_bytes INTEGER NOT NULL,
  report_key TEXT NOT NULL,
  background_key TEXT,
  duration_ms INTEGER,
  trained_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_ml_models_asset ON ml_models (asset_id, created_at);
CREATE INDEX idx_ml_models_owner ON ml_models (owner_user_id, created_at);
CREATE INDEX idx_ml_models_target ON ml_models (asset_id, target, model_version);

CREATE TABLE ml_predictions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  model_id TEXT NOT NULL REFERENCES ml_models(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('single', 'batch')),
  source_asset_id TEXT,                     -- batch: the dataset that was predicted (no foreign key: deleting it must not be blocked)
  source_version_id TEXT,
  inputs TEXT,                              -- single: the values the person entered (JSON)
  result TEXT NOT NULL,                     -- single: the prediction; batch: the summary (JSON)
  output_key TEXT,                          -- batch: the CSV of predictions in storage
  output_bytes INTEGER,
  rows INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_ml_predictions_model ON ml_predictions (model_id, created_at);
CREATE INDEX idx_ml_predictions_owner ON ml_predictions (owner_user_id, created_at);
