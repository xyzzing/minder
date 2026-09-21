-- Memory v1 initial schema (docs/prd-memory-v1.md PR 2).
-- Beside the JSONL audit ledger, not a replacement: events.jsonl keeps its
-- role; this store adds typed episodes and lessons.

CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  session_id TEXT,
  task_id TEXT,
  repo TEXT,
  repo_version TEXT,
  tool TEXT,
  failure_key TEXT,
  action_fingerprint TEXT,
  payload_json TEXT NOT NULL,
  redaction_status TEXT NOT NULL
);

-- Evidence is append-only: corrections happen as new events.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only');
END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only');
END;

CREATE TABLE episodes (
  episode_id TEXT PRIMARY KEY,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  repo TEXT,
  task_id TEXT,
  status TEXT NOT NULL
);

CREATE TABLE episode_events (
  episode_id TEXT,
  event_id TEXT,
  seq INTEGER
);

CREATE TABLE lessons (
  lesson_id TEXT PRIMARY KEY,
  repo TEXT,
  failure_key TEXT,
  instruction TEXT NOT NULL,
  anti_pattern TEXT,
  verification_json TEXT,
  status TEXT NOT NULL,
  source_episode TEXT,
  valid_from TEXT,
  valid_to TEXT,
  expires_when TEXT
);

CREATE INDEX idx_events_failure_key
  ON events(failure_key, ts);
CREATE INDEX idx_events_fingerprint
  ON events(failure_key, action_fingerprint);
CREATE INDEX idx_episode_events_episode
  ON episode_events(episode_id, seq);
CREATE INDEX idx_lessons_lookup
  ON lessons(repo, failure_key, status);
