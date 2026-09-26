-- Candidate skill proposals (docs/minder-phase-2-3-frontier-coding.md
-- P2.3). Recurring verified lessons may PROPOSE a skill; acceptance is an
-- operator act. Nothing here installs a default skill or writes SKILLS.md.

CREATE TABLE skill_candidates (
  candidate_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo TEXT,
  failure_family TEXT,
  source_lesson_ids TEXT NOT NULL,   -- json list
  episode_count INTEGER NOT NULL,
  instructions TEXT NOT NULL,
  anti_pattern TEXT,
  verification_json TEXT,
  status TEXT NOT NULL,              -- proposed | accepted | rejected | needs_eval
  created_at TEXT NOT NULL
);

CREATE INDEX idx_skill_candidates_lookup
  ON skill_candidates(repo, failure_family, status);
