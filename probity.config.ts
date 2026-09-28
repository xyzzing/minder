import { defineConfig, forbidCommandPattern, forbidContentPattern } from '@nizos/probity'

// Probity pre-action gates for minder. These BLOCK the write/command
// before it happens - the commit-time counterparts live in
// tests/test_instruction_gate.py (gap-trap), which stay authoritative;
// this file is the fast feedback layer. Keep the two in sync: a rule
// added here should have a gap-trap contract (AGENTS.project.md).
export default defineConfig({
  rules: [
    {
      // Contract: SQLite access - connections and transactions live in
      // db.py; queries.py reads. Never elsewhere.
      files: ['minder_memory/**', 'minder_decision/**', 'minder_op/**',
              'minder_web/**', 'minder_trace/**'],
      rules: [
        forbidContentPattern({
          match: /\bsqlite3\.connect\(/,
          reason:
            'minder keeps every sqlite connection in minder_memory/db.py ' +
            '(reads: minder_op/queries.py). Add your access there - see the ' +
            'SQLite access contract in AGENTS.project.md.',
        }),
        forbidContentPattern({
          match: /["']BEGIN IMMEDIATE/,
          reason:
            'Hand-rolled transactions are banned: use ' +
            'minder_memory.db.transaction() (never nested). See the SQLite ' +
            'access contract in AGENTS.project.md.',
        }),
      ],
    },
    {
      // Contract: process execution - shlex.split + shell=False only.
      files: ['minder.py', 'hook.py', 'proxy.py', 'sink.py', 'frontier.py',
              'adapter.py', 'reflex.py', 'minder_memory/**', 'minder_op/**',
              'minder_web/**'],
      rules: [
        forbidContentPattern({
          match: /shell\s*=\s*True|os\.system\s*\(|\beval\s*\(|\bexec\s*\(/,
          reason:
            'minder spawns subprocesses only via shlex.split + shell=False ' +
            '(run_frontier in hook.py is the sanctioned path). See the ' +
            'process execution contract in AGENTS.project.md.',
        }),
      ],
    },
    {
      // Destructive-command guard: minder's live state and the owner's
      // dsh profile are one careless command away from data loss.
      files: ['**'],
      rules: [
        forbidCommandPattern({
          match: /rm\s+-rf\s+\/(?!\w)|git\s+push\s+(--force|-f)\s+origin\s+main\b|drop\s+table\s/i,
          reason:
            'Blocked: destructive command against main or the filesystem ' +
            'root. minder lands on main by direct push; force-push and ' +
            'rm -rf / are never part of the flow (see AGENTS.project.md).',
        }),
      ],
    },
  ],
})
