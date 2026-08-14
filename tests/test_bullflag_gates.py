"""Tests for engine-v2 stage 4a gates (#179): each gate's boundary + accept/reject aggregation."""

from __future__ import annotations

from small_cap_stack.bullflag import Segment, evaluate, extract, tokenize
from small_cap_stack.bullflag.features import FeatureVector
from small_cap_stack.bullflag.gates import passed
from tests.support import bar as _bar

# Clean setup: retracement ~0.47, wick 0.1, pole_height ~0.41, peak vol > cons vol.
_BARS = [
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=800),
]
_DEFAULTS = {
    "max_cons": 4,
    "min_pole_pct": 0.02,
    "max_retracement": 0.50,
}


def _seg_of(bars, *, peak: int = 1):  # noqa: ANN001, ANN202
    """The `Segment` for a single-bar pole at `peak`, consolidating to the last bar.

    Explicit since #518 deleted `segment_at_end`: these tests are about the stages after
    segmentation, so the shape is an input to state, not a thing to derive.
    """
    return Segment(
        base_idx=peak - 1,
        peak_idx=peak,
        cons_end_idx=len(bars) - 1,
        tokens=tuple(tokenize(bars, eps=0.01)[peak - 1 :]),
        pole_len=1,
        cons_len=len(bars) - 1 - peak,
    )


def _fv() -> FeatureVector:
    return extract(_BARS, _seg_of(_BARS))


def test_clean_setup_passes_all() -> None:
    gates = evaluate(_fv(), **_DEFAULTS)
    assert passed(gates) is True
    # Five gates, down from eight — `pole_len`, `wick_peak` and `cons_holds_base` were measured
    # against the 197-session record and removed (§D-44, #690).
    assert {g.name for g in gates} == {
        "cons_len",
        "vol_peak_gt_cons",
        "peak_green",
        "pole_height",
        "cons_retracement",
    }


def test_pole_height_gate_boundary() -> None:
    # pole_height ~0.41; a 0.99 floor rejects it (the pole_height gate specifically).
    gates = evaluate(_fv(), **{**_DEFAULTS, "min_pole_pct": 0.99})
    assert passed(gates) is False
    assert next(g for g in gates if g.name == "pole_height").passed is False


def test_retracement_gate_boundary() -> None:
    assert passed(evaluate(_fv(), **{**_DEFAULTS, "max_retracement": 0.10})) is False


def test_there_is_no_wick_gate() -> None:
    """The pole-wick reject (§D-13) was removed in §D-44 — it rejected the better setups.

    Pinned as an absence rather than deleted: "too wicky -> no trade" came from the trader's own
    notes and reads as an obviously-correct rule, so a future reader is likely to re-add it. Measure
    it first — across 1,433 rejections the setups it dropped outperformed the ones it kept in all
    three periods tested.
    """
    assert "wick_peak" not in {g.name for g in evaluate(_fv(), **_DEFAULTS)}


def test_there_is_no_window_gate() -> None:
    """The trading window is a *selection* rule (`day.py`, #567), so gating it here too would
    double-gate it. `evaluate` used to take an off-by-default `gate_window=` for that; #518 deleted
    it along with the end-anchored detector, its only other reader. `trigger_in_window` stays a
    scored feature."""
    assert not any(g.name == "loc_in_window" for g in evaluate(_fv(), **_DEFAULTS))
    assert extract(_BARS, _seg_of(_BARS)).trigger_in_window is True  # still a feature
