-- Phase 10A: model monitoring. Additive: new mon_* tables only; no existing table is changed.

-- One drift check: a registered version's training data compared with a later dataset. The result is stored so it can be re-read and compared over time.
CREATE TABLE mon_checks (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES reg_models(id),
  version_id TEXT NOT NULL REFERENCES reg_versions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  reference_version_id TEXT,                -- dataset version the model was trained on (no foreign key: deleting it must not be blocked)
  current_asset_id TEXT,
  current_version_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('stable','moderate','significant','insufficient_data')),
  result TEXT NOT NULL,                     -- JSON: per-column figures, thresholds, notes
  created_at TEXT NOT NULL
);
CREATE INDEX mon_checks_version ON mon_checks (version_id, created_at);
CREATE INDEX mon_checks_owner ON mon_checks (owner_user_id, created_at);

-- An actual outcome the owner recorded for a stored single prediction. This is the only source of performance (concept-drift) evidence.
CREATE TABLE mon_outcomes (
  id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES reg_versions(id),
  prediction_id TEXT NOT NULL REFERENCES ml_predictions(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  actual TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE (prediction_id)
);
CREATE INDEX mon_outcomes_version ON mon_outcomes (version_id, recorded_at);
