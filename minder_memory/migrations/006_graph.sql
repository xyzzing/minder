-- Graph relationships (docs/minder-phase-2-3-frontier-coding.md P3.1).
-- SQLite only — no graph database. Edges are temporal: valid_to set means
-- no longer authoritative, history stays queryable.

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,            -- File | Test | Commit | FailureSignature | Episode | Lesson | Skill | Patch | VerificationRun
  properties_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE edges (
  id TEXT PRIMARY KEY,
  from_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,       -- AFFECTS | FAILED_TEST | DERIVED_FROM | VERIFIED_BY | MODIFIED | APPLIES_TO | SUPERSEDES | CONTRADICTS | RAN | HAS_FAILURE
  to_id TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  UNIQUE(from_id, edge_type, to_id)
);

CREATE INDEX idx_edges_from ON edges(from_id, edge_type);
CREATE INDEX idx_edges_to ON edges(to_id, edge_type);
CREATE INDEX idx_nodes_type ON nodes(type);
