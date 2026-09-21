# inspect-schema-boundary

Inspect the source fixture/contract before any mapping edit.

## Sequence

1. **Inspect** — open the fixture, schema, or contract that produced the
   value; read the real field names.
2. **Hypothesis** — state in one sentence what the producer and the
   consumer each believe the shape is, and where they diverge.
3. **Patch** — make the smallest change that reconciles them.
4. **Regression** — add or update a test that fails before the patch.
5. **Verify** — run the direct test and one downstream consumer test.

## Never

- Map fields by guessing names.
- Skip the downstream test.
