-- Proactive dispatch intake (issue #89, stage 1). One row per trigger
-- the poller saw: what arrived, what the intake gate said, and why, in
-- stable codes the CLI and console render. Append-only like every
-- evidence table: a trigger seen is a fact about the past. work_order_id
-- stays NULL here forever: stage 2 records the binding as its own
-- fact (a run row), never an update of this one.

CREATE TABLE dispatch_triggers (
  trigger_id    TEXT PRIMARY KEY,   -- source:notification_id:updated_at
  ts            TEXT NOT NULL,
  source        TEXT NOT NULL,      -- github
  repo          TEXT NOT NULL,
  kind          TEXT NOT NULL,      -- issue | issue_comment | pull_request | other
  sender        TEXT,               -- author login; NULL when the fetch failed
  sender_class  TEXT NOT NULL,      -- listed | bot | unlisted | unknown
  decision      TEXT NOT NULL,      -- admitted | would_accept | filtered
  reason        TEXT,               -- stable code; NULL unless filtered
  work_order_id TEXT
);
CREATE TRIGGER dispatch_triggers_no_update
BEFORE UPDATE ON dispatch_triggers
BEGIN SELECT RAISE(ABORT, 'dispatch_triggers is append-only'); END;
CREATE TRIGGER dispatch_triggers_no_delete
BEFORE DELETE ON dispatch_triggers
BEGIN SELECT RAISE(ABORT, 'dispatch_triggers is append-only'); END;
CREATE INDEX idx_dispatch_triggers_decision ON dispatch_triggers (decision, ts);
CREATE INDEX idx_dispatch_triggers_ts ON dispatch_triggers (ts);
