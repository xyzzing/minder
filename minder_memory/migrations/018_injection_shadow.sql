-- Shadow prompt-injection guard ledger (issue #65). One row per proxied
-- request the guard worker classified in shadow mode. Log-only like
-- classifier_shadow: nothing in policy reads this table to make
-- decisions; it exists so the shadow period has a denominator and the
-- eventual enforcement decision has evidence.
CREATE TABLE injection_shadow (
  id               TEXT PRIMARY KEY,
  ts               TEXT NOT NULL,
  session_id       TEXT,          -- the proxy's session fingerprint
  verdict          TEXT,          -- safe | injection
  score            REAL,          -- untrusted-class probability
  latency_ms       REAL,          -- client-measured end-to-end opinion time
  model            TEXT,          -- classifier identity (stub | prompt-guard-2-86m)
  chars_classified INTEGER        -- size of the redacted text the model saw
);
CREATE TRIGGER injection_shadow_no_update
BEFORE UPDATE ON injection_shadow
BEGIN SELECT RAISE(ABORT, 'injection_shadow is append-only'); END;
CREATE TRIGGER injection_shadow_no_delete
BEFORE DELETE ON injection_shadow
BEGIN SELECT RAISE(ABORT, 'injection_shadow is append-only'); END;
CREATE INDEX idx_injection_shadow_ts ON injection_shadow(ts);
