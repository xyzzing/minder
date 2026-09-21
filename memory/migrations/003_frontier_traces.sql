-- Frontier consult traces (docs/prd-memory-v1.md PR 7). Hashed metadata
-- per consult so helpfulness can be joined later; raw prompts stay out.

CREATE TABLE frontier_traces (
  trace_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  episode_id TEXT,
  failure_key TEXT,
  local_attempts INTEGER,
  redaction_profile TEXT,
  provider_fingerprint TEXT,
  request_hash TEXT,
  response_hash TEXT,
  helpfulness INTEGER,
  verification_status TEXT
);

CREATE INDEX idx_frontier_traces_key ON frontier_traces(failure_key, ts);
