-- Success-loop guard (docs/success-loop-guard-design.md; Phase 2+ backlog
-- item after the live DBS incident: an agent repeated an identical
-- SUCCESSFUL curl 20+ times — minder's duplicate guard governs failure
-- loops only). Observations are append-only; the advisory column is set
-- at insert time (1 = this repeat raised the advisory), never updated.

CREATE TABLE success_observations (
  obs_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  action_fingerprint TEXT NOT NULL,
  result_signature TEXT NOT NULL,   -- sha256(exit + normalized output[:512])
  exit_code INTEGER,
  excerpt TEXT,                     -- redacted, <=160 chars
  advisory INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER success_observations_no_update BEFORE UPDATE ON success_observations
BEGIN SELECT RAISE(ABORT, 'success_observations is append-only'); END;
CREATE TRIGGER success_observations_no_delete BEFORE DELETE ON success_observations
BEGIN SELECT RAISE(ABORT, 'success_observations is append-only'); END;
CREATE INDEX idx_success_obs ON success_observations
  (session_id, action_fingerprint, result_signature, ts);
