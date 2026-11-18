-- Reviews of a person's own work. A review outlives the work and the account: when the account is deleted, the link to the person is cut (author_user_id NULL), contact details and
-- the person's name are blanked out of the text, and the review is labelled with a random "deleted account" code. Additive.
CREATE TABLE reviews (
  id TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL CHECK (length(target_kind) BETWEEN 1 AND 40),
  target_id TEXT NOT NULL CHECK (length(target_id) BETWEEN 1 AND 100),
  target_title TEXT NOT NULL CHECK (length(target_title) BETWEEN 1 AND 200),
  author_user_id TEXT REFERENCES users(id),
  anon_label TEXT,
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment TEXT CHECK (comment IS NULL OR length(comment) <= 2000),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  anonymized_at TEXT,
  CHECK ((author_user_id IS NULL) = (anonymized_at IS NOT NULL))
);
CREATE UNIQUE INDEX reviews_one_per_author ON reviews (author_user_id, target_kind, target_id) WHERE author_user_id IS NOT NULL;
CREATE INDEX reviews_created ON reviews (created_at);
CREATE INDEX reviews_kind ON reviews (target_kind, created_at);
