-- Phase 10B: the PredictAI Brain (request caps for a free-tier provider) and Exam Prediction.
-- Additive, plus one CHECK widening done the only way SQLite allows (copy, drop, rename; the same pattern as migration 10): exam_assessment_questions.origin gains 'ai_written'.
-- Existing rows are copied unchanged.

-- ---------------------------------------------------------------- Brain: request-based limits (a free tier is limited in requests, not tokens)
ALTER TABLE ai_providers ADD COLUMN daily_request_limit INTEGER;     -- most requests per quota day; NULL = no request cap
ALTER TABLE ai_providers ADD COLUMN requests_per_minute INTEGER;     -- most requests in any 60 seconds; NULL = no cap
ALTER TABLE ai_providers ADD COLUMN quota_tz TEXT;                   -- IANA zone in which the provider's quota day resets (Gemini: America/Los_Angeles); NULL = UTC

-- ---------------------------------------------------------------- Exam Prediction
CREATE TABLE exam_predictions (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES exam_subjects(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  params TEXT NOT NULL,
  result TEXT NOT NULL,                       -- JSON: ranked chapters and topics with every component, the backtest, the web evidence with its sources, the limitations
  created_at TEXT NOT NULL
);
CREATE INDEX exam_predictions_subject ON exam_predictions (subject_id, created_at);

ALTER TABLE exam_assessments ADD COLUMN prediction_id TEXT;          -- set when a paper was built from a prediction (no foreign key: deleting a prediction must not be blocked)

CREATE TABLE exam_assessment_questions_new (
  id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES exam_assessments(id),
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  qtype TEXT NOT NULL,
  marks REAL NOT NULL,
  difficulty TEXT NOT NULL,
  options TEXT,
  answer TEXT,
  marking TEXT,
  chapter TEXT,
  topic TEXT,
  origin TEXT NOT NULL CHECK (origin IN ('past_paper', 'source_derived', 'template_generated', 'user_edited', 'ai_written')),
  source_question_id TEXT,
  source_ref TEXT,
  included INTEGER NOT NULL DEFAULT 1,
  edited INTEGER NOT NULL DEFAULT 0
);
INSERT INTO exam_assessment_questions_new (id, assessment_id, position, text, qtype, marks, difficulty, options, answer, marking, chapter, topic, origin, source_question_id, source_ref, included, edited)
  SELECT id, assessment_id, position, text, qtype, marks, difficulty, options, answer, marking, chapter, topic, origin, source_question_id, source_ref, included, edited FROM exam_assessment_questions;
DROP TABLE exam_assessment_questions;
ALTER TABLE exam_assessment_questions_new RENAME TO exam_assessment_questions;
CREATE INDEX exam_aq ON exam_assessment_questions(assessment_id, position);
