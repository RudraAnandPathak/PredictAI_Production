-- Which older research sessions were already copied into a notebook, so an import never makes a second copy. Additive.
CREATE TABLE nb_imports (
  session_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  created_at TEXT NOT NULL
);
CREATE INDEX nb_imports_owner ON nb_imports (owner_user_id);
