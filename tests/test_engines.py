"""Engine registry (issue #3): active-engine resolution, per-engine caps
paths, and proxy hot-switch without a restart."""
import json
import os

import pytest

import minder
import proxy


@pytest.fixture
def config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "minder.json"
    monkeypatch.setattr(minder, "CFG_PATH", cfg_path)
    return cfg_path


def write_config(cfg_path, data):
    cfg_path.write_text(json.dumps(data))
    # the proxy re-resolves on a (mtime_ns, size) stamp; make the change
    # visible even when both writes land in the same filesystem tick
    st = cfg_path.stat()
    os.utime(cfg_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def test_implicit_engine_from_env(monkeypatch):
    monkeypatch.setenv("MINDER_UPSTREAM", "http://127.0.0.1:9090")
    engines, active = minder.engine_registry({"engines": {}})
    assert active == "llama"
    assert engines["llama"]["upstream"] == "http://127.0.0.1:9090"
    assert engines["llama"]["unit"] is None
    assert minder.engine_upstream({"engines": {}}) == "http://127.0.0.1:9090"


def test_registry_resolves_active_engine(config):
    write_config(config, {
        "engines": {
            "llama": {"upstream": "http://127.0.0.1:8080",
                      "unit": "llama-model@qwen27b"},
            "strata": {"upstream": "http://127.0.0.1:8081/",
                       "unit": "strata-hip"},
        },
        "active_engine": "strata",
    })
    engines, active = minder.engine_registry()
    assert active == "strata"
    assert engines["strata"]["unit"] == "strata-hip"
    # trailing slash normalized so request paths cannot double it
    assert engines["strata"]["upstream"] == "http://127.0.0.1:8081"
    assert minder.engine_upstream() == "http://127.0.0.1:8081"


def test_registry_invalid_active_falls_back(config):
    write_config(config, {
        "engines": {"llama": {"upstream": "http://127.0.0.1:8080"}},
        "active_engine": "gone",
    })
    _, active = minder.engine_registry()
    assert active == "llama"


def test_registry_skips_entries_without_upstream(config):
    write_config(config, {
        "engines": {"broken": {"unit": "x"},
                    "llama": {"upstream": "http://127.0.0.1:8080"}},
        "active_engine": "broken",
    })
    engines, active = minder.engine_registry()
    assert set(engines) == {"llama"}
    assert active == "llama"


def test_caps_path_legacy_when_no_registry():
    assert minder.caps_path() == minder.CAPS_PATH


def test_caps_path_per_engine_when_registry(config):
    write_config(config, {
        "engines": {"llama": {"upstream": "http://127.0.0.1:8080"},
                    "strata": {"upstream": "http://127.0.0.1:8081"}},
        "active_engine": "strata",
    })
    p = minder.caps_path()
    assert p.name == "model_caps.strata.json"
    assert p.parent == minder.CAPS_PATH.parent


def test_proxy_hot_switch_without_restart(config, monkeypatch):
    write_config(config, {
        "engines": {"a": {"upstream": "http://127.0.0.1:7001"},
                    "b": {"upstream": "http://127.0.0.1:7002"}},
        "active_engine": "a",
    })
    monkeypatch.setattr(proxy, "_engine_state", {"stamp": None, "fp": None})
    proxy.refresh_upstream()
    assert proxy.UPSTREAM == "http://127.0.0.1:7001"

    write_config(config, {
        "engines": {"a": {"upstream": "http://127.0.0.1:7001"},
                    "b": {"upstream": "http://127.0.0.1:7002"}},
        "active_engine": "b",
    })
    proxy.refresh_upstream()
    assert proxy.UPSTREAM == "http://127.0.0.1:7002"


def test_proxy_caps_follow_active_engine(config, tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "CAPS_PATH", tmp_path / "model_caps.json")
    caps_a = {"thinking": {"mechanism": "kwargs"}}
    caps_b = {"thinking": {"mechanism": "softswitch"}}
    (tmp_path / "model_caps.a.json").write_text(json.dumps(caps_a))
    (tmp_path / "model_caps.b.json").write_text(json.dumps(caps_b))
    write_config(config, {
        "engines": {"a": {"upstream": "http://127.0.0.1:7001"},
                    "b": {"upstream": "http://127.0.0.1:7002"}},
        "active_engine": "a",
    })
    monkeypatch.setattr(proxy, "_caps_cache", {"mtime": None, "caps": None})
    assert proxy.get_caps() == caps_a

    write_config(config, {
        "engines": {"a": {"upstream": "http://127.0.0.1:7001"},
                    "b": {"upstream": "http://127.0.0.1:7002"}},
        "active_engine": "b",
    })
    assert proxy.get_caps() == caps_b
