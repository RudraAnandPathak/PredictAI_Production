-- Phase 7: Model Registry, Exam Intelligence, Advanced Intelligence, Reports, Collaboration, Scheduling, Owner operations.
-- Additive only: no existing table is altered or dropped. Every table keeps the owner and project so reads can be isolated.
-- Rollback (manual, destroys Phase 7 data): drop the tables below in reverse order. No automatic down-migration is provided on purpose.

-- ================================================================== Model Registry
CREATE TABLE reg_models (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT,
  model_type TEXT NOT NULL CHECK (model_type IN ('binary_classification', 'multiclass_classification', 'regression', 'forecasting', 'other')),
  active_version_id TEXT,                          -- the version whose lifecycle state is 'active' (metadata only: nothing is deployed)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX reg_models_name ON reg_models(owner_user_id, project_id, name);

CREATE TABLE reg_versions (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES reg_models(id),
  version INTEGER NOT NULL,
  source_model_id TEXT NOT NULL,                   -- the Phase 5 model it was registered from (no foreign key: deleting that model must not delete this record)
  status TEXT NOT NULL CHECK (status IN ('draft', 'registered', 'evaluated', 'staging', 'active', 'archived', 'failed')),
  metadata TEXT NOT NULL,                          -- snapshot: features, target, preprocessing, algorithm, hyperparameters, configuration, metrics, dataset lineage (JSON)
  evaluation TEXT NOT NULL,                        -- metrics split into validation (cross-validation) and independent hold-out test, plus leakage notes (JSON)
  artifact_key TEXT NOT NULL,                      -- the registry's own immutable copy of the artifact
  artifact_sha256 TEXT NOT NULL,
  artifact_bytes INTEGER NOT NULL,
  last_verified_at TEXT,
  last_verified_ok INTEGER,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE (model_id, version)
);
CREATE INDEX reg_versions_model ON reg_versions(model_id, version);
CREATE INDEX reg_versions_source ON reg_versions(source_model_id);

CREATE TABLE reg_events (                           -- append-only history: registration, transitions, rollback, metadata notes, verification
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id TEXT NOT NULL REFERENCES reg_models(id),
  version_id TEXT,
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  event TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX reg_events_model ON reg_events(model_id, id);

-- ================================================================== Exam Intelligence
CREATE TABLE exam_subjects (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  level TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX exam_subjects_name ON exam_subjects(owner_user_id, project_id, name, level);

CREATE TABLE exam_documents (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  asset_id TEXT NOT NULL,
  version_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('syllabus', 'curriculum', 'notes', 'slides', 'past_paper', 'other')),
  title TEXT NOT NULL,
  exam_year INTEGER,
  status TEXT NOT NULL,                            -- analysed | analysed_with_warnings | failed
  quality TEXT NOT NULL,                           -- OCR confidence, extraction warnings, counts (JSON)
  structure TEXT NOT NULL,                         -- headings, pages, instructions, answer sections (JSON)
  analysed_at TEXT NOT NULL,
  UNIQUE (subject_id, asset_id, version_id)
);

CREATE TABLE exam_curriculum (                      -- chapters/topics/subtopics read from a syllabus or curriculum document
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  document_id TEXT NOT NULL REFERENCES exam_documents(id),
  chapter TEXT NOT NULL,
  topic TEXT,
  subtopic TEXT,
  keywords TEXT NOT NULL,
  source_ref TEXT NOT NULL
);
CREATE INDEX exam_curriculum_subject ON exam_curriculum(subject_id);

CREATE TABLE exam_questions (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  document_id TEXT NOT NULL REFERENCES exam_documents(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  number TEXT,
  text TEXT NOT NULL,                              -- exactly as extracted, never edited
  qtype TEXT NOT NULL CHECK (qtype IN ('mcq', 'short', 'long', 'true_false', 'fill_blank', 'unknown')),
  marks REAL,
  options TEXT,
  answer_text TEXT,
  page INTEGER,
  confidence REAL NOT NULL,
  warnings TEXT,
  source_ref TEXT NOT NULL,
  text_sha256 TEXT NOT NULL
);
CREATE INDEX exam_questions_doc ON exam_questions(document_id);
CREATE INDEX exam_questions_subject ON exam_questions(subject_id);

CREATE TABLE exam_mappings (                        -- append-only: an automatic mapping and every later correction, so the original is never overwritten
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id TEXT NOT NULL REFERENCES exam_questions(id),
  curriculum_id TEXT,
  chapter TEXT,
  topic TEXT,
  subtopic TEXT,
  confidence REAL NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('auto', 'user')),
  reason TEXT,
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE INDEX exam_mappings_q ON exam_mappings(question_id, id);

CREATE TABLE exam_analyses (                        -- stored frequency/emphasis results so a report can cite exactly what was calculated
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  params TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE exam_blueprints (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  spec TEXT NOT NULL,
  plan TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE exam_assessments (
  id TEXT PRIMARY KEY,
  blueprint_id TEXT NOT NULL REFERENCES exam_blueprints(id),
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'rejected')),
  validation TEXT NOT NULL,
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE exam_assessment_questions (
  id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES exam_assessments(id),
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  qtype TEXT NOT NULL,
  marks REAL NOT NULL,
  difficulty TEXT NOT NULL,
  options TEXT,
  answer TEXT,                                     -- MCQ key or model answer (JSON)
  marking TEXT,                                    -- marking points (JSON)
  chapter TEXT,
  topic TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('past_paper', 'source_derived', 'template_generated', 'user_edited')),
  source_question_id TEXT,
  source_ref TEXT,
  included INTEGER NOT NULL DEFAULT 1,
  edited INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX exam_aq ON exam_assessment_questions(assessment_id, position);

-- ================================================================== Advanced Intelligence
CREATE TABLE adv_runs (                             -- immutable: a scenario keeps its baseline, a result keeps its inputs and reproduction information
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('root_cause', 'metric', 'scenario', 'causal')),
  asset_id TEXT,
  version_id TEXT,
  model_id TEXT,
  title TEXT NOT NULL,
  params TEXT NOT NULL,
  result TEXT NOT NULL,
  registers TEXT NOT NULL,                         -- retrieved facts / calculated results / interpretations / suggestions / unresolved questions (JSON)
  reproduction TEXT NOT NULL,
  result_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX adv_runs_project ON adv_runs(owner_user_id, project_id, created_at);

-- ================================================================== Reports
CREATE TABLE rep_templates (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT REFERENCES users(id),         -- NULL = system template
  key TEXT NOT NULL,
  name TEXT NOT NULL,
  report_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  spec TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX rep_templates_key ON rep_templates(COALESCE(owner_user_id, ''), key, version);

CREATE TABLE rep_reports (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  report_type TEXT NOT NULL,
  source_kind TEXT NOT NULL,                       -- research | dataset | prediction | exam | combined | blank
  source_ref TEXT NOT NULL,                        -- {"bundle_ids": [...], "analysis_ids": [...], ...} (JSON)
  current_version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX rep_reports_owner ON rep_reports(owner_user_id, project_id, updated_at);

CREATE TABLE rep_versions (                         -- immutable: an edit creates the next row
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES rep_reports(id),
  version INTEGER NOT NULL,
  content TEXT NOT NULL,                           -- sections and blocks, each claim with its evidence ids (JSON)
  settings TEXT NOT NULL,                          -- audience, tone, length, citation style, layout, fonts, header/footer (JSON)
  template_key TEXT,
  template_version INTEGER,
  readiness TEXT NOT NULL,                         -- {"status": ..., "reasons": [...]} (JSON)
  content_sha256 TEXT NOT NULL,
  author_user_id TEXT NOT NULL REFERENCES users(id),
  note TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (report_id, version)
);

CREATE TABLE rep_evidence (                         -- the Report Evidence Bundle: append-only, shared by all versions of a report
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES rep_reports(id),
  ref TEXT NOT NULL,                               -- E1, E2 ... as written in the report
  source_id TEXT,
  source_type TEXT NOT NULL,                       -- document | web | wikipedia | arxiv | dataset_calculation | model | exam | assumption
  location TEXT,
  citation TEXT NOT NULL,                          -- structured metadata (JSON); formatted citations are produced from it, never stored
  anchor TEXT,
  passage TEXT,
  calculation_ref TEXT,
  dataset_version TEXT,
  model_version TEXT,
  source_date TEXT,
  retrieved_at TEXT,
  verification TEXT NOT NULL,
  uncertainty TEXT,
  limitation TEXT,
  bundle_ref TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  UNIQUE (report_id, ref)
);

CREATE TABLE rep_exports (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES rep_reports(id),
  version INTEGER NOT NULL,
  format TEXT NOT NULL,
  storage_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  readiness TEXT NOT NULL,
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  origin TEXT NOT NULL DEFAULT 'manual',           -- manual | scheduled
  created_at TEXT NOT NULL
);
CREATE INDEX rep_exports_report ON rep_exports(report_id, created_at);

-- ================================================================== Collaboration
CREATE TABLE ws_workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  created_at TEXT NOT NULL
);

CREATE TABLE ws_members (
  workspace_id TEXT NOT NULL REFERENCES ws_workspaces(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  added_by TEXT NOT NULL REFERENCES users(id),
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX ws_members_user ON ws_members(user_id, status);

CREATE TABLE ws_invitations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES ws_workspaces(id),
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
  token_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'declined', 'revoked', 'expired')),
  invited_by TEXT NOT NULL REFERENCES users(id),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX ws_invitations_email ON ws_invitations(email, status);

CREATE TABLE ws_resources (                         -- ResourcePermission: one row per grant, never deleted; revoking sets revoked_at
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES ws_workspaces(id),
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  resource_owner_id TEXT NOT NULL REFERENCES users(id),
  permission TEXT NOT NULL CHECK (permission IN ('view', 'edit')),
  shared_by TEXT NOT NULL REFERENCES users(id),
  expires_at TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT
);
CREATE INDEX ws_resources_lookup ON ws_resources(resource_type, resource_id, revoked_at);
CREATE INDEX ws_resources_ws ON ws_resources(workspace_id, revoked_at);

CREATE TABLE ws_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id TEXT NOT NULL REFERENCES ws_workspaces(id),
  actor_user_id TEXT,
  event TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ws_events_ws ON ws_events(workspace_id, id);

-- ================================================================== Scheduling and delivery
CREATE TABLE sch_schedules (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  report_id TEXT NOT NULL REFERENCES rep_reports(id),
  pinned_version INTEGER,                          -- snapshot mode: the version that is always sent
  mode TEXT NOT NULL CHECK (mode IN ('snapshot', 'refresh')),
  frequency TEXT NOT NULL CHECK (frequency IN ('once', 'daily', 'weekly', 'monthly')),
  timezone TEXT NOT NULL,
  local_time TEXT NOT NULL,                        -- HH:MM in the schedule's timezone
  weekday INTEGER,
  day_of_month INTEGER,
  export_format TEXT NOT NULL,
  recipients TEXT NOT NULL,                        -- [{"kind": "email"|"slack"|"local", "target": ...}]
  status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'ended', 'blocked')),
  next_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX sch_due ON sch_schedules(status, next_run_at);

CREATE TABLE sch_runs (
  id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL REFERENCES sch_schedules(id),
  scheduled_for TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  report_version INTEGER,
  export_id TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('pending', 'ok', 'partial', 'failed', 'blocked')),
  reason TEXT,
  initiated_by TEXT,
  idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE sch_deliveries (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sch_runs(id),
  provider TEXT NOT NULL,
  recipient TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('pending', 'sent', 'failed', 'dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  next_attempt_at TEXT,
  created_at TEXT NOT NULL,
  last_attempt_at TEXT
);

-- ================================================================== Owner operations
CREATE TABLE own_maintenance (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  active INTEGER NOT NULL DEFAULT 0,
  notice TEXT,
  changed_by TEXT,
  changed_at TEXT
);
INSERT INTO own_maintenance (id, active) VALUES (1, 0);

CREATE TABLE own_events (                           -- Phase 7 owner actions with actor, action, target, time, outcome (also mirrored to audit_events)
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  outcome TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE own_quota_policy (                     -- owner overrides of per-account quotas; absent = the configured default
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
