"""Canonical failure keys and fingerprints (docs/prd-memory-v1.md PR 1)."""
import json
from pathlib import Path

from memory import canonicalise as canon

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"
REPO = "/home/operator/projects/minder"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_same_failure_across_timestamps_and_abs_paths():
    a, b = load("pytest_keyerror_a.json"), load("pytest_keyerror_b.json")
    ka = canon.failure_key(a)
    kb = canon.failure_key(b)
    assert ka == kb
    assert ka.startswith("bash|keyerror|")
    assert "tests/test_supplier.py::test_supplier_lookup" in ka


def test_abs_vs_rel_path_same_key_when_repo_known():
    a = load("pytest_keyerror_a.json")
    with_repo = canon.failure_key(a, repo=REPO)
    without = canon.failure_key(a)
    # repo-relative recovery: the relpath segment is stable either way
    assert with_repo.split("|")[3] == without.split("|")[3] == \
        "tests/test_supplier.py"
    # documented fallback (no repo, absolute path in file_path): basename
    abs_event = dict(a, file_path="/home/operator/projects/minder/tests/"
                                  "test_supplier.py", error_excerpt="",
                     repo="")
    assert canon.failure_key(abs_event).split("|")[3] == \
        "test_supplier.py"


def test_different_families_different_keys():
    a = load("pytest_keyerror_a.json")
    b = load("pytest_assert_error.json")
    ka, kb = canon.failure_key(a), canon.failure_key(b)
    assert ka != kb
    assert ka.split("|")[1] == "keyerror"
    assert kb.split("|")[1] == "assertionerror"


def test_unchanged_retry_true_for_identical_attempt():
    a, b = load("pytest_keyerror_a.json"), load("pytest_keyerror_b.json")
    assert canon.unchanged_retry(a, b)


def test_unchanged_retry_false_on_progress():
    a, b = load("pytest_keyerror_a.json"), load("pytest_keyerror_b.json")
    # new file content involved
    assert not canon.unchanged_retry(a, dict(b, content_hash="deadbeef"))
    # new hypothesis stated
    assert not canon.unchanged_retry(a, dict(b, hypothesis="map is unseeded"))
    # different command (different action entirely)
    c = dict(b, command="python3 -m pytest tests/other.py -q",
             args_json='{"command": "python3 -m pytest tests/other.py -q"}')
    assert not canon.unchanged_retry(a, c)


def test_missing_fields_fail_closed_conservative_never_raise():
    assert canon.failure_key({}) == "unknown|unknown|none|none"
    assert canon.action_fingerprint({}) == "unknown" or \
        canon.action_fingerprint({})  # stable non-raising value
    assert canon.failure_key({"tool": None, "error_excerpt": None}) == \
        "unknown|unknown|none|none"
    # both degrade to the same conservative key → same_failure holds
    assert canon.same_failure({}, None) is True


def test_api_key_shaped_strings_redacted_before_keying():
    base = "connect failed with auth error: Bad credentials for "
    e1 = {"tool": "bash", "error_excerpt": base + "sk-live-abcdef1234567890"}
    e2 = {"tool": "bash", "error_excerpt": base + "sk-live-zzzzzzzz99999999"}
    norm = canon.normalise_error_excerpt(e1["error_excerpt"])
    assert "sk-live" not in norm and canon._REDACTED.lower() in norm
    assert canon.failure_key(e1) == canon.failure_key(e2)
    # excerpt stored for evidence is the redacted one too
    assert canon.normalise_error_excerpt(e2["error_excerpt"]) == norm


def test_exit_code_family_and_action_fingerprint_stability():
    ev = {"tool": "bash", "command": "make lint",
          "error_excerpt": "process exited with code 2"}
    key = canon.failure_key(ev)
    assert key.split("|")[1] == "exit-2"
    fp1 = canon.action_fingerprint(ev)
    fp2 = canon.action_fingerprint(dict(ev, error_excerpt="other failure"))
    assert fp1 == fp2  # fingerprint ignores the outcome
    fp3 = canon.action_fingerprint(dict(ev, command="make test"))
    assert fp1 != fp3
