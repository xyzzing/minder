#!/usr/bin/env python3
"""minder reflex — advisory micro-classification client (the Jev-class tier).

Asks the optional local sidecar (reflex-verdict/minder_sidecar.py, CPU,
openJev-verdict-2.0) to classify a failure by root cause and returns a
digest hint line. Purely advisory: it enriches digests, never governs
budgets or blocks (Law #6). Fail-open: sidecar down / low confidence /
disabled config → empty string, zero impact on the hot path.

Config (minder.json): {"reflex": {"enabled": true,
                                   "url": "http://127.0.0.1:8391/classify",
                                   "threshold": 0.85}}
Gate evidence (2026-09-20, 66 labeled minder-shaped failures): full 57.6%
vs 22.7% majority; selective@0.85 79.3% at 43.9% coverage; logic_bug 14/14,
env_failure 3/15 (weakest class — hint text is hedged accordingly).
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minder  # noqa: E402

DEFAULTS = {"enabled": False, "threshold": 0.85,
            "url": "http://127.0.0.1:8391/classify"}

LABELS = {
    "env_failure": "Environment or infrastructure failure: missing "
                   "dependency, permissions, disk, network, service or "
                   "driver problem outside the code under test",
    "mock_outdated": "Outdated mock, fixture or test double: mock "
                     "expectations, stale fixture, patched target moved",
    "route_mismatch": "API route or contract mismatch: 404, endpoint moved, "
                      "wrong URL, response shape or middleware mismatch",
    "logic_bug": "Logic bug in the code under test: wrong assertion value, "
                 "type error, index error, off-by-one, ordering defect",
    "flaky": "Flaky or timing-dependent test: intermittent, load sensitive, "
             "passes on retry",
    "config_error": "Configuration or schema error: invalid value, missing "
                    "required field, validation rejecting a setting",
}

GUIDANCE = {
    "logic_bug": "the failure is inside the code under test — re-read the "
                 "exact assertion inputs before changing anything else",
    "flaky": "the test is timing/load-sensitive — before 'fixing' logic, "
             "run it in isolation once to confirm it reproduces",
    "route_mismatch": "endpoint or contract mismatch — verify the actual "
                      "route/response shape with one direct curl before "
                      "editing handlers",
    "env_failure": "looks environmental — check the dependency/service "
                   "exists and is running before touching code",
    "mock_outdated": "the test double is stale — update the mock/fixture to "
                     "the current interface instead of changing production "
                     "code",
    "config_error": "configuration or schema mismatch — diff the config "
                    "against what validation actually requires",
}


def _post(url, payload, timeout=5):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def classify(text, cfg=None, post=None):
    """→ (choice, confidence) or (None, 0.0) — never raises."""
    rcfg = dict(DEFAULTS)
    rcfg.update((cfg or minder.cfg()).get("reflex") or {})
    if not rcfg.get("enabled"):
        return None, 0.0
    post = post or _post
    try:
        st, resp = post(rcfg["url"], {"text": text, "labels": LABELS})
    except Exception:
        return None, 0.0
    if st != 200 or not isinstance(resp, dict) or "choice" not in resp:
        return None, 0.0
    return resp.get("choice"), float(resp.get("confidence") or 0.0)


def digest_hint(text, cfg=None, post=None):
    """One-line advisory hint for an L1 digest, '' when not applicable."""
    c = cfg or minder.cfg()
    rcfg = dict(DEFAULTS)
    rcfg.update(c.get("reflex") or {})
    choice, conf = classify(text, c, post)
    if not choice or conf < rcfg.get("threshold", DEFAULTS["threshold"]):
        return ""
    guide = GUIDANCE.get(choice)
    if not guide:
        return ""
    return f"[reflex {conf:.2f}] probable {choice}: {guide}."
