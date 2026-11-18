-- Phase 6: research, retrieval, world intelligence and AI-provider routing.
-- Rows hold metadata, small summaries and derived text indexes' bookkeeping; document passages, web page text, evidence bundles, news article text and
-- analysis results live in storage under p/<project>/... so project and account deletion removes them with the rest of the project.
-- Plain SQLite SQL (D1-compatible). Migrations 0001-0004 are untouched. Every project-scoped table carries owner_user_id and project_id and every read filters by owner.

-- Consent and preferences: one row per person (project_id NULL) and one per project they decide on. Everything is OFF by default.
CREATE TABLE research_settings (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  project_id TEXT REFERENCES projects(id),
  web_enabled INTEGER NOT NULL DEFAULT 0,
  providers TEXT NOT NULL DEFAULT '[]',          -- JSON list of search-provider keys the person allows
  external_ai INTEGER NOT NULL DEFAULT 0,        -- may an external (non-local) AI provider see project content? never implied by web_enabled
  news_enabled INTEGER NOT NULL DEFAULT 0,
  freshness_days INTEGER,
  cache_days INTEGER,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_research_settings ON research_settings (owner_user_id, COALESCE(project_id, ''));

CREATE TABLE research_sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  question TEXT NOT NULL,
  level TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT 'general' CHECK (purpose IN ('general', 'education')),
  status TEXT NOT NULL CHECK (status IN ('draft', 'queued', 'running', 'completed', 'failed', 'cancelled')),
  options TEXT NOT NULL,                         -- JSON: selected sources, web on/off, providers, freshness (validated, closed vocabulary)
  plan TEXT,                                     -- JSON research plan (bounded)
  plan_hash TEXT,
  approved_plan_hash TEXT,                       -- the plan the person approved for external queries
  latest_bundle_id TEXT,
  run_count INTEGER NOT NULL DEFAULT 0,
  saved INTEGER NOT NULL DEFAULT 0,
  search_text TEXT,                              -- derived: question + finding statements + note text, for searching earlier research
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_run_at TEXT
);
CREATE INDEX idx_research_sessions_owner ON research_sessions (owner_user_id, project_id, updated_at);

-- One table for every long-running piece of work (research runs, news ingestion, embedding builds).
CREATE TABLE research_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('research', 'news_ingest', 'influence')),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  session_id TEXT,
  subject_id TEXT,                               -- news source id (news_ingest) or dataset asset id (influence)
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
  params TEXT NOT NULL,
  extended INTEGER NOT NULL DEFAULT 0,           -- entitlement captured at submission
  progress TEXT,
  error_code TEXT,
  error_message TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  heartbeat_at TEXT,
  result_id TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  duration_ms INTEGER
);
CREATE INDEX idx_research_jobs_owner ON research_jobs (owner_user_id, status, created_at);
CREATE INDEX idx_research_jobs_session ON research_jobs (session_id, created_at);
CREATE INDEX idx_research_jobs_status ON research_jobs (status, heartbeat_at);

-- One row per source per research session (a later run of the same session updates the row). Deleting a session deletes its rows and its stored page snapshots together.
CREATE TABLE research_sources (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  bundle_id TEXT,                                -- the latest bundle that cites it
  source_key TEXT NOT NULL,                      -- stable identity inside the project: canonical URL, or (kind, reference, version)
  kind TEXT NOT NULL CHECK (kind IN ('document', 'dataset', 'analysis', 'model', 'web', 'news', 'evidence')),
  origin_class TEXT NOT NULL CHECK (origin_class IN ('user_provided', 'calculated', 'model_prediction', 'external_research')),
  title TEXT NOT NULL,
  url TEXT,
  domain TEXT,
  publisher TEXT,
  ref_asset_id TEXT,                             -- deliberately no foreign key: deleting the file must not be blocked by a source record
  ref_id TEXT,
  content_hash TEXT,
  text_key TEXT,                                 -- page snapshot (web/news) so a citation can be re-checked later
  retrieved_at TEXT,
  published_at TEXT,
  freshness TEXT,                                -- JSON
  reliability TEXT,                              -- JSON: indicators with the basis for each (no numeric trust score)
  status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'inaccessible', 'blocked', 'stale')),
  status_code TEXT,
  provider TEXT,
  saved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_research_sources ON research_sources (session_id, source_key);
CREATE INDEX idx_research_sources_owner ON research_sources (owner_user_id, project_id, updated_at);
CREATE INDEX idx_research_sources_asset ON research_sources (ref_asset_id);

CREATE TABLE evidence_bundles (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  job_id TEXT,
  number INTEGER NOT NULL,                       -- run number within the session
  question TEXT NOT NULL,
  level TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'insufficient')),
  bundle_key TEXT NOT NULL,
  bundle_sha256 TEXT NOT NULL,
  asset_ids TEXT NOT NULL DEFAULT '[]',          -- files whose text appears in the bundle: deleting one deletes the bundle
  source_count INTEGER NOT NULL DEFAULT 0,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  finding_count INTEGER NOT NULL DEFAULT 0,
  conflict_count INTEGER NOT NULL DEFAULT 0,
  quality_verdict TEXT,
  used_web INTEGER NOT NULL DEFAULT 0,
  label TEXT,
  saved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_evidence_bundles_session ON evidence_bundles (session_id, number);
CREATE INDEX idx_evidence_bundles_owner ON evidence_bundles (owner_user_id, project_id, created_at);

-- Notes are the person's own writing (origin is always the person), never generated.
CREATE TABLE research_notes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  target_type TEXT NOT NULL CHECK (target_type IN ('session', 'finding', 'source', 'bundle')),
  target_id TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_research_notes_session ON research_notes (session_id, created_at);

