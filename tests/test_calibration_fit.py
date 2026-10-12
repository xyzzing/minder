"""The margin-scale fitter (issue #36).

The live install applies zero difficulty bands and the ledger says why:
831 `difficulty_skipped reason=below_confidence`. The floor is
`clip01(scale x margin) >= 0.7`, and the scale was a hand-picked
constant (2.0 for the base head, 20.0 for the specialization bundle,
chosen by eye against 34 rows). Nothing re-fits it when a bundle is
re-exported, so every export inherits a guess.

prd-v0.10 6.9.3 is the law here: fit on a calibration/fitting split,
score on the disjoint eval set, and refuse a fit whose corpus shares
cases with what scores it. These tests pin the fitter and that refusal.
"""
import pytest

# getattr, not a module-level import: the proven-red gate runs this file
# against pre-change code, where a missing symbol fails the whole file at
# import and reports no symbol instead of the assertion that shows the
# behaviour was absent.
calibration = getattr(
    __import__("minder_core", fromlist=["calibration"]), "calibration", None)


def _dist(hit, margin):
    """A closed-set distribution whose top-two margin is exactly
    `margin`, argmax `hit`."""
    other = "routine" if hit == "mechanical" else "mechanical"
    rest = (1.0 - margin) / 2
    return {hit: margin + rest, other: rest,
            "complex": 0.0, "expert_or_ambiguous": 0.0}


def _rows():
    """Margins chosen so the right answer is known by hand.

    A floor of 0.7 acts at margin >= 0.7/scale. scale 10 acts on the
    0.30/0.12/0.08 rows - all correct, selective accuracy 1.0 - and
    scale 20 adds the 0.05 row, which is wrong, pulling it to 3/4. So
    the best scale is the largest one that keeps the wrong row out.
    """
    return [
        {"id": "r1", "margin": 0.30, "correct": True,
         "probs": _dist("mechanical", 0.30), "gold": "mechanical"},
        {"id": "r2", "margin": 0.12, "correct": True,
         "probs": _dist("mechanical", 0.12), "gold": "mechanical"},
        {"id": "r3", "margin": 0.08, "correct": True,
         "probs": _dist("routine", 0.08), "gold": "routine"},
        {"id": "r4", "margin": 0.05, "correct": False,
         "probs": _dist("routine", 0.05), "gold": "mechanical"},
        {"id": "r5", "margin": 0.01, "correct": False,
         "probs": _dist("complex", 0.01), "gold": "mechanical"},
    ]


def test_fitter_refuses_a_scale_that_acts_on_nothing():
    """A scale whose floor acts on no row is not a calibration, it is a
    router switched off, and its selective accuracy is None, not 0."""
    assert calibration is not None
    fit = calibration.fit_margin_scale(_rows(), floor=0.7,
                                       scales=(0.1, 0.5, 1.0, 2.0))
    assert fit["scale"] is None
    assert fit["reason"] == "no_scale_acts"
    assert fit["scales_tried"] == 4


def test_fitter_picks_the_largest_scale_that_keeps_selective_accuracy():
    assert calibration is not None
    fit = calibration.fit_margin_scale(_rows(), floor=0.7,
                                       scales=(2.0, 10.0, 20.0))
    assert fit["scale"] == 10.0
    assert fit["acted"] == 3
    assert fit["coverage"] == pytest.approx(0.6)
    assert fit["selective_accuracy"] == pytest.approx(1.0)
    assert fit["reason"] == "ok"


def test_fitter_breaks_an_accuracy_tie_by_coverage_then_ece():
    """Two scales with the same selective accuracy: the one acting on
    more cases is the better router, because coverage is exactly what
    the live floor is short of. scale 7 acts on one row, 10 on both."""
    assert calibration is not None
    rows = [{"id": "a", "margin": 0.30, "correct": True,
             "probs": {"mechanical": 0.6, "routine": 0.3,
                       "complex": 0.05, "expert_or_ambiguous": 0.05},
             "gold": "mechanical"},
            {"id": "b", "margin": 0.10, "correct": True,
             "probs": {"mechanical": 0.35, "routine": 0.25,
                       "complex": 0.3, "expert_or_ambiguous": 0.1},
             "gold": "mechanical"}]
    fit = calibration.fit_margin_scale(rows, floor=0.7,
                                       scales=(7.0, 10.0, 20.0))
    assert fit["scale"] == 10.0
    assert fit["acted"] == 2


def test_fitter_reports_the_calibration_error_it_chose_against():
    assert calibration is not None
    fit = calibration.fit_margin_scale(_rows(), floor=0.7,
                                       scales=(2.0, 10.0, 20.0))
    assert isinstance(fit["ece"], float)
    assert 0.0 <= fit["ece"] <= 1.0


def test_fitter_rejects_a_row_without_a_margin_or_a_correct_flag():
    """The fitter's denominator is the row set. A silently skipped row
    would let a scale be fitted on fewer cases than the record claims."""
    assert calibration is not None
    with pytest.raises(ValueError):
        calibration.fit_margin_scale([{"id": "x", "gold": "mechanical"}],
                                     floor=0.7)


def test_default_scale_grid_is_a_constant_not_an_inline_literal():
    assert calibration is not None
    grid = calibration.DEFAULT_MARGIN_SCALES
    assert len(grid) >= 8
    assert min(grid) > 0.0
    assert max(grid) >= 20.0
