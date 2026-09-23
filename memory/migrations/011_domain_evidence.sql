-- Domain-routed evidence governance (docs/minder-phase0-1-plan.md, PRD v2).
-- Phase 1 evidence schema: task contexts, transitions, boundary signals,
-- human input events, routing shadow traces, and the trading research
-- registry. Kept separate from graph 006 (which these may project into);
-- append-only like events wherever the PRD requires immutable history.

-- Explicit task context: one open row per task; closing sets closed_at
-- (episodes-style); switching closes the old row and opens a new one.
CREATE TABLE task_contexts (
  context_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'operator',
  domain TEXT NOT NULL,          -- coding|trading_research|resume_application|cited_research|mixed
  subtask TEXT,
  subtask_seq INTEGER NOT NULL DEFAULT 1,
  origin TEXT NOT NULL,          -- explicit_user|harness|router_validated|restricted
  contract_version TEXT NOT NULL DEFAULT 'domain-route/v1',
  egress_class TEXT,
  required_verifier TEXT,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  close_reason TEXT,
  note TEXT
);
CREATE INDEX idx_task_contexts_task ON task_contexts(task_id, opened_at);

-- Auditable switch lifecycle: never edited or deleted after write.
CREATE TABLE domain_transitions (
  transition_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  from_context_id TEXT,
  to_context_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  trigger TEXT NOT NULL,         -- explicit_declare|clarification|router_proposed|...
  provenance TEXT NOT NULL,      -- declared|harness|router_proposed
  router_provider TEXT,
  router_model_version TEXT,
  validation_result TEXT NOT NULL,  -- approved|rejected|restricted
  reason TEXT,
  data_access_consequences TEXT
);
CREATE TRIGGER domain_transitions_no_update BEFORE UPDATE ON domain_transitions
BEGIN SELECT RAISE(ABORT, 'domain_transitions is append-only'); END;
CREATE TRIGGER domain_transitions_no_delete BEFORE DELETE ON domain_transitions
BEGIN SELECT RAISE(ABORT, 'domain_transitions is append-only'); END;

-- Passive boundary-signal journal (ambiguity-rate measurement, P0.1).
CREATE TABLE boundary_signals (
  signal_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,            -- explicit_declare|harness_metadata|replay_inferred
  declared_domain TEXT,
  actor TEXT,
  session_id TEXT,
  note TEXT
);
CREATE TRIGGER boundary_signals_no_update BEFORE UPDATE ON boundary_signals
BEGIN SELECT RAISE(ABORT, 'boundary_signals is append-only'); END;
CREATE TRIGGER boundary_signals_no_delete BEFORE DELETE ON boundary_signals
BEGIN SELECT RAISE(ABORT, 'boundary_signals is append-only'); END;

-- The first captured human evidence events: a consultation with an exact
-- question, authority, and decision scope (hashes/redacted text only).
CREATE TABLE human_input_events (
  event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  authority TEXT NOT NULL,       -- user|operator|reviewer
  kind TEXT NOT NULL,            -- clarify_request|factual_correction|wording_approval|intent_choice|holdout_unlock|permission|review_decision
  question TEXT,
  answer_summary TEXT,
  affects_json TEXT,             -- [{type, id, version}]
  decision TEXT,                 -- approved|rejected|deferred
  method TEXT,
  note TEXT
);
CREATE TRIGGER human_input_events_no_update BEFORE UPDATE ON human_input_events
BEGIN SELECT RAISE(ABORT, 'human_input_events is append-only'); END;
CREATE TRIGGER human_input_events_no_delete BEFORE DELETE ON human_input_events
BEGIN SELECT RAISE(ABORT, 'human_input_events is append-only'); END;

-- Routing shadow traces: same discipline as decision_traces (log-only;
-- policy never reads this to decide). Provenance separates declared from
-- router-proposed; validation records what deterministic Minder decided.
CREATE TABLE route_traces (
  trace_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  contract_id TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  session_id TEXT,
  task_id TEXT,
  declared_domain TEXT,
  candidate_domain TEXT,         -- coding|trading_research|resume_application|cited_research|mixed|unknown
  transition TEXT,               -- stay|new_subtask|switch|uncertain
  intent_kind TEXT,              -- advisory only
  confidence REAL,
  abstained INTEGER NOT NULL DEFAULT 0,
  abstain_reason TEXT,
  provider TEXT,
  model_version TEXT,
  provenance TEXT NOT NULL,      -- declared|router_proposed
  validation_result TEXT NOT NULL,  -- approved|rejected|restricted|shadow
  latency_ms REAL
);
CREATE INDEX idx_route_traces_key ON route_traces(session_id, ts);

-- Trading research registry (PRD v2 §Trading research protocol).
-- Minder enforces the protocol; it never computes performance statistics
-- and never certifies profitability.
CREATE TABLE hypothesis_families (
  family_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  registered_at TEXT NOT NULL,
  method_digest TEXT NOT NULL,
  method_note TEXT,
  universe TEXT,
  data_sources_json TEXT NOT NULL,
  planned_metric TEXT NOT NULL,
  split_scheme TEXT NOT NULL,
  expected_trials INTEGER,       -- declared manifest size; NULL = open-ended
  status TEXT NOT NULL DEFAULT 'registered',  -- registered|superseded
  superseded_by TEXT,
  PRIMARY KEY (family_id, version)
);

CREATE TABLE trial_records (
  trial_id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  dataset_vintage TEXT NOT NULL,    -- json {digest, as_of}
  split_assignment TEXT NOT NULL,   -- train|validation|holdout
  result_summary_json TEXT NOT NULL,  -- redacted metric summaries, no raw series
  flagged TEXT                      -- NULL|holdout_violation|vintage_mix|...
);
CREATE TRIGGER trial_records_no_update BEFORE UPDATE ON trial_records
BEGIN SELECT RAISE(ABORT, 'trial_records is append-only'); END;
CREATE TRIGGER trial_records_no_delete BEFORE DELETE ON trial_records
BEGIN SELECT RAISE(ABORT, 'trial_records is append-only'); END;
CREATE INDEX idx_trials_family ON trial_records(family_id, recorded_at);

-- Vintage-change journal: staleness triage without erasing history.
CREATE TABLE vintage_events (
  event_id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  old_digest TEXT,
  new_digest TEXT NOT NULL,
  reason TEXT,
  actor TEXT
);
CREATE TRIGGER vintage_events_no_update BEFORE UPDATE ON vintage_events
BEGIN SELECT RAISE(ABORT, 'vintage_events is append-only'); END;
CREATE TRIGGER vintage_events_no_delete BEFORE DELETE ON vintage_events
BEGIN SELECT RAISE(ABORT, 'vintage_events is append-only'); END;

-- Researcher-tooling analysis artifacts (PBO/DSR/PSR/SPA outputs): stored
-- and cross-checked against the manifest; always start 'candidate' and
-- have no path to 'verified' without a recorded human review decision.
CREATE TABLE analysis_artifacts (
  analysis_id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  produced_at TEXT NOT NULL,
  method TEXT NOT NULL,           -- PBO/CSCV|DSR|PSR|SPA|other
  code_digest TEXT NOT NULL,
  n_trials_referenced INTEGER,
  trial_sharpe_variance REAL,
  verdict_json TEXT,
  flagged TEXT,                   -- NULL|n_inconsistent|...
  status TEXT NOT NULL DEFAULT 'candidate'
);
