# diagnose-build-environment

The failure is a build/dependency environment problem until proven
otherwise.

## Sequence

1. Check the package manager is installed and on PATH (pnpm, npm, yarn).
2. Check the lockfile is present and not corrupted (pnpm-lock.yaml,
   package-lock.json, yarn.lock).
3. Check node_modules exists and is not stale (re-run the install if
   the lockfile changed).
4. Check the build tool (webpack, esbuild, vite) is resolvable from the
   project root.
5. Check environment variables the build expects (NODE_ENV, PATH,
   registry mirrors).
6. Only if everything checks out, re-escalate to code investigation.

## Never

- Edit product code to work around a missing tool, dependency, or
  registry.
- Delete node_modules or the lockfile without confirming the install
  command first.
- Install unrequested packages or switch package managers.
