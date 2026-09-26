-- Classifier shadow log (docs/minder-phase-4-7-frontier-coding.md P5.1)
-- Log-only labels from an optional CPU classifier. Nothing in policy
-- reads this table to make decisions; it exists for offline comparison.
CREATE TABLE classifier_shadow (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  event_id TEXT,
  failure_key TEXT,
  failure_class TEXT,        -- code_logic|schema_contract|environment|permissions|test_expectation|unknown
  recommended_action TEXT,   -- inspect|retrieve_memory|think_retry|environment_check|block_duplicate|escalate_candidate
  egress_risk TEXT,          -- safe|uncertain|block
  confidence REAL,
  model_version TEXT,
  policy_action TEXT         -- what Minder actually did on this event
);
CREATE INDEX idx_classifier_shadow_key ON classifier_shadow(failure_key, ts);
