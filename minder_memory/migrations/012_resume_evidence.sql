-- Résumé evidence (Phase 1 résumé slice; PRD v2 §Résumé fact, wording,
-- and intent — retention decision accepted by the owner 2026-09-23).
-- Three linked, never-collapsed objects: what happened (career
-- assertions), which phrasings are accurate (approved wordings), and how
-- accurate experience is presented to one pinned JD (application
-- intents). Draft usages tie drafts to assertions so a factual
-- correction can reach exactly the drafts that rely on it.
-- PII discipline: short phrases and digests only — never whole CVs or
-- full JD texts.

CREATE TABLE career_assertions (
  assertion_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  subject_digest TEXT NOT NULL,     -- stable digest of the experience item
  claim_text TEXT NOT NULL,         -- canonical first-party assertion (short)
  wording_variants_json TEXT NOT NULL,
  support TEXT NOT NULL DEFAULT 'first_party',  -- none|first_party|corroborated
  status TEXT NOT NULL DEFAULT 'active',        -- active|challenged|superseded
  superseded_by TEXT,
  uncertain INTEGER NOT NULL DEFAULT 0,
  actor TEXT NOT NULL
);
CREATE INDEX idx_assertions_subject ON career_assertions(subject_digest);

CREATE TABLE approved_wordings (
  wording_id TEXT PRIMARY KEY,
  assertion_id TEXT NOT NULL,
  phrase TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'any',   -- 'any' | <jd_id>
  status TEXT NOT NULL DEFAULT 'approved',  -- approved|review|withdrawn
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  withdrawn_at TEXT,
  withdraw_reason TEXT
);
CREATE INDEX idx_wordings_assertion ON approved_wordings(assertion_id);

-- Retention (owner decision 4): an intent is JD-scoped and expires with
-- the application cycle — default 90 days, operator-configurable; the
-- expiry is explicitly recorded at creation (expires_at) and enforced
-- mechanically (status -> expired, row kept).
CREATE TABLE application_intents (
  intent_id TEXT PRIMARY KEY,
  jd_id TEXT NOT NULL,
  jd_digest TEXT NOT NULL,
  assertion_id TEXT NOT NULL,
  chosen_variant TEXT NOT NULL,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  expired_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'  -- active|expired|review|superseded
);
CREATE INDEX idx_intents_assertion ON application_intents(assertion_id);
CREATE INDEX idx_intents_jd ON application_intents(jd_id);

-- Which versions appear in which draft; append-only + flag column so a
-- factual correction can flag dependents without erasing anything.
CREATE TABLE draft_usages (
  usage_id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL,
  jd_id TEXT,
  assertion_id TEXT NOT NULL,
  phrase TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  flagged TEXT                    -- NULL|assertion_superseded|unapproved_phrase|...
);
CREATE TRIGGER draft_usages_no_update BEFORE UPDATE ON draft_usages
BEGIN SELECT RAISE(ABORT, 'draft_usages is append-only'); END;
CREATE TRIGGER draft_usages_no_delete BEFORE DELETE ON draft_usages
BEGIN SELECT RAISE(ABORT, 'draft_usages is append-only'); END;
CREATE INDEX idx_draft_usages_assertion ON draft_usages(assertion_id);

-- Corrections annotate; they never rewrite. A factual correction
-- appends one flag row per dependent draft usage (and drops intents to
-- 'review' via their mutable status), leaving all evidence intact.
CREATE TABLE evidence_flags (
  flag_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  target_type TEXT NOT NULL,      -- draft_usage|application_intent|...
  target_id TEXT NOT NULL,
  flag TEXT NOT NULL,             -- assertion_superseded|unapproved_phrase|...
  reason TEXT,
  actor TEXT
);
CREATE TRIGGER evidence_flags_no_update BEFORE UPDATE ON evidence_flags
BEGIN SELECT RAISE(ABORT, 'evidence_flags is append-only'); END;
CREATE TRIGGER evidence_flags_no_delete BEFORE DELETE ON evidence_flags
BEGIN SELECT RAISE(ABORT, 'evidence_flags is append-only'); END;
CREATE INDEX idx_evidence_flags_target ON evidence_flags(target_type, target_id);
