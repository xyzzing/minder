-- Stated preferences as lesson evidence (issue #22).
--
-- `human_input_events` (011) already carries actor, authority, kind,
-- question, answer_summary, affects_json, decision, method, note, with
-- the append-only triggers. The only thing it lacked was a kind for a
-- durable working preference ("always run the tests through make") and
-- for an ideal answer supplied for future work (Reflexio's
-- expert_content input). That vocabulary is closed in Python, not in the
-- database (`task_context.HUMAN_KINDS`), so no schema change is needed
-- for acceptance 1.
--
-- What is missing is the reverse link: a lesson promoted from such an
-- event has no episode, and `lessons.source_episode` is the only source
-- column the store has. The evidence has to name the `human_input_events`
-- row it came from (acceptance 4) so the lesson is reviewable and
-- invalidatable like any other, and so an operator rejecting it records
-- a #14 reason code against a labelled source.
--
-- The new column is nullable and no other code writes it: an
-- episode-sourced lesson keeps naming its episode and reads NULL here.
-- A store at version 16 has no shipped data behind this column, so the
-- ALTER is the whole forward path.

ALTER TABLE lessons ADD COLUMN source_human_input TEXT;

-- The operator queue filters and displays by source; the lesson detail
-- page reads the event by id. Both lookups are keyed by this column.
CREATE INDEX idx_lessons_source_human_input
  ON lessons (source_human_input);
