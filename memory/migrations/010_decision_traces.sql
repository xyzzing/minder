-- Decision traces (docs/minder-phases-4-7-with-5.5-6.5.md Phase 5.5, slice D)
-- Log-only record of the assess loop: what the model proposed and what
-- the Python gate decided, side by side. NOTHING in policy reads this
-- table to make decisions; it exists for shadow comparison and replay.
CREATE TABLE decision_traces (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  contract_id TEXT,
  contract_version TEXT,
  session_id TEXT,
  failure_key TEXT,
  state_hash TEXT,
  menu_json TEXT,
  model_recommendation TEXT,
  policy_decision TEXT,
  override TEXT,
  fallback TEXT,
  confidence REAL,
  failure_kind TEXT,
  needs_new_evidence REAL,
  provider TEXT,
  model_version TEXT,
  latency_ms REAL
);
CREATE INDEX idx_decision_traces_key ON decision_traces(failure_key, ts);
CREATE INDEX idx_decision_traces_contract ON decision_traces(contract_id, contract_version);
