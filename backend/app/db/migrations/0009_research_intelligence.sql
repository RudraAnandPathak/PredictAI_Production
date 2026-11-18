-- Phase 10: Research Intelligence (independent of the Phase 6 research engine). Create-only: nothing from Phases 1-9 is altered.
-- It EXTENDS the Phase 8 foundation instead of duplicating it: rw_workspaces, rw_conversations, rw_messages and rw_notes are used as they are, and every ri_sources row shares its id with a rw_sources row
-- (so workspace deletion, source limits, content-hash de-duplication, note links and owner-consistency triggers all keep working). rw_sources.kind is the coarse category
-- (txt/markdown are stored under 'docx' = "document"); the exact format lives in ri_sources.format.
-- Every row carries owner_user_id and workspace_id; the services filter on both and triggers refuse mismatches.

CREATE TABLE ri_sources (
  id TEXT PRIMARY KEY REFERENCES rw_sources(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  format TEXT NOT NULL CHECK (format IN ('pdf','docx','txt','markdown','csv','xlsx','html','metadata')),
  origin TEXT NOT NULL CHECK (origin IN ('upload','url','doi','arxiv','discovery')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  authors TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(authors)),
  published TEXT,
  abstract TEXT,
  doi TEXT,
  url TEXT,
  identifier TEXT,
  venue TEXT,
  keywords TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(keywords)),
  access TEXT NOT NULL DEFAULT 'full_text' CHECK (access IN ('full_text','abstract_only','metadata_only')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','failed','cancelled')),
  stage TEXT,
  error_code TEXT,
  error_message TEXT,
  page_count INTEGER,
  char_count INTEGER NOT NULL DEFAULT 0,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  table_count INTEGER NOT NULL DEFAULT 0,
  embed_status TEXT NOT NULL DEFAULT 'none' CHECK (embed_status IN ('none','pending','ready','failed')),
  embed_model TEXT,
  byte_size INTEGER,
  content_sha256 TEXT,
  stored_name TEXT,
  malware TEXT NOT NULL DEFAULT 'not_scanned' CHECK (malware IN ('clean','not_scanned','infected')),
  injection_flags INTEGER NOT NULL DEFAULT 0,
  structure TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(structure)),
  refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(refs)),
  intelligence TEXT CHECK (intelligence IS NULL OR json_valid(intelligence)),
  included INTEGER NOT NULL DEFAULT 1 CHECK (included IN (0,1)),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ri_sources_workspace ON ri_sources (workspace_id, owner_user_id, status);
CREATE UNIQUE INDEX ri_sources_doi ON ri_sources (workspace_id, doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX ri_sources_url ON ri_sources (workspace_id, url) WHERE url IS NOT NULL;

CREATE TABLE ri_source_versions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  version INTEGER NOT NULL,
  content_sha256 TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, version)
);

CREATE TABLE ri_chunks (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  version INTEGER NOT NULL DEFAULT 1,
  ordinal INTEGER NOT NULL,
  text TEXT NOT NULL,
  section TEXT,
  page_start INTEGER,
  page_end INTEGER,
  locator TEXT NOT NULL,
  words INTEGER NOT NULL,
  flagged INTEGER NOT NULL DEFAULT 0,
  text_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, version, ordinal)
);
CREATE INDEX ri_chunks_ws ON ri_chunks (workspace_id, owner_user_id, source_id);

CREATE VIRTUAL TABLE ri_chunks_fts USING fts5(text, section, content='ri_chunks', content_rowid='seq', tokenize='porter unicode61');
CREATE TRIGGER ri_chunks_ai AFTER INSERT ON ri_chunks BEGIN
  INSERT INTO ri_chunks_fts(rowid, text, section) VALUES (new.seq, new.text, COALESCE(new.section, ''));
END;
CREATE TRIGGER ri_chunks_ad AFTER DELETE ON ri_chunks BEGIN
  INSERT INTO ri_chunks_fts(ri_chunks_fts, rowid, text, section) VALUES ('delete', old.seq, old.text, COALESCE(old.section, ''));
END;

