# minder-gap-triage reference

One row per signature the gaps surface produces. The correction is what the
row is actually evidence of.

| signature | correction |
|---|---|
| `sample_error` is source code, a docstring, a diff, or a grep hit | capture artefact. `error_family()` matched an exception class name in the output text. Skip it and write nothing. |
| `sample_error` ends `[exit code: 1]` with no traceback | expected outcome (`grep` and `diff` both exit 1), or the error sits above the 300-char tail. Read the episode before concluding. |
| `sample_error` matches none of the error signs | no error sign at all. The row is the response tail; nothing failed that the capture can name. |
| `failure_key` ends `\|none\|none` | `relpath_of()` found no file, so the locus fields are guesses. They name what the output mentioned, not where it broke. |
| one mechanism spread across many `failure_key` values | one mechanism. Group on the sample text; the key names what the command printed. |
| a row still `open` after the fix landed | closure is an explicit operator decision through `close_gap`. Writing the skill is not closure. |
| an `AssertionError` from `File "<stdin>"` | an inline probe asserting without a message, not a product failure. |
| `sample_error` is a `<path>/<content>` read envelope | a `read` tool call whose output was classified as a failure because the file mentions an error class. |

## Bad and good

Bad — the key looks like a family, so it becomes a skill:

```
bash|oserror|the|pathlib.path   ->  "we need an OSError skill"
```

Good — read the sample before the key. The sample is
`def _downgrade_band(...)`: someone read a file that mentions `OSError`, and
the classifier matched the name in the text. Skip the row.

## Evidence status

Assign one before any category, and let the category follow it.

| status | what it looks like |
|---|---|
| confirmed failure | a tool rejection, a live traceback, a sandbox denial |
| expected outcome | `grep` exit 1, `diff` exit 1, a deliberate decline |
| user-control event | the operator cancelled or dismissed a plan |
| capture artefact | the tail of benign output, or a truncated join |
| insufficient evidence | no command, no status, no error in the sample |

## Avoid

- One skill per row. Raw events become episodes, episodes become mechanisms,
  mechanisms become candidate skills; each step collapses the count.
- Counting `grep` exit 1 as a failure.
- Closing a gap from a self-report. A gap closes on demonstrated recovery or
  a preventive check firing in a real or test run.
- Promoting plausible prose without an evaluation.
- Reading the `failure_key` family as the failure mechanism.
