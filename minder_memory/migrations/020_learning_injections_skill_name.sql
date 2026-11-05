-- learning_injections.skill_name (issue #13 acceptance 3, repaired by the
-- mews M0 diagnosis of issue #80): a skill-section injection records the
-- skill it injected, so the ledger distinguishes a lesson assist from a
-- skill assist. digest_injected, not lesson_id, marks whether anything
-- reached the digest: a skill row has no lesson_id but did inject.
--
-- Why this migration exists: the column was originally added by editing
-- 015 in place, under the assumption that no store had shipped at
-- version 15. That assumption was false - the live store predates the
-- edit - so every such store permanently lacked the column and
-- `minder-op migrate` could never repair it (migrate applies only
-- num > version). 015 returns to its shipped shape and the column moves
-- here, so fresh stores and drifted stores converge. 019 is reserved by
-- the work-order runtime (issue #84, PR #85); the numbering gap is safe:
-- migrate applies every file above the store's version.
--
-- Append-only table: an ADD COLUMN is schema, not evidence; existing
-- rows read NULL, which is exactly what a pre-skill ledger means.

ALTER TABLE learning_injections ADD COLUMN skill_name TEXT;
CREATE INDEX idx_learning_injections_skill
  ON learning_injections (skill_name, ts);
