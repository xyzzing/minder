-- Offline training export (docs/minder-phase-4-7-frontier-coding.md P7.1)
-- A curated dataset for OFFLINE research only — nothing here is ever
-- deployed; no adapter is loaded by minder (see docs/lora-offline.md).
CREATE TABLE training_candidates (
  candidate_id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL,
  lesson_id TEXT,
  failure_family TEXT,
  trajectory_json TEXT NOT NULL,  -- redacted compact steps
  split TEXT NOT NULL,            -- train|held_out
  created_at TEXT NOT NULL
);
CREATE INDEX idx_training_candidates_family
  ON training_candidates(failure_family, split);
