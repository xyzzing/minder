.PHONY: gates

# The one command P3 cites: suite (incl. the instruction gate), lint,
# ratchet. Types are CI-enforced (pinned mypy job); see
# AGENTS.project.md Verification for the mypy command.
gates:
	env -u LD_LIBRARY_PATH python3 -m pytest -q
	ruff check .
	scripts/ratchet.sh
