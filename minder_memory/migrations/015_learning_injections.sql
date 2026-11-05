-- Injection ledger (issue #13). minder injects a verified lesson into the
-- Warden digest at two places in minder_memory/policy.py, and until now
-- nothing recorded that it happened. Two consequences, both measured on
-- the live store: the operator queue cannot show that a lesson it kept
-- producing keeps firing, and no "did minder help" claim is checkable,
-- because the denominator (what was injected, to which session, under
-- which trigger) does not exist.
--
-- One row per injection decision, including the ones that injected
-- nothing. A ledger of successes alone cannot answer "how often did we
-- have a lesson and did not use it", which is the question retrieval
-- tuning is actually about.
--
-- Append-only like every other evidence table: a later verdict about an
-- injection is new evidence, never an edit of this row.

CREATE TABLE learning_injections (
  injection_id     TEXT PRIMARY KEY,
  ts               TEXT NOT NULL,
  session_id       TEXT,                 -- hook session/task key
  event_id         TEXT,                 -- the failure event that asked
  failure_key      TEXT,
  repo             TEXT,
  lesson_id        TEXT,                 -- NULL when nothing was injected
  tier             TEXT,                 -- exact | family | n/a
  trigger_matched  INTEGER,              -- NULL until #15 fills it
  digest_injected  INTEGER NOT NULL,     -- 1 = text reached the digest
  chars_injected   INTEGER NOT NULL DEFAULT 0,
  assist_mode      TEXT NOT NULL,        -- retrieve | block_duplicate | ...
  redaction_status TEXT NOT NULL DEFAULT 'redacted'
);
CREATE TRIGGER learning_injections_no_update
BEFORE UPDATE ON learning_injections
BEGIN SELECT RAISE(ABORT, 'learning_injections is append-only'); END;
CREATE TRIGGER learning_injections_no_delete
BEFORE DELETE ON learning_injections
BEGIN SELECT RAISE(ABORT, 'learning_injections is append-only'); END;
CREATE INDEX idx_learning_injections_lesson
  ON learning_injections (lesson_id, ts);
CREATE INDEX idx_learning_injections_failure
  ON learning_injections (failure_key, ts);
