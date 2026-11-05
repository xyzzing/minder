-- The work-order contract at intake (FR-O1, issue #84). The quality plan
-- and the reward definition of the v0.11 amendment: one row per declared
-- work order, carrying task, acceptance_criterion and room_family under
-- windtunnel.work-order/v1 (shape owned by windtunnel, FR-W1; minder owns
-- the runtime). task_id is the stable key the console and CLI address;
-- revision counts re-declarations of the same task. Criterion and family
-- are immutable while a work order is open: a live contract is never
-- silently rewritten (SCHEMAS-room.md section 12, id-stability rule).
-- Attempt grading is a later slice; nothing here grades, only records.
--
-- A sibling of task_contexts, not new columns: task_contexts'
-- contract_version already means domain-route/v1 and its lifecycle is
-- domain switching, not contract holding. The two concerns share the
-- task_id key and nothing else.
--
-- Append-only like every other evidence table: closing pins a row, it
-- never edits one.

CREATE TABLE IF NOT EXISTS work_orders (
    wo_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    task TEXT NOT NULL,
    acceptance_criterion TEXT NOT NULL,
    room_family TEXT NOT NULL,
    kind TEXT,
    contract_version TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'operator',
    declared_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_work_orders_task
    ON work_orders (task_id, declared_at DESC);
