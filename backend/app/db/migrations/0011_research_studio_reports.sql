-- Phase 10 completion round 2: R7 Research Studio and R8 reports. Additive: new ri_* tables only; no existing table is changed.

-- A relation between two saved findings of one workspace (the "connect related findings" tool).
CREATE TABLE ri_finding_links (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  from_id TEXT NOT NULL REFERENCES ri_findings(id),
  to_id TEXT NOT NULL REFERENCES ri_findings(id),
  relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts','extends','related')),
  note TEXT CHECK (note IS NULL OR length(note) <= 1000),
  created_at TEXT NOT NULL,
  CHECK (from_id <> to_id),
  UNIQUE (from_id, to_id, relation)
);
CREATE INDEX ri_finding_links_ws ON ri_finding_links (workspace_id, owner_user_id);

-- Named collections of findings ("organise evidence").
CREATE TABLE ri_finding_groups (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  created_at TEXT NOT NULL,
  UNIQUE (workspace_id, owner_user_id, name)
);
CREATE TABLE ri_finding_group_items (
  group_id TEXT NOT NULL REFERENCES ri_finding_groups(id),
  finding_id TEXT NOT NULL REFERENCES ri_findings(id),
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, finding_id)
);

-- Research outlines: a tree of headings with notes and linked findings, stored as JSON and validated by the service.
CREATE TABLE ri_outlines (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  items TEXT NOT NULL CHECK (json_valid(items)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ri_outlines_ws ON ri_outlines (workspace_id, owner_user_id, updated_at);

-- Saved studio outputs (research questions, hypotheses, literature-review drafts). Always labelled as generated; each item keeps its quoted basis.
CREATE TABLE ri_generated (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  analysis_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('research_questions','hypotheses','literature_review')),
  title TEXT NOT NULL,
  content TEXT NOT NULL CHECK (json_valid(content)),
  source_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_ids)),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_generated_ws ON ri_generated (workspace_id, owner_user_id, created_at);

-- Reports. `sections` is the current (possibly edited) document; every edit or regeneration first stores the previous one in ri_report_versions.
CREATE TABLE ri_reports (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES rw_workspaces(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  analysis_id TEXT,
  report_type TEXT NOT NULL CHECK (report_type IN ('research_summary','literature_review','comparative','gap_analysis','methodology_comparison','evidence_synthesis','academic')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  notice TEXT NOT NULL,
  sources TEXT NOT NULL CHECK (json_valid(sources)),
  sections TEXT NOT NULL CHECK (json_valid(sections)),
  version INTEGER NOT NULL DEFAULT 1,
  edited INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ri_reports_ws ON ri_reports (workspace_id, owner_user_id, updated_at);
CREATE TABLE ri_report_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id TEXT NOT NULL REFERENCES ri_reports(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  version INTEGER NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('edit','regenerate','restore')),
  title TEXT NOT NULL,
  sections TEXT NOT NULL CHECK (json_valid(sections)),
  created_at TEXT NOT NULL
);
CREATE INDEX ri_report_versions_r ON ri_report_versions (report_id, id);
