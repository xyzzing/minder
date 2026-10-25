"""Output-contract templates (issue #47): the RA-016-vocabulary record
shape, the digest pinning, the resolve/apply discipline (opt-in,
non-streaming, fail-closed on unknown or mismatched templates), the
band field merge, and the reserve lint that must fund a contract's
est_tokens under the band ceiling."""
import json


from minder_decision import difficulty
from minder_decision import output_contracts as contracts
from minder_op import room_checks


def _record(**over):
    rec = {
        "template_id": "finance-npv-working",
        "family": "finance",
        "status": "evidence_checked",
        "producer_profile": "qwen3-8b-local",
        "applicability": {"model_specific": True},
        "provenance": {"source": "a1 arms"},
        "payload": {
            "system_hint": "Work inside <work></work> first.",
            "suffix_template": "Write brief working in <work></work>, "
                               "then one JSON object.",
        },
        "est_tokens": 180,
    }
    for key, value in over.items():
        if value is None:
            rec.pop(key, None)
        elif isinstance(rec.get(key), dict) and isinstance(value, dict):
            rec[key] = {**rec[key], **value}
        else:
            rec[key] = value
    return rec


def _good_record(**over):
    return _record(**over)


# ---- validate_template ------------------------------------------------------

def test_valid_template_has_no_errors():
    assert contracts.validate_template(_good_record()) == []


def test_missing_fields_are_named():
    errors = contracts.validate_template({"template_id": "x"})
    assert any("status" in e for e in errors)
    assert any("payload" in e for e in errors)
    assert any("est_tokens" in e for e in errors)
    assert any("producer_profile" in e for e in errors)


def test_unknown_status_rejected():
    rec = _good_record()
    rec["status"] = "verified_forever"
    assert contracts.validate_template(rec)


def test_est_tokens_must_be_a_real_int():
    for bad in ("180", True, 1.5, None):
        assert contracts.validate_template(_good_record(est_tokens=bad))


# ---- digest -----------------------------------------------------------------

def test_digest_is_stable_and_payload_bound():
    rec = _good_record()
    assert contracts.payload_digest(rec) == contracts.payload_digest(rec)
    other = _good_record()
    other["payload"]["suffix_template"] += " Extra."
    assert contracts.payload_digest(rec) != contracts.payload_digest(other)
    assert len(contracts.payload_digest(rec)) == 64


# ---- load_contracts ---------------------------------------------------------

def _write(tmp_path, name, rec):
    path = tmp_path / name
    path.write_text(json.dumps(rec), encoding="utf-8")
    return path


def test_config_dir_wins_over_share_dir(tmp_path):
    share, config = tmp_path / "share", tmp_path / "config"
    share.mkdir()
    config.mkdir()
    _write(share, "a.json", _good_record(
        payload={"suffix_template": "share version"}))
    _write(config, "a.json", _good_record(
        payload={"suffix_template": "config version"}))
    got, problems = contracts.load_contracts([share, config])
    assert problems == []
    assert got["finance-npv-working"]["payload"]["suffix_template"] \
        == "config version"


def test_distinct_ids_from_both_dirs_coexist(tmp_path):
    share, config = tmp_path / "share", tmp_path / "config"
    share.mkdir()
    config.mkdir()
    _write(share, "a.json", _good_record(template_id="from-share"))
    _write(config, "b.json", _good_record(template_id="from-config"))
    got, problems = contracts.load_contracts([share, config])
    assert problems == []
    assert set(got) == {"from-share", "from-config"}


