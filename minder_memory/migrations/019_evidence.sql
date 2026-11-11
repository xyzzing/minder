-- Issue #79: the capture layer weighs structured status before content
-- words, and the evidence kind rides the event and the gap row so triage
-- can filter on status rather than content. close_reason persists what
-- close_gap previously computed and dropped.
ALTER TABLE events ADD COLUMN evidence TEXT;
ALTER TABLE skill_gaps ADD COLUMN evidence TEXT;
ALTER TABLE skill_gaps ADD COLUMN close_reason TEXT;
