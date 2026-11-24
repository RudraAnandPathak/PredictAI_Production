-- Personal API tokens: let a person's own scripts and AI agents (for example the PredictAI agent skill) use a limited part of the API
-- without a browser session. Only a SHA-256 hash of each token is stored; the token itself is shown once, when it is created. Additive.
CREATE TABLE api_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  token_hash TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT '["analytics"]' CHECK (json_valid(scopes)),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT,
  revoked_reason TEXT
);
CREATE INDEX api_tokens_user ON api_tokens (user_id, revoked_at);
