---
name: minder-gap-triage
description: Decide what minder's skill gaps are actually evidence of before acting on them. Use when reading the /gaps console page or the skill_gaps table, when asked what skills should exist or what to build from failures, when asked to close a skill gap, or when a minder_op gap command is involved. Not for running a task, or for writing skills that do not come from minder's captured failures.
---

# minder-gap-triage

Most recorded skill gaps are capture artefacts. A measured triage of 607 rows
found 447 of them were not failures: `is_failure()` matches `"error:"` in the
whole tool response, so reading a file that mentions an exception class is
recorded as a failure of that class.

## Sequence

1. Read the raw event, not the label. Open the episode behind the row and
   look for the command and its status. Completion: you can say what ran and
   what it returned, or you have recorded that the capture does not hold it.
2. Assign evidence status to every row before any category. Completion: every
   row is in exactly one status bucket, and no row is categorised on its
   `failure_key` alone.
3. Group confirmed failures by mechanism, counting raw events, episodes,
   mechanisms and candidate skills separately. Completion: each mechanism
   names the shared cause, not the shared error word.
4. Pick the cheapest effective intervention. An every-call habit is a rule
   line; a tool misuse the harness could block is a hook; a broken
   environment is fixed; a product bug is fixed. A skill is for a recurring
   task family with non-obvious know-how. Completion: each mechanism has a
   disposition and a reason it is not one of the cheaper ones.
5. Draft only what survived step 4, and evaluate it on representative tasks
   and negative-trigger tasks before claiming it works. Completion: the
   evaluation is recorded, not asserted.
6. Close a gap only on demonstrated recovery or a preventive check firing in
   a real or test run. Completion: the row is closed by `close_gap` with a
   reason, or it is still open and you say so.

Read [`../bodies/minder-gap-triage.md`](../bodies/minder-gap-triage.md) for
the signature-to-correction table, the worked example, and the Avoid list;
it is the one place they are written down.
