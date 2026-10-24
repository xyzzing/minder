-- Issue #42: label lessons with their producer profile and model-specific
-- applicability (PRD §12A, mirrored from repo-agent's memory plane).
--
-- Backfill is honest: existing rows are 'unrecorded' (unknown producer)
-- and portable by default (0) — single-model deployments see no change.
ALTER TABLE lessons ADD COLUMN producer_profile TEXT NOT NULL
  DEFAULT 'unrecorded';
ALTER TABLE lessons ADD COLUMN model_specific INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_lessons_producer ON lessons(producer_profile, model_specific);