-- Search and page cache, per person and project, with expiry and an invalidation reason.
CREATE TABLE web_cache (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('search', 'page')),
  cache_key TEXT NOT NULL,
  provider TEXT NOT NULL,
  label TEXT,                                    -- the query or URL, shortened, so the person can see what is cached
  body_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status_code TEXT,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  invalidated_reason TEXT
);
CREATE UNIQUE INDEX uq_web_cache ON web_cache (project_id, owner_user_id, cache_key);
CREATE INDEX idx_web_cache_expiry ON web_cache (expires_at);

-- Derived retrieval index for one document version (rebuildable; the extraction is the source of truth).
CREATE TABLE doc_indexes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  asset_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  extraction_id TEXT,
  index_version TEXT NOT NULL,
  chunk_count INTEGER NOT NULL,
  page_count INTEGER,
  key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  embedding_model TEXT,
  embedding_key TEXT,
  built_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_doc_indexes ON doc_indexes (asset_id, version_id);
CREATE INDEX idx_doc_indexes_owner ON doc_indexes (owner_user_id, project_id);

-- World intelligence
CREATE TABLE news_sources (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  feed_url TEXT NOT NULL,
  publisher TEXT,
  language TEXT,
  policy TEXT NOT NULL,                          -- JSON: terms acknowledged, minimum interval, item cap, retention days, full-text fetch (default off)
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error')),
  last_fetched_at TEXT,
  last_status TEXT,
  last_error_code TEXT,
  article_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_news_sources ON news_sources (project_id, owner_user_id, feed_url);

CREATE TABLE news_articles (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT NOT NULL REFERENCES news_sources(id),
  url TEXT NOT NULL,
  guid_hash TEXT NOT NULL,
  title TEXT NOT NULL,
  publisher TEXT,
  domain TEXT,
  author TEXT,
  published_at TEXT,
  retrieved_at TEXT NOT NULL,
  language TEXT,
  content_hash TEXT NOT NULL,
  text_key TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'quarantined', 'expired')),
  quarantine_reason TEXT,
  duplicate_of TEXT,
  derived TEXT,                                  -- JSON: topics, entities, sentiment, statement types, with method versions and confidence
  derived_version TEXT,
  event_id TEXT,
  expires_at TEXT
);
CREATE UNIQUE INDEX uq_news_articles ON news_articles (project_id, owner_user_id, guid_hash);
CREATE INDEX idx_news_articles_owner ON news_articles (owner_user_id, project_id, status, published_at);
CREATE INDEX idx_news_articles_event ON news_articles (event_id);

CREATE TABLE news_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  label TEXT NOT NULL,
  first_seen TEXT,
  last_seen TEXT,
  article_count INTEGER NOT NULL,
  source_count INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('confirmed', 'reported', 'disputed', 'unverified')),
  summary TEXT NOT NULL,                         -- JSON: basis of the status, topics, entities, quoted lead sentences with article ids
  members_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_news_events_owner ON news_events (owner_user_id, project_id, last_seen);

CREATE TABLE influence_analyses (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  label TEXT,
  config TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  result_key TEXT NOT NULL,
  result_hash TEXT NOT NULL,
  candidate_count INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_influence_owner ON influence_analyses (owner_user_id, project_id, created_at);

-- AI provider routing (server-level configuration owned by the platform owner; secrets are referenced by environment-variable NAME, never stored)
CREATE TABLE ai_providers (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('builtin', 'ollama', 'openai_compatible')),
  label TEXT NOT NULL,
  base_url TEXT,
  model TEXT,
  embed_model TEXT,
  enabled INTEGER NOT NULL DEFAULT 0,
  privacy_class TEXT NOT NULL CHECK (privacy_class IN ('local', 'private_network', 'external')),
  paid INTEGER NOT NULL DEFAULT 0,
  tasks TEXT NOT NULL DEFAULT '[]',              -- JSON list of tasks the owner allows this provider to run
  priority INTEGER NOT NULL DEFAULT 100,         -- lower runs first
  timeout_s INTEGER NOT NULL DEFAULT 30,
  max_retries INTEGER NOT NULL DEFAULT 1,
  secret_env TEXT,                               -- NAME of the environment variable holding a key
  daily_budget_units INTEGER,
  notes TEXT,
  last_probe TEXT,
  last_probe_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO ai_providers (id, key, kind, label, enabled, privacy_class, paid, tasks, priority, timeout_s, max_retries, notes, created_at, updated_at)
VALUES ('builtin', 'builtin', 'builtin', 'Built-in (deterministic, runs inside PredictAI)', 1, 'local', 0,
        '["plan","extract","summarize","classify","sentiment","entities","embed_hash","synthesize"]', 1000, 5, 0,
        'Always available. Rule-based and extractive: it quotes sources and counts, it does not write new claims.', '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z');

CREATE TABLE ai_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id TEXT,
  project_id TEXT,
  provider_key TEXT NOT NULL,
  model TEXT,
  task TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'error', 'timeout', 'skipped', 'refused')),
  error_code TEXT,
  latency_ms INTEGER,
  units_in INTEGER NOT NULL DEFAULT 0,
  units_out INTEGER NOT NULL DEFAULT 0,
  cost_estimate REAL NOT NULL DEFAULT 0,
  privacy_class TEXT,
  fallback_from TEXT,
  reason TEXT,
  occurred_at TEXT NOT NULL
);
CREATE INDEX idx_ai_usage_owner ON ai_usage (owner_user_id, occurred_at);
CREATE INDEX idx_ai_usage_provider ON ai_usage (provider_key, occurred_at);
