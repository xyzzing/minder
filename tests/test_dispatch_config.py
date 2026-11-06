"""Dispatch config (#89, stage 1): the defaults deny everything, and a
malformed file is a named error, never a silent fallback to permissive
values.
"""
import json

import pytest

from minder_op import dispatch_config


def test_defaults_deny():
    cfg = dispatch_config.default_config()
    assert cfg.repo is None
    assert cfg.allowed_senders == ()
    assert cfg.accept_mode == "observe"


def test_missing_file_is_a_named_error(tmp_path):
    with pytest.raises(dispatch_config.DispatchConfigError) as exc:
        dispatch_config.load_config(tmp_path / "dispatch.json")
    assert exc.value.code == "config_missing"


def test_invalid_accept_mode_rejected(tmp_path):
    path = tmp_path / "dispatch.json"
    path.write_text(json.dumps(
        {"repo": "xyzzing/minder", "accept_mode": "yolo"}))
    with pytest.raises(dispatch_config.DispatchConfigError) as exc:
        dispatch_config.load_config(path)
    assert exc.value.code == "accept_mode_invalid"


def test_repo_must_be_owner_slash_name(tmp_path):
    path = tmp_path / "dispatch.json"
    path.write_text(json.dumps({"repo": "not-a-repo"}))
    with pytest.raises(dispatch_config.DispatchConfigError) as exc:
        dispatch_config.load_config(path)
    assert exc.value.code == "repo_invalid"


def test_valid_file_roundtrips(tmp_path):
    path = tmp_path / "dispatch.json"
    path.write_text(json.dumps({
        "repo": "xyzzing/minder", "allowed_senders": ["alice"],
        "accept_mode": "auto", "poll_interval_min": 15}))
    cfg = dispatch_config.load_config(path)
    assert cfg.repo == "xyzzing/minder"
    assert cfg.allowed_senders == ("alice",)
    assert cfg.accept_mode == "auto"
    assert cfg.poll_interval_min == 15


def test_malformed_json_is_a_named_error(tmp_path):
    path = tmp_path / "dispatch.json"
    path.write_text("{not json")
    with pytest.raises(dispatch_config.DispatchConfigError) as exc:
        dispatch_config.load_config(path)
    assert exc.value.code == "config_invalid"
