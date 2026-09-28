"""Adapter-layer tests (PRD v0.9 9D, AT-Q13/Q14 + the I-2/I-3 laws)."""
import json

import pytest

from minder_quality import adapters


def test_at_q14_parser_outside_menu_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "id": "evil", "languages": ["python"],
        "probe_argv": ["true"], "argv": ["true"],
        "parser": "python", "pattern": "."}))
    with pytest.raises(adapters.AdapterError, match="parser"):
        adapters.load(extra_dirs=[tmp_path])


def test_at_q14_unknown_placeholder_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "id": "shelly", "languages": ["python"],
        "probe_argv": ["true"],
        "argv": ["bash", "-c", "{shell}"],
        "parser": "line_regex", "pattern": "x"}))
    with pytest.raises(adapters.AdapterError, match="placeholder"):
        adapters.load(extra_dirs=[tmp_path])


def test_missing_analyzer_is_unavailable_never_zero(tmp_path):
    adapter = {"id": "absent-tool", "languages": ["python"],
               "probe_argv": ["definitely-not-on-path-xyz", "--version"],
               "argv": ["definitely-not-on-path-xyz", "{paths}"],
               "parser": "line_regex", "pattern": "."}
    findings, status = adapters.run(adapter, [tmp_path], tmp_path)
    assert findings == []
    assert status.startswith("unavailable:")


def test_at_q13_fake_adapter_findings_flow(tmp_path):
    """A working fake analyzer (argv-only, a real script file): its
    findings normalize to (rule, rel, snippet); the identical finding
    in the pre-state subtracts out (I-2, see the custom-dir test)."""
    scanner = tmp_path / "scan.py"
    scanner.write_text(
        "import json, sys\n"
        "out = [{'rule': 'X001', 'path': p, 'message': 'smell'}"
        " for p in sys.argv[1:]]\n"
        "print(json.dumps({'findings': out}))\n")
    adapter = {"id": "fake-scan", "languages": ["python"],
               "probe_argv": [sys_exec(), "-c", "print('1.0')"],
               "argv": [sys_exec(), str(scanner), "{paths}"],
               "parser": "json_path", "pointer": "findings",
               "fields": {"rule": "$.rule", "path": "$.path",
                          "message": "$.message"}}
    (tmp_path / "post").mkdir()
    (tmp_path / "post" / "task.py").write_text("x = 1\n")
    findings, status = adapters.run(
        adapter, [tmp_path / "post" / "task.py"], tmp_path / "post")
    assert status.startswith("ok") and len(findings) == 1
    rule, rel, snippet = findings[0]
    assert rule == "X001" and rel == "task.py"


def sys_exec():
    import sys
    return sys.executable


def test_assess_touched_with_custom_dir(tmp_path, monkeypatch):
    scanner = tmp_path / "scan.py"
    scanner.write_text(
        "import json, sys\n"
        "out = [{'rule': 'X001', 'path': p, 'message': 'smell'}"
        " for p in sys.argv[1:]]\n"
        "print(json.dumps({'findings': out}))\n")
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    (adapter_dir / "fake.json").write_text(json.dumps({
        "id": "fake-scan", "languages": ["python"],
        "probe_argv": [sys_exec(), "-c", "print('1.0')"],
        "argv": [sys_exec(), str(scanner), "{paths}"],
        "parser": "json_path", "pointer": "findings",
        "fields": {"rule": "$.rule", "path": "$.path",
                   "message": "$.message"}}))
    for name, content in (("pre", "x = 1\n"), ("post", "x = 1\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "task.py").write_text(content)
    findings, statuses = adapters.assess_touched(
        tmp_path / "pre", tmp_path / "post", ["task.py"],
        extra_dirs=[adapter_dir])
    # identical pre/post findings subtract to nothing (I-2)
    assert findings == []
    assert statuses["fake-scan"].startswith("ok")


def test_shipped_adapters_load_clean():
    loaded = adapters.load()
    assert {"ruff", "vulture", "deptry", "jscpd", "antislop",
            "slop-scan"} <= set(loaded)
    assert "sloppylint" not in loaded  # no verified machine-readable output
    for adapter in loaded.values():
        assert adapter["parser"] in adapters.PARSERS
        assert adapter["blocking"] == {}  # advisory until an ADR says else
