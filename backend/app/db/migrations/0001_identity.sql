-- Identity, sessions, authorization, access codes, tokens, audit and rate limiting.
-- Timestamps are UTC ISO-8601 text. Plain SQLite SQL, so it also runs on Cloudflare D1.

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  email_normalized TEXT NOT NULL UNIQUE,
  display_name TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'blocked', 'deleted')),
  verified_at TEXT,
  blocked_at TEXT,
  blocked_reason TEXT,
  authz_version INTEGER NOT NULL DEFAULT 1,
  password_changed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);

-- One row per way of proving identity: provider 'password' or an OAuth provider.
CREATE TABLE identities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_subject TEXT NOT NULL,
  password_hash TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  UNIQUE (provider, provider_subject)
);
CREATE INDEX idx_identities_user ON identities (user_id);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  issued_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  absolute_expires_at TEXT NOT NULL,
  reauthenticated_at TEXT NOT NULL,
  rotated_from TEXT,
  persistent INTEGER NOT NULL DEFAULT 1,
  revoked_at TEXT,
  revoked_reason TEXT,
  device_label TEXT
);
CREATE INDEX idx_sessions_user ON sessions (user_id, revoked_at);

CREATE TABLE user_roles (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  granted_by TEXT,
  granted_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT
);
CREATE UNIQUE INDEX uq_user_roles_active ON user_roles (user_id, role) WHERE revoked_at IS NULL;

CREATE TABLE access_codes (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  tier TEXT NOT NULL,
  label TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  target_user_id TEXT REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  used_by TEXT REFERENCES users(id),
  revoked_at TEXT,
  revoked_by TEXT
);

CREATE TABLE entitlements (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tier TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('access_code', 'owner_grant')),
  access_code_id TEXT REFERENCES access_codes(id),
  granted_by TEXT,
  activated_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT,
  revoked_reason TEXT
);
CREATE UNIQUE INDEX uq_entitlements_active ON entitlements (user_id, tier) WHERE revoked_at IS NULL;

-- Single-use proofs: email verification, password reset, OAuth state.
CREATE TABLE auth_tokens (
  id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  payload TEXT
);
CREATE INDEX idx_auth_tokens_user ON auth_tokens (user_id, purpose);

-- Append-only security and administration history. Metadata only: never passwords,
-- tokens, codes or document content.
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
  actor_user_id TEXT,
  target_user_id TEXT,
  request_id TEXT,
  source_ip TEXT,
  reason TEXT,
  metadata TEXT
);
CREATE INDEX idx_audit_time ON audit_events (occurred_at);
CREATE INDEX idx_audit_type ON audit_events (event_type, occurred_at);
CREATE INDEX idx_audit_target ON audit_events (target_user_id, occurred_at);
CREATE INDEX idx_audit_actor ON audit_events (actor_user_id, occurred_at);

-- Keyed hashes only: no raw emails or IP addresses are stored here.
CREATE TABLE rate_limit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bucket TEXT NOT NULL,
  key_hash TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE INDEX idx_rate_limit ON rate_limit_events (bucket, key_hash, occurred_at);
