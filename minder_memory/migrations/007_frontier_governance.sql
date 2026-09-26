-- Frontier consult governance (docs/minder-phase-4-7-frontier-coding.md P4.1)
-- One governed eval row per frontier trace. Keyed by trace_id from
-- frontier_traces (003); that table's own helpfulness column (INTEGER,
-- legacy PR 7 semantics) is deliberately left alone. Raw prompt/response
-- text has no column here or anywhere — hashes live on 003.
CREATE TABLE frontier_evals (
  trace_id TEXT PRIMARY KEY,
  consult_trigger TEXT,          -- what asked for the consult (e.g. warden L2)
  helpfulness TEXT,              -- null|helpful|partial|harmful|inconclusive
  verification_status TEXT,      -- null|pass|fail|not_run
  distilled_json TEXT,           -- candidate actions only, redacted
  accepted_actions_json TEXT,
  rejected_actions_json TEXT,
  classified_at TEXT
);
CREATE INDEX idx_frontier_evals_class
  ON frontier_evals(helpfulness, verification_status);
