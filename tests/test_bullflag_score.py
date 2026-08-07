"""Tests for engine-v2 stage 4b score (#179): monotonicity, contribution sum, inf handling."""

from __future__ import annotations

import pytest

from small_cap_stack.bullflag import Segment, extract, score, tokenize
from tests.support import bar as _bar


def _fv(flag_low: float, *, cons_vol: float = 800.0):  # noqa: ANN202
    # A 1-bar pole (peak 6.5) then a flag; deeper flag_low = deeper retracement.
    bars = [
        _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
        _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
        _bar(2, 6.0, 6.1, flag_low, 5.7, vol=cons_vol),
    ]
    return extract(bars, _seg_of(bars))


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


def test_contributions_sum_to_score() -> None:
    s, contrib = score(_fv(5.6))
    assert s == pytest.approx(sum(contrib.values()))
    assert 0.0 <= s <= 1.0


def test_shallower_retracement_scores_higher() -> None:
    shallow, _ = score(_fv(5.9))  # retracement ~0.32
    deep, _ = score(_fv(5.0))  # retracement ~0.79
    assert shallow > deep


def test_vol_ratio_inf_is_clamped_not_infinite() -> None:
    s, contrib = score(_fv(5.6, cons_vol=0.0))  # cons vol 0 -> vol_ratio inf
    assert s <= 1.0
    assert contrib["vol_ratio"] == pytest.approx(0.13)  # full weight, clamped to 1.0


def test_custom_weights_are_used() -> None:
    s, contrib = score(_fv(5.6), weights={"retracement_shallow": 1.0})
    assert {k for k, v in contrib.items() if v != 0.0} <= {"retracement_shallow"}
    assert s == pytest.approx(contrib["retracement_shallow"])