CREATE TABLE ri_embeddings (
  chunk_seq INTEGER NOT NULL REFERENCES ri_chunks(seq),
  model TEXT NOT NULL,
  provider TEXT NOT NULL,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  dims INTEGER NOT NULL,
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (chunk_seq, model)
);
CREATE INDEX ri_embeddings_ws ON ri_embeddings (workspace_id, owner_user_id, model);

CREATE TABLE ri_conversation_scope (
  conversation_id TEXT NOT NULL REFERENCES rw_conversations(id),
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  mode TEXT NOT NULL CHECK (mode IN ('include','exclude')),
  PRIMARY KEY (conversation_id, source_id)
);

CREATE TABLE ri_citations (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES rw_conversations(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  chunk_id TEXT NOT NULL,
  label TEXT NOT NULL,
  page INTEGER,
  locator TEXT NOT NULL,
  quote TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX ri_citations_message ON ri_citations (message_id);
CREATE INDEX ri_citations_source ON ri_citations (source_id);

CREATE TABLE ri_message_meta (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES rw_conversations(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  mode TEXT NOT NULL CHECK (mode IN ('model','extractive','insufficient_evidence')),
  provider TEXT,
  model TEXT,
  external INTEGER NOT NULL DEFAULT 0 CHECK (external IN (0,1)),
  removed_unsupported INTEGER NOT NULL DEFAULT 0,
  retrieval TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(retrieval)),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_message_meta_conv ON ri_message_meta (conversation_id);

CREATE TABLE ri_findings (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT NOT NULL REFERENCES ri_sources(id),
  chunk_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('finding','highlight','evidence')),
  text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
  comment TEXT CHECK (comment IS NULL OR length(comment) <= 2000),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_findings_ws ON ri_findings (workspace_id, owner_user_id, created_at);

CREATE TABLE ri_workspace_settings (
  workspace_id TEXT PRIMARY KEY REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  ai_data_policy TEXT NOT NULL DEFAULT 'local_only' CHECK (ai_data_policy IN ('local_only','external_allowed')),
  consent_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE ri_jobs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT REFERENCES ri_sources(id),
  kind TEXT NOT NULL CHECK (kind IN ('ingest','embed','reindex','fetch_url','resolve_identifier')),
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
CREATE INDEX ri_jobs_queue ON ri_jobs (status, run_after);
CREATE INDEX ri_jobs_ws ON ri_jobs (workspace_id, owner_user_id, created_at);

CREATE TABLE ri_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('ai_request','import','embed_units','retrieval','upload_bytes')),
  units INTEGER NOT NULL DEFAULT 1,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ri_usage_owner ON ri_usage (owner_user_id, kind, created_at);

CREATE TRIGGER ri_sources_owner_ck BEFORE INSERT ON ri_sources
WHEN (SELECT owner_user_id FROM rw_sources WHERE id = NEW.id) IS NOT NEW.owner_user_id
  OR (SELECT workspace_id FROM rw_sources WHERE id = NEW.id) IS NOT NEW.workspace_id
BEGIN SELECT RAISE(ABORT, 'ri_sources must match its rw_sources row'); END;

CREATE TRIGGER ri_chunks_owner_ck BEFORE INSERT ON ri_chunks
WHEN (SELECT owner_user_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.owner_user_id
  OR (SELECT workspace_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.workspace_id
BEGIN SELECT RAISE(ABORT, 'ri_chunks owner and workspace must match their source'); END;

CREATE TRIGGER ri_embeddings_owner_ck BEFORE INSERT ON ri_embeddings
WHEN (SELECT owner_user_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.owner_user_id
  OR (SELECT workspace_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.workspace_id
  OR (SELECT source_id FROM ri_chunks WHERE seq = NEW.chunk_seq) IS NOT NEW.source_id
BEGIN SELECT RAISE(ABORT, 'ri_embeddings owner, workspace and source must match their chunk'); END;

CREATE TRIGGER ri_citations_owner_ck BEFORE INSERT ON ri_citations
WHEN (SELECT owner_user_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.owner_user_id
  OR (SELECT workspace_id FROM ri_sources WHERE id = NEW.source_id) IS NOT NEW.workspace_id
BEGIN SELECT RAISE(ABORT, 'ri_citations must cite a source in the same workspace and owner'); END;
