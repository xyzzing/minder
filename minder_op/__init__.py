"""minder_op — read-mostly operator CLI for minder's memory plane
(docs/minder-operator-8a-8b-from-v10.md).

8A is strictly read-only: status, flags, episodes, lessons, gaps,
consults (labels from frontier_evals — the 003 INTEGER column is legacy),
decisions, export-stats. 8B adds three explicit writes (lessons
invalidate/promote, gaps close) that require --yes and go through the
existing memory APIs only.

The CLI never changes a Warden action, never writes SKILLS.md, never
edits flags (env/systemd own those), never prunes logs.
"""
__version__ = "0.1"
