"""Direct unit tests for the shared bar primitives (#296).

These five functions are the vocabulary the whole detection pipeline is written in — segment,
features and cycles all build on them — so they are pinned directly here rather than only
incidentally through the pipeline's tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag.primitives import (
    classify,
    find_pole_peak,
    is_big_green,
    non_increasing,
    upper_wick_frac,
)
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# --- classify ---------------------------------------------------------------------------------


def test_classify_green_red_flat() -> None:
    assert classify(_bar(0, 1.0, 2.0, 0.9, 1.5)) == "green"
    assert classify(_bar(0, 1.5, 2.0, 0.9, 1.0)) == "red"
    assert classify(_bar(0, 1.0, 2.0, 0.9, 1.0)) == "flat"  # doji: close == open


# --- is_big_green -----------------------------------------------------------------------------


def test_is_big_green_requires_green_and_half_body() -> None:
    # body 1.0 of range 1.0 -> full-bodied green
    assert is_big_green(_bar(0, 1.0, 2.0, 1.0, 2.0)) is True
    # body 0.5 of range 1.0 -> exactly at the boundary, inclusive
    assert is_big_green(_bar(0, 1.0, 2.0, 1.0, 1.5)) is True
    # body 0.4 of range 1.0 -> too small
    assert is_big_green(_bar(0, 1.0, 2.0, 1.0, 1.4)) is False
    # a full-bodied RED bar is not big green
    assert is_big_green(_bar(0, 2.0, 2.0, 1.0, 1.0)) is False


def test_is_big_green_zero_range_is_not_big_green() -> None:
    """A zero-range bar has no body to measure — must not divide by zero."""
    assert is_big_green(_bar(0, 1.0, 1.0, 1.0, 1.0)) is False


# --- upper_wick_frac --------------------------------------------------------------------------


def test_upper_wick_frac_closed_at_high_has_no_wick() -> None:
    assert upper_wick_frac(_bar(0, 1.0, 2.0, 1.0, 2.0)) == 0.0


def test_upper_wick_frac_all_wick() -> None:
    # opens and closes at the low, high far above -> the whole range is upper wick
    assert upper_wick_frac(_bar(0, 1.0, 2.0, 1.0, 1.0)) == 1.0


def test_upper_wick_frac_half() -> None:
    # range 1.0, body top at 1.5 -> upper wick 0.5
    assert upper_wick_frac(_bar(0, 1.0, 2.0, 1.0, 1.5)) == 0.5


def test_upper_wick_frac_measures_from_the_body_top_on_a_red_bar() -> None:
    """The wick runs from max(open, close) — on a red bar that is the OPEN, not the close."""
    assert upper_wick_frac(_bar(0, 1.5, 2.0, 1.0, 1.2)) == 0.5


def test_upper_wick_frac_zero_range_bar_has_no_wick() -> None:
    """A zero-range bar must return 0.0, not raise ZeroDivisionError."""
    assert upper_wick_frac(_bar(0, 1.0, 1.0, 1.0, 1.0)) == 0.0


# --- non_increasing ---------------------------------------------------------------------------


def test_non_increasing() -> None:
    assert non_increasing([3.0, 2.0, 1.0]) is True
    assert non_increasing([3.0, 3.0, 1.0]) is True  # flat steps are non-increasing
    assert non_increasing([1.0, 2.0]) is False
    assert non_increasing([5.0]) is True  # a single value is trivially non-increasing
    assert non_increasing([]) is True  # vacuously true


# --- find_pole_peak ---------------------------------------------------------------------------


def test_find_pole_peak_picks_the_dominant_high() -> None:
    bars = [
        _bar(0, 1.0, 1.1, 0.9, 1.0),
        _bar(1, 1.0, 2.0, 0.9, 1.0),  # <- dominant high
        _bar(2, 1.0, 1.5, 0.9, 1.0),
        _bar(3, 1.0, 1.3, 0.9, 1.0),
    ]
    assert find_pole_peak(bars, 4) == 1


def test_find_pole_peak_ignores_a_mid_pullback_uptick() -> None:
    """The #163 fix: a small up-tick inside a deeper pullback must not be taken as the peak."""
    bars = [
        _bar(0, 1.0, 1.1, 0.9, 1.0),
        _bar(1, 1.0, 3.0, 0.9, 1.0),  # <- the real peak
        _bar(2, 1.0, 2.0, 0.9, 1.0),
        _bar(3, 1.0, 2.4, 0.9, 1.0),  # an up-tick, but still below the real peak
        _bar(4, 1.0, 2.1, 0.9, 1.0),
    ]
    assert find_pole_peak(bars, 4) == 1


def test_find_pole_peak_returns_none_when_still_extending() -> None:
    """A new high on the LAST bar means the move hasn't pulled back yet — no completed shape."""
    bars = [
        _bar(0, 1.0, 1.1, 0.9, 1.0),
        _bar(1, 1.0, 1.5, 0.9, 1.0),
        _bar(2, 1.0, 2.0, 0.9, 1.0),  # new high on the last bar
    ]
    assert find_pole_peak(bars, 4) is None


def test_find_pole_peak_ties_resolve_to_the_earliest() -> None:
    bars = [
        _bar(0, 1.0, 1.0, 0.9, 1.0),
        _bar(1, 1.0, 2.0, 0.9, 1.0),  # <- first of the tied highs
        _bar(2, 1.0, 2.0, 0.9, 1.0),
        _bar(3, 1.0, 1.5, 0.9, 1.0),
    ]
    assert find_pole_peak(bars, 4) == 1


def test_find_pole_peak_never_selects_index_zero() -> None:
    """The peak needs a predecessor to have risen from, so the search floor is 1."""
    bars = [
        _bar(0, 1.0, 9.0, 0.9, 1.0),  # highest, but has no predecessor
        _bar(1, 1.0, 2.0, 0.9, 1.0),
        _bar(2, 1.0, 1.5, 0.9, 1.0),
    ]
    peak = find_pole_peak(bars, 4)
    assert peak is not None and peak >= 1


def test_find_pole_peak_window_is_bounded_by_max_cons() -> None:
    """Only the trailing max_cons bars (plus the peak) are searched, so an older high is ignored."""
    bars = [
        _bar(0, 1.0, 1.0, 0.9, 1.0),
        _bar(1, 1.0, 9.0, 0.9, 1.0),  # an old high, outside a max_cons=2 window
        _bar(2, 1.0, 3.0, 0.9, 1.0),
        _bar(3, 1.0, 2.0, 0.9, 1.0),
        _bar(4, 1.0, 1.5, 0.9, 1.0),
    ]
    # lo = max(1, 5 - 1 - 2) = 2 -> the search starts at index 2, so the old high is out of scope
    assert find_pole_peak(bars, 2) == 2
