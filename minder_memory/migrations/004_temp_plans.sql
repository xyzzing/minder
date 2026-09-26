-- Temporary plans (docs/minder-phase-2-3-frontier-coding.md P2.2).
-- When no skill matches a repeated failure, minder drafts a short
-- deterministic procedure. A temp plan is NOT a skill and never becomes
-- one automatically; SKILLS.md is never written.

CREATE TABLE temp_plans (
  plan_id TEXT PRIMARY KEY,
  episode_id TEXT,
  repo TEXT,
  failure_key TEXT,
  gap_type TEXT NOT NULL,
  steps_json TEXT NOT NULL,
  status TEXT NOT NULL,          -- open | executed | verified | abandoned
  created_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE INDEX idx_temp_plans_lookup ON temp_plans(repo, failure_key, status);
