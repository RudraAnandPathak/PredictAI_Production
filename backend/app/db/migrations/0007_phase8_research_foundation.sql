-- Phase 8: foundation for the Phase 10 Research Intelligence module (account-owned research workspaces, their sources, conversations and notes).
-- Create-only: nothing from Phases 1-7 is altered. Identity tables (users, user_roles, sessions, access codes, auth tokens, audit events) already exist from Phase 2 and are NOT duplicated.
-- The Phase 6 tables research_sessions / research_sources / research_notes belong to the project-scoped "research a question" feature and are left untouched; these rw_* tables are a
-- separate, workspace-scoped structure so neither feature can corrupt the other.
-- Every row carries owner_user_id. Triggers refuse a child row whose owner differs from its workspace's owner, so an application bug cannot attach one person's data to another's workspace.
-- No ingestion, retrieval or answering lives here: Phase 8 stores and protects the structures; Phase 10 will fill them.

CREATE TABLE rw_workspaces (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  description TEXT CHECK (description IS NULL OR length(description) <= 2000),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX rw_workspaces_name ON rw_workspaces (owner_user_id, name COLLATE NOCASE);
CREATE INDEX rw_workspaces_owner ON rw_workspaces (owner_user_id, status, updated_at);

CREATE TABLE rw_sources (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('pdf', 'docx', 'web_article', 'academic_paper', 'csv', 'xlsx')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  ref_asset_id TEXT,                              -- an uploaded file in the owner's project (deliberately no foreign key: deleting the file must not be blocked)
  url TEXT,
  content_hash TEXT,
  page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
  byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX rw_sources_workspace ON rw_sources (workspace_id, created_at);
CREATE INDEX rw_sources_owner ON rw_sources (owner_user_id);
CREATE UNIQUE INDEX rw_sources_hash ON rw_sources (workspace_id, content_hash) WHERE content_hash IS NOT NULL;

CREATE TABLE rw_conversations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX rw_conversations_workspace ON rw_conversations (workspace_id, updated_at);

CREATE TABLE rw_messages (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  conversation_id TEXT NOT NULL REFERENCES rw_conversations(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  body TEXT NOT NULL,
  source_refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_refs)),   -- JSON list of {source_id, locator}; Phase 10 defines the locator
  created_at TEXT NOT NULL
);
CREATE INDEX rw_messages_conversation ON rw_messages (conversation_id, seq);

CREATE TABLE rw_notes (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  source_id TEXT REFERENCES rw_sources(id),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX rw_notes_workspace ON rw_notes (workspace_id, created_at);

-- Ownership consistency (defence in depth; the services check ownership first).
CREATE TRIGGER rw_sources_owner_ck BEFORE INSERT ON rw_sources
WHEN (SELECT owner_user_id FROM rw_workspaces WHERE id = NEW.workspace_id) IS NOT NEW.owner_user_id
BEGIN SELECT RAISE(ABORT, 'rw_sources owner must match its workspace owner'); END;

CREATE TRIGGER rw_conversations_owner_ck BEFORE INSERT ON rw_conversations
WHEN (SELECT owner_user_id FROM rw_workspaces WHERE id = NEW.workspace_id) IS NOT NEW.owner_user_id
BEGIN SELECT RAISE(ABORT, 'rw_conversations owner must match its workspace owner'); END;

CREATE TRIGGER rw_messages_owner_ck BEFORE INSERT ON rw_messages
WHEN (SELECT owner_user_id FROM rw_conversations WHERE id = NEW.conversation_id) IS NOT NEW.owner_user_id
  OR (SELECT workspace_id FROM rw_conversations WHERE id = NEW.conversation_id) IS NOT NEW.workspace_id
BEGIN SELECT RAISE(ABORT, 'rw_messages owner and workspace must match their conversation'); END;

CREATE TRIGGER rw_notes_owner_ck BEFORE INSERT ON rw_notes
WHEN (SELECT owner_user_id FROM rw_workspaces WHERE id = NEW.workspace_id) IS NOT NEW.owner_user_id
  OR (NEW.source_id IS NOT NULL AND (SELECT workspace_id FROM rw_sources WHERE id = NEW.source_id) IS NOT NEW.workspace_id)
BEGIN SELECT RAISE(ABORT, 'rw_notes owner and source must match their workspace'); END;
