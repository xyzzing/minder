# Glossary

One name per concept, for code identifiers, briefs, docs, and commit
messages. Each entry says what the thing is, not what it does, and lists
the words to stop using for it. User-facing copy follows the product
wording and is exempt.

## Process

**Contract**:
An architecture entry in `AGENTS.project.md`: what it owns, the sanctioned
path, forbidden bypasses, and its gate.
_Avoid_: architecture rule, architecture convention

**Gate**:
A command that fails a commit, push, or CI run when a rule breaks.
_Avoid_: blocking check, guard script

**Brief**:
The requirements file handed to a subagent for one task.
_Avoid_: task prompt, instructions file

**Spec**:
A design document approved before a plan is written.
_Avoid_: design doc

**Seam**:
The interface a test exercises; the highest one that reaches the behavior.
_Avoid_: test boundary, layer under test

## minder domain

**Episode**:
One failure-to-resolution arc over tool events; the unit lessons are
verified against.
_Avoid_: incident, ticket, task (a task is a dsh session goal)

**Lesson**:
A distilled, status-bearing (verified / candidate / invalidated) rule
drawn from an episode; candidates are inert until promoted with tests.
_Avoid_: memory entry, note

**Skill gap**:
A recorded absence: a failure that matched no known skill trigger.
_Avoid_: TODO, backlog item

**Verdict**:
A scorecard or comparator outcome (ok / warning, PASS / FAIL /
INSUFFICIENT_SAMPLE / NON_COMPARABLE). Never a percentage alone.
_Avoid_: score, rating

**Capture**:
The pipeline from hook invocation to persisted evidence (ledger or
sqlite), measured as coverage against a floor.
_Avoid_: logging, telemetry

**Sink**:
The loopback write sidecar sandboxed hooks persist through; not the
sqlite store itself.
_Avoid_: database, server

**Frontier**:
An outside model consulted for escalations; its output is untrusted
until verified.
_Avoid_: external LLM, API model

**Staged share**:
The deployed copy of minder (`~/.local/share/minder`) the systemd units
run from; never the repo checkout.
_Avoid_: install dir, prod