def test_malformed_file_is_a_named_problem_not_a_crash(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    _write(share, "bad.json", _good_record(est_tokens="lots"))
    (share / "worse.json").write_text("{not json", encoding="utf-8")
    got, problems = contracts.load_contracts([share])
    assert got == {}
    assert len(problems) == 2
    assert all("worse.json" in p or "bad.json" in p for p in problems)


# ---- resolve_contract -------------------------------------------------------

def _band(**over):
    band = {"contract": "finance-npv-working", "contract_sha256": None}
    band.update(over)
    return band


def test_resolve_not_applicable_when_band_has_no_contract():
    assert contracts.resolve_contract({}, {}, {}, opt_in=True) == \
        (None, None)
    assert contracts.resolve_contract({}, {"contract": None}, {},
                                      opt_in=True) == (None, None)


def test_resolve_refuses_without_opt_in():
    rec, why = contracts.resolve_contract({}, _band(), {}, opt_in=False)
    assert rec is None and why == "no-opt-in"


def test_resolve_refuses_streaming_requests():
    rec, why = contracts.resolve_contract({"stream": True}, _band(), {},
                                          opt_in=True)
    assert rec is None and why == "streaming"


def test_resolve_names_unknown_templates():
    rec, why = contracts.resolve_contract({}, _band(), {}, opt_in=True)
    assert rec is None and why == "unknown-contract:finance-npv-working"


def test_resolve_enforces_the_status_floor():
    store = {"finance-npv-working": _good_record(status="candidate")}
    rec, why = contracts.resolve_contract({}, _band(), store, opt_in=True)
    assert rec is None and why == "status-floor:candidate"


def test_resolve_refuses_digest_mismatch():
    store = {"finance-npv-working": _good_record()}
    rec, why = contracts.resolve_contract(
        {}, _band(contract_sha256="0" * 64), store, opt_in=True)
    assert rec is None and why == "digest-mismatch"
    ok, why2 = contracts.resolve_contract(
        {}, _band(contract_sha256=contracts.payload_digest(store[
            "finance-npv-working"])), store, opt_in=True)
    assert ok is store["finance-npv-working"] and why2 is None


def test_resolve_skips_schema_placeholders_the_proxy_cannot_fill():
    rec = _good_record()
    rec["payload"]["suffix_template"] = "then JSON shaped: {answer_shape}"
    store = {"finance-npv-working": rec}
    got, why = contracts.resolve_contract({}, _band(), store, opt_in=True)
    assert got is None and why == "schema-placeholder"


def test_resolve_happy_path():
    store = {"finance-npv-working": _good_record()}
    rec, why = contracts.resolve_contract({"stream": False}, _band(),
                                          store, opt_in=True)
    assert rec is store["finance-npv-working"] and why is None


# ---- apply_contract ---------------------------------------------------------

def test_apply_appends_suffix_to_last_user_message():
    req = {"messages": [
        {"role": "system", "content": "You are careful."},
        {"role": "user", "content": "Compute the NPV."},
    ]}
    summary = contracts.apply_contract(req, _good_record())
    assert req["messages"][-1]["content"].endswith(
        "then one JSON object.")
    assert "NPV." in req["messages"][-1]["content"]
    assert summary["chars_added"] > 0
    assert summary["est_tokens"] == 180


def test_apply_inserts_system_hint_when_absent():
    req = {"messages": [{"role": "user", "content": "q"}]}
    contracts.apply_contract(req, _good_record())
    assert req["messages"][0]["role"] == "system"
    assert "<work></work>" in req["messages"][0]["content"]


def test_apply_appends_to_existing_system_message():
    req = {"messages": [{"role": "system", "content": "Be careful."},
                        {"role": "user", "content": "q"}]}
    contracts.apply_contract(req, _good_record())
    assert req["messages"][0]["content"].startswith("Be careful.")
    assert "<work></work>" in req["messages"][0]["content"]


def test_apply_refuses_non_string_content_without_mutation():
    req = {"messages": [{"role": "user",
                         "content": [{"type": "text", "text": "q"}]}]}
    before = json.dumps(req)
    summary = contracts.apply_contract(req, _good_record())
    assert summary["applied"] is False and summary["why"] == "content-shape"
    assert json.dumps(req) == before


# ---- band field + reserve lint ---------------------------------------------

def test_default_bands_carry_an_empty_contract_field():
    for label in difficulty.LABELS:
        band = difficulty.band_for(label, {})
        assert band["contract"] is None


def test_band_merge_carries_contract_fields():
    band = difficulty.band_for("complex", {"difficulty_bands": {
        "complex": {"contract": "finance-npv-working",
                    "contract_sha256": "a" * 64}}})
    assert band["contract"] == "finance-npv-working"
    assert band["contract_sha256"] == "a" * 64


def test_apply_band_reports_the_contract():
    req = {"max_tokens": 32768}
    band = difficulty.band_for("complex", {})
    band["contract"] = "finance-npv-working"
    applied = difficulty.apply_band(req, band, {})
    assert applied["contract"] == "finance-npv-working"


def test_reserve_lint_breaks_when_contract_tokens_are_unknown():
    overrides = {"complex": {"budget": 10240, "max_tokens": 32768,
                             "contract": "finance-npv-working"}}
    assert room_checks.reserve_violations(
        overrides, {"contract_est_tokens": {}}) == ["complex"]


def test_reserve_lint_funds_contract_tokens_under_the_ceiling():
    overrides = {"complex": {"budget": 10240, "max_tokens": 32768,
                             "contract": "finance-npv-working"}}
    fits = {"contract_est_tokens": {"finance-npv-working": 180}}
    assert room_checks.reserve_violations(overrides, fits) == []
    huge = {"contract_est_tokens": {"finance-npv-working": 22400}}
    assert room_checks.reserve_violations(overrides, huge) == ["complex"]


def test_reserve_lint_ignores_bands_without_contracts():
    overrides = {"complex": {"budget": 10240, "max_tokens": 32768}}
    assert room_checks.reserve_violations(overrides, {}) == []
