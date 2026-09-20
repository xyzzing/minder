## Mode & Escalation contract (minder)

You run in EXEC mode by default (fast, low temperature). Deliberation is
triggered FOR you by the minder watchdog; you never need to request it.

1. NEVER submit the same failing edit or command a third time. On the second
   failure: re-read the actual file state and regenerate the anchor, or accept
   the escalation digest below and change your approach.
2. When you receive a "[minder] ESCALATION" digest, it blocks your tool call.
   Obey its ordering: root-cause hypothesis FIRST (zero tool calls before it),
   then fresh ground truth (re-read the file / rerun the failing command with
   the smallest possible probe), then the retry. Prior attempts are VOID.
3. After a THINK-RETRY (L1): output a short plan, then apply edits in exec
   style. Do not re-explain the whole task.
4. FRONTIER RESPONSE content (L2): apply it as directed, then verify. Never
   commit frontier output without a green verification run.
5. An L3 digest means budgets are exhausted: stop retrying that action and
   report the failure to the operator instead.
