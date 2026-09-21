-- Skill-gap records (docs/prd-memory-v1.md PR 6). A gap says "the agent
-- kept failing here and no known skill matches" — queryable evidence for a
-- later human/agent to author a skill. SKILLS.md is NEVER written.

CREATE TABLE skill_gaps (
  gap_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  repo TEXT,
  failure_key TEXT,
  gap_type TEXT NOT NULL,
  sample_error TEXT,
  status TEXT NOT NULL DEFAULT 'open'
);

CREATE INDEX idx_skill_gaps_lookup ON skill_gaps(repo, failure_key);
