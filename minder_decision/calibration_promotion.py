"""What a promotion pins, where it lives, and when it stops being true
(issue #36).

A promoted scale is a claim about a model, a suite and a comparison. All
three can move underneath it: the owner relabels the eval set, the
bundle is re-exported, the operator re-pins a baseline. The record names
each pinned fact so `record_drift` can say which one moved and the
operator can see that the number they are running no longer describes
anything that exists.

The record lives under `benchmarks_root()`, next to the pinned baselines,
because both are operator state. Locations inside it are stored relative
to that root: `MINDER_BENCHMARKS_DIR` is per-invocation, and an absolute
path makes every drift check report a baseline that was never lost.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .calibration_gate import default_baseline_path_for
from .difficulty_eval import SUITE_ID, load_cases, suite_fingerprint

# The confidence floor the scale is fitted against. Same value the
# router reads (minder.py DEFAULTS), restated here because a pinned
# record that does not name the floor it was chosen for is a number
# without a question.
DEFAULT_CONFIDENCE_FLOOR = 0.7

CONFIDENCE_SOURCE = "distribution-margin"
# under benchmarks_root(), which the operator may relocate; naming the
# dir twice (MINDER_BENCHMARKS_DIR ending in benchmarks/) is how a
# pinned record ends up somewhere nobody looks
PROMOTIONS_DIR = "promotions"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


def bundle_revision(bundle_path):
    """The installed artifact's identity: base revision plus the
    strategy and its parameters. A re-export changes it, which is
    exactly when a pinned scale stops describing the installed model."""
    path = Path(bundle_path) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    core = {"base_revision": manifest.get("base_revision"),
            "strategy": manifest.get("strategy"),
            "config": manifest.get("specialization_config"),
            "format_version": manifest.get("format_version")}
    return _digest(core)


def promotion_record(*, scale, bundle_path, cases_path,
                     fit=None, gate=None, now=None,
                     confidence_source=CONFIDENCE_SOURCE, baseline=None):
    """What a promotion actually pinned. Every key here is a thing that
    can move under the pin, and `record_drift` names the ones that did."""
    now = now or datetime.now(timezone.utc)
    pinned = default_baseline_path_for()
    # Store where the report lives inside the benchmarks root, not where
    # it happened to be on disk for this run. `MINDER_BENCHMARKS_DIR` is
    # per-invocation, so an absolute path makes the drift check report a
    # moved baseline every time the root moves - and the operator re-pins
    # a denominator that was never lost.
    pinned = _baseline_location(pinned)
    return {
        "record_version": 1,
        "suite_id": SUITE_ID,
        "suite_fingerprint": suite_fingerprint(cases_path),
        "label_digest": load_cases(cases_path)[2],
        "bundle_path": str(bundle_path),
        "bundle_revision": bundle_revision(bundle_path),
        "margin_scale": scale,
        "confidence_source": confidence_source,
        "confidence_floor": (fit or {}).get("floor", DEFAULT_CONFIDENCE_FLOOR),
        "fit": fit,
        "gate": None if gate is None else {
            "verdict": gate["verdict"], "reasons": gate["reasons"]},
        # the report the verdict was computed against. The gate refuses
        # to run without a pinned baseline, so a record that did not
        # name it would claim a comparison nothing can re-check once
        # the operator re-pins a baseline over that file
        "baseline_path": pinned,
        "baseline_fingerprint": (baseline or {}).get("suite_fingerprint"),
        # the scale the baseline was replayed at. The gate refuses a
        # baseline whose scale is not the provider's, so pinning it here
        # is what lets a later check tell "the baseline was replaced"
        # from "the baseline was replaced with one at another scale"
        "baseline_scale": (baseline or {}).get(
            "difficulty_detail", {}).get("margin_scale"),
        # where the pinned digests came from, so a later check can
        # recompute them without guessing at the suite layout
        "cases_path": str(cases_path),
        "promoted_at": now.isoformat(),
    }


def promotion_path(suite_id=SUITE_ID):
    """Where a pinned promotion lives, next to the pinned baselines."""
    from minder_op.benchmark import benchmarks_root
    return benchmarks_root() / PROMOTIONS_DIR / f"{suite_id}.json"


def read_promotion(suite_id=SUITE_ID):
    path = promotion_path(suite_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_promotion(record, path=None):
    """Atomic write; the previous record is kept as `.bak` so a bad
    promotion is one move away from the last good one (I2)."""
    path = path or promotion_path(record["suite_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.parent.joinpath(path.name + ".bak").write_text(
            path.read_text(encoding="utf-8"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
    tmp.replace(path)
    return path


def record_drift(record, *, cases_path, bundle_path, baseline_path=None):
    """Which pinned facts no longer hold. A relabel moves the label
    digest and with it the suite fingerprint; a re-export moves the
    bundle revision; re-pinning a baseline replaces the denominator the
    verdict was computed against. A promotion whose facts moved
    describes a model, a suite or a comparison that no longer exists, so
    the pin stops meaning anything."""
    drift = []
    try:
        digest = load_cases(cases_path)[2]
    except (OSError, ValueError):
        digest = None
    if digest is not None and record.get("label_digest") != digest:
        drift.append("label_digest")
    try:
        fingerprint = suite_fingerprint(cases_path)
    except (OSError, ValueError):
        fingerprint = None
    if fingerprint is not None and record.get("suite_fingerprint") != \
            fingerprint:
        drift.append("suite_fingerprint")
    try:
        revision = bundle_revision(bundle_path)
    except (OSError, ValueError):
        revision = None
    if revision is not None and record.get("bundle_revision") != revision:
        drift.append("bundle_revision")
    # The baseline is a file, not a digest the record keeps a copy of:
    # `benchmark baseline create --yes` overwrites it, and a record
    # gated against the old report then claims a comparison nobody can
    # rerun. An absent or unreadable file counts the same as a moved one.
    if record.get("baseline_fingerprint") is None:
        # Nothing was pinned, so there is no denominator to have moved.
        # The loop refuses to gate without a baseline, so this is an
        # older or hand-edited record: saying so is a finding, folding it
        # into baseline_fingerprint would send the operator off to re-pin
        # a report the record never named.
        drift.append("baseline_unpinned")
        return drift
    pinned = baseline_path or record.get("baseline_path")
    report = _pinned_baseline_report(pinned)
    if record.get("baseline_fingerprint") != (report or {}).get(
            "suite_fingerprint"):
        drift.append("baseline_fingerprint")
        return drift
    # The fingerprint says the denominator is the same suite; only the
    # scale says whether it is the same comparison. A baseline re-pinned
    # at another margin scale keeps the fingerprint and voids the verdict.
    if record.get("baseline_scale") != \
            (report.get("difficulty_detail") or {}).get("margin_scale"):
        drift.append("baseline_scale")
    return drift


def _baseline_location(path):
    """The baseline's location relative to the benchmarks root, or the
    path itself when it is not under that root."""
    from minder_op.benchmark import benchmarks_root
    try:
        return str(Path(path).relative_to(benchmarks_root()))
    except (ValueError, OSError):
        return str(path)


def _resolve_baseline_location(location):
    """A recorded baseline location as a readable path. An absolute path
    from an older record still resolves; a relative one resolves against
    the root in force, which is what makes it survive a moved root."""
    if not location:
        return None
    path = Path(location)
    if path.is_absolute():
        return path
    from minder_op.benchmark import benchmarks_root
    candidate = benchmarks_root() / path
    if candidate.is_file():
        return candidate
    if path.is_file():
        return path
    return candidate


def _pinned_baseline_report(path):
    """The pinned report, or None when it is absent or invalid. A drift
    check runs inside doctor, so it degrades to a finding rather than a
    traceback."""
    if not path:
        return None
    from minder_op.benchmark import BenchmarkError, read_report
    try:
        return read_report(_resolve_baseline_location(path))
    except (BenchmarkError, OSError, ValueError):
        return None
