#!/usr/bin/env bash
# minder release automation — test → secret-scan → commit → push → tag+release.
# Usage: ./release.sh [version]        (default: from minder.py MINDER_VERSION)
# Idempotent: safe to re-run; skips steps already done.
set -euo pipefail

cd "$(dirname "$0")"
VERSION="${1:-$(python3 -c 'import minder; print(minder.MINDER_VERSION)')}"
REPO="xyzzing/minder"
TAG="v${VERSION}"

say() { printf '[release] %s\n' "$*"; }
die() { printf '[release] STOP: %s\n' "$*" >&2; exit 1; }

# --- gates -------------------------------------------------------------------
say "running test suite…"
# env -u LD_LIBRARY_PATH: inside zcode/appimage shells a shadowing libpython
# poisons sys.executable and breaks every subprocess test (no-op elsewhere).
MINDER_NO_SYSTEMD=1 env -u LD_LIBRARY_PATH python3 -m pytest tests/ -q \
  || die "tests red — fix first"

say "scanning for secrets…"
if git grep -nE '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})' \
    -- . ':!tests/*' 2>/dev/null \
   || grep -rnE '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})' \
       --include='*.py' --include='*.sh' --include='*.json' --include='*.md' \
       --include='*.yaml' . 2>/dev/null | grep -v './tests/'; then
  die "secret-looking string found — review before publishing"
fi

# --- git ---------------------------------------------------------------------
if [ ! -d .git ]; then
  git init -q
  git branch -M main
  say "git initialized"
fi
git add -A
if git diff --cached --quiet; then
  say "nothing to commit"
else
  git commit -qm "minder v${VERSION}: measure → instruct → escalate → audit"
  say "committed"
fi

# --- remote / push -----------------------------------------------------------
if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin --push \
    || die "gh repo create failed (exists? auth?)"
  say "created $REPO and pushed"
else
  git push -u origin main || die "push failed"
fi

# --- tag + release -----------------------------------------------------------
if git rev-parse "$TAG" >/dev/null 2>&1; then
  say "tag $TAG exists — skipping release"
else
  git tag -a "$TAG" -m "minder $TAG"
  git push origin "$TAG"
  gh release create "$TAG" --title "minder $TAG" --notes \
"Capability-measured escalation watchdog for local coding agents.

- CAP probe: thinking mechanism, effort vocabulary, tool-call cleanliness — measured, never assumed; re-verified at every proxy boot
- L0→L3 ladder: think-retry → frontier panel consult (DeepSeek + optional OpenAI cross-check) → stop alarm; refundable budgets, backfire breaker
- Stateless escalation channel via digest markers — works with any OpenAI-compatible harness through the proxy on :8390
- zcode + Claude-Code-style hooks-bridge integration (dsh), compaction survival, verify consults, domain profiles with egress redaction and hash-chained audit ledger
- Optional advisory reflex tier (CPU micro-classifier, fail-open)
- Token accounting in the ledger; CAP-measured effort vocabulary surfaced to harness UIs
- 118 tests, Python 3.10+ stdlib only

Install: \`./install.sh\` — see the README."
  say "released $TAG"
fi

say "done: https://github.com/$REPO"
