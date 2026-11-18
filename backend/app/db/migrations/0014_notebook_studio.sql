-- Notebook Studio outputs (briefing, study guide, FAQ, timeline, ...). A notebook IS a Research Intelligence workspace; this only adds the generated outputs. Additive.
CREATE TABLE nb_outputs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 40),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  instructions TEXT,
  content TEXT NOT NULL,
  citations TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(citations)),
  meta TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
  source_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_ids)),
  mode TEXT NOT NULL CHECK (mode IN ('model','extractive')),
  provider TEXT,
  model TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX nb_outputs_ws ON nb_outputs (workspace_id, owner_user_id, created_at);
