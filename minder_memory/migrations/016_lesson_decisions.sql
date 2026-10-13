-- Lesson decision taxonomy (issue #14). The operator queue recorded that
-- a decision happened; the reason was free text, so nothing about the
-- queue's history could be counted or trended. Closed vocabularies live
-- in minder_memory/lesson_decisions.py (C4); this table stores which one
-- an operator chose.
--
-- Append-only like every other evidence table: a decision is what the
-- operator said at a time. A later change of mind appends another row.

CREATE TABLE lesson_decisions (
  decision_id      TEXT PRIMARY KEY,
  ts               TEXT NOT NULL,
  lesson_id        TEXT NOT NULL,
  action           TEXT NOT NULL,          -- invalidate | reject | adopt
  code             TEXT NOT NULL,          -- closed list, per action
  note             TEXT,                   -- the free-text note, capped
  actor            TEXT NOT NULL,
  redaction_status TEXT NOT NULL DEFAULT 'redacted'
);
CREATE TRIGGER lesson_decisions_no_update
BEFORE UPDATE ON lesson_decisions
BEGIN SELECT RAISE(ABORT, 'lesson_decisions is append-only'); END;
CREATE TRIGGER lesson_decisions_no_delete
BEFORE DELETE ON lesson_decisions
BEGIN SELECT RAISE(ABORT, 'lesson_decisions is append-only'); END;
CREATE INDEX idx_lesson_decisions_lesson ON lesson_decisions (lesson_id, ts);
CREATE INDEX idx_lesson_decisions_code ON lesson_decisions (action, code);

-- The diagnosis of the decision that ended a lesson, denormalised onto
-- the lesson so the list page needs no join. The full history, including
-- the note, stays in lesson_decisions.
ALTER TABLE lessons ADD COLUMN invalidated_diagnosis TEXT;
