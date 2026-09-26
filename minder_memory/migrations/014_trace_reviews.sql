-- Trace Review (docs/trace-review/prd-trace-review.md): offline post-run
-- evaluation of completed DSH sessions, plus the structured human feedback
-- that turns a finding into a confirmed failure and then a regression case.
--
-- Both tables are append-only, like every other evidence table here: a
-- review is what an evaluator version said about a trace at a time, and
-- feedback is what a human said about it. Corrections append evidence;
-- history is never rewritten.
--
-- Findings live inside `findings_json` rather than in their own table. The
-- MVP is one row per review run, and a per-finding table would add a join
-- and a migration for no query the CLI or the console actually makes; the
-- split is a documented later option, not an oversight.

CREATE TABLE trace_reviews (
  review_id        TEXT PRIMARY KEY,
  run_id           TEXT NOT NULL,          -- mndr_run_<sha1(session)[:12]>
  session_id       TEXT NOT NULL,          -- DSH session id (lineage)
  ts               TEXT NOT NULL,
  status           TEXT NOT NULL,          -- 'ok' | 'degraded:<Exc>'
  evaluator_version TEXT NOT NULL DEFAULT '',
  rubric_id        TEXT,                   -- versioned rubric, when declared
  policy_version   TEXT,                   -- policy in force during the run
  summary_json     TEXT NOT NULL DEFAULT '{}',
  findings_json    TEXT NOT NULL DEFAULT '[]',
  redaction_status TEXT NOT NULL DEFAULT 'redacted'
);
CREATE TRIGGER trace_reviews_no_update BEFORE UPDATE ON trace_reviews
BEGIN SELECT RAISE(ABORT, 'trace_reviews is append-only'); END;
CREATE TRIGGER trace_reviews_no_delete BEFORE DELETE ON trace_reviews
BEGIN SELECT RAISE(ABORT, 'trace_reviews is append-only'); END;
CREATE INDEX idx_trace_reviews_session ON trace_reviews (session_id, ts);
CREATE INDEX idx_trace_reviews_run ON trace_reviews (run_id, ts);

CREATE TABLE trace_feedback (
  feedback_id      TEXT PRIMARY KEY,
  review_id        TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  ts               TEXT NOT NULL,
  level            TEXT NOT NULL,          -- run | event | claim
  category         TEXT NOT NULL,          -- closed taxonomy (see below)
  target_ref       TEXT,                   -- ds_seq / claim id / finding id
  finding_id       TEXT,                   -- set when judging a finding
  finding_verdict  TEXT,                   -- confirm | reject | NULL
  comment          TEXT,                   -- redacted, length-capped
  reviewer         TEXT,
  redaction_status TEXT NOT NULL DEFAULT 'redacted'
);
CREATE TRIGGER trace_feedback_no_update BEFORE UPDATE ON trace_feedback
BEGIN SELECT RAISE(ABORT, 'trace_feedback is append-only'); END;
CREATE TRIGGER trace_feedback_no_delete BEFORE DELETE ON trace_feedback
BEGIN SELECT RAISE(ABORT, 'trace_feedback is append-only'); END;
CREATE INDEX idx_trace_feedback_review ON trace_feedback (review_id, ts);
CREATE INDEX idx_trace_feedback_finding ON trace_feedback (finding_id, ts);
