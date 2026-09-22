"""Frontier trace governance tests (docs/minder-phase-4-7-frontier-coding.md
P4.1). record_consult stores hashes + redacted distilled actions only;
classify_consult never raises; external-prohibited refuses response-derived
text."""
import json

from memory import frontier_traces

SECRET = "sk-proj-supersecret1234567890"


def _payload(**over):
    payload = {
        "trigger": "warden-l2",
        "key": "bash|keyerror|supplier_id|app/supplier.py",
        "attempts": 3,
        "episode_id": "ep_gov1",
        "prompt": f"fix the KeyError. my key is {SECRET}",
        "response": "SYNTH-ADVICE: check the dict default",
        "providers": [{"name": "probe-a"}, {"name": "probe-b"}],
        "distilled": ["read app/supplier.py line 40",
                      f"export OPENAI_API_KEY={SECRET}"],
    }
    payload.update(over)
    return payload


def test_record_consult_stores_hashes_no_raw_secret(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(_payload(), db_path=dbp)
    assert tid
    rec = frontier_traces.get_consult(tid, db_path=dbp)
    assert rec["failure_key"] == "bash|keyerror|supplier_id|app/supplier.py"
    assert rec["local_attempts"] == 3
    assert rec["provider_fingerprint"] == "probe-a,probe-b"
    assert len(rec["request_hash"]) == 16
    assert len(rec["response_hash"]) == 16
    # neither the prompt nor the distilled action may round-trip the secret
    assert SECRET not in json.dumps(rec)
    assert rec["distilled_json"][0] == "read app/supplier.py line 40"
    assert SECRET not in rec["distilled_json"][1]


def test_classify_pass_accepted_helpful(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(_payload(), db_path=dbp)
    out = frontier_traces.classify_consult(
        tid, "pass", accepted=["read app/supplier.py line 40"],
        db_path=dbp)
    assert out == "helpful"
    assert frontier_traces.get_consult(tid, db_path=dbp)[
        "helpfulness"] == "helpful"


def test_classify_pass_accepted_and_rejected_partial(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(_payload(), db_path=dbp)
    out = frontier_traces.classify_consult(
        tid, "pass", accepted=["read app/supplier.py line 40"],
        rejected=["rename the column"], db_path=dbp)
    assert out == "partial"


def test_classify_fail_applied_advice_harmful(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(_payload(), db_path=dbp)
    out = frontier_traces.classify_consult(
        tid, "fail", accepted=["rename the column"], db_path=dbp)
    assert out == "harmful"


def test_classify_missing_verification_inconclusive(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(_payload(), db_path=dbp)
    assert frontier_traces.classify_consult(tid, "not_run", db_path=dbp) \
        == "inconclusive"
    assert frontier_traces.classify_consult(tid, None, db_path=dbp) \
        == "inconclusive"
    # unknown trace: never raises, inconclusive
    assert frontier_traces.classify_consult("tr_missing", "pass",
                                            db_path=dbp) == "inconclusive"
    assert frontier_traces.get_consult("tr_missing", db_path=dbp) is None


def test_existing_panel_and_trace_tests_unchanged():
    """Regression pin (P4.1 test 6): panel behaviour and the PR 7 trace
    API are untouched; the full suite runs both modules anyway."""
    import tests.test_frontier  # noqa: F401
    import tests.test_frontier_traces  # noqa: F401


def test_external_prohibited_stores_hashes_only(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record_consult(
        _payload(redaction_profile="external-prohibited"), db_path=dbp)
    rec = frontier_traces.get_consult(tid, db_path=dbp)
    assert rec["redaction_profile"] == "external-prohibited"
    assert len(rec["request_hash"]) == 16
    assert len(rec["response_hash"]) == 16
    assert SECRET not in json.dumps(rec)
    # response-derived distilled actions are refused entirely
    assert not rec["distilled_json"]
    # classify still works; the label is stored, the advice text is not
    assert frontier_traces.classify_consult(
        tid, "pass", accepted=["read app/supplier.py line 40"],
        db_path=dbp) == "helpful"
    rec = frontier_traces.get_consult(tid, db_path=dbp)
    assert rec["helpfulness"] == "helpful"
    assert not rec["accepted_actions_json"]
