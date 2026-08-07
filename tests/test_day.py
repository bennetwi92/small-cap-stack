"""Tests for the engine-v2 full-day detector (#211 stage 3): detect_day.

Synthetic-bar mechanics — the clean-pass path, no-pole, the peak_green reject, the appearance and
staleness gates, exhaustion, and the entry levels. The end-to-end fidelity to the 25 reviewed
opportunities is pinned separately by the graduated fixtures (stage 4).
"""

from __future__ import annotations

from datetime import time, timedelta

from small_cap_stack.bullflag import DaySetup, detect_day
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from tests.support import T0 as _T0


def _b(i: int, o: float, h: float, low: float, c: float, v: float = 100_000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=v)


# base -> green thrust peak -> lower-high pullback -> breakout
_PASS = [
    _b(0, 9.90, 10.00, 9.90, 9.95, 50_000),
    _b(1, 10.50, 11.00, 10.50, 10.95, 300_000),
    _b(2, 10.85, 10.90, 10.70, 10.75, 100_000),
    _b(3, 10.80, 10.92, 10.78, 10.90, 120_000),
]


def test_clean_pass() -> None:
    d = detect_day(_PASS)
    assert isinstance(d, DaySetup)
    assert d.passed is True
    assert d.trigger_idx == 3
    assert (d.segment.base_idx, d.segment.peak_idx, d.segment.cons_end_idx) == (0, 1, 2)
    assert (d.segment.pole_len, d.segment.cons_len) == (1, 1)
    assert d.cycle_num == 1 and d.exhausted is False
    assert d.takeable is True


def test_entry_levels_and_stop() -> None:
    d = detect_day(_PASS)
    assert d is not None
    assert d.breakout_level == 10.90  # last consolidation candle high
    assert d.entry_trigger == 10.91  # +1 tick
    assert d.entry_fill == 10.93  # +3 ticks
    assert d.stop == 10.70  # consolidation low


def test_no_pole_returns_none() -> None:
    falling = [_b(0, 10, 10, 9.5, 9.6), _b(1, 9.6, 9.6, 9, 9.1), _b(2, 9.1, 9.1, 8.5, 8.6)]
    assert detect_day(falling) is None


def test_red_peak_is_found_but_fails_peak_green() -> None:
    red = [
        _b(0, 9.90, 10.00, 9.90, 9.95),
        _b(1, 11.00, 11.00, 10.50, 10.55, 300_000),  # higher high but RED (close < open)
        _b(2, 10.85, 10.90, 10.70, 10.75, 100_000),
        _b(3, 10.80, 10.92, 10.78, 10.90),
    ]
    d = detect_day(red)
    assert d is not None  # identify-and-reject: the shape IS found...
    assert d.passed is False  # ...but rejected
    assert "peak_green" in {g.name for g in d.gates if not g.passed}
    assert d.takeable is False


def test_appearance_gate_skips_an_entry_before_first_hit() -> None:
    # first_hit after the only takeable entry -> no cycle is takeable -> no setup.
    after = _PASS[3].start + timedelta(minutes=1)
    assert detect_day(_PASS, first_hit=after) is None


def test_staleness_nulls_the_trigger_but_keeps_the_shape() -> None:
    # Seen at bar 0; the breakout at bar 3 is +15 min, past a 10-min staleness bound -> no trigger,
    # but the shape (and its gate verdict) is still returned.
    d = detect_day(_PASS, first_hit=_PASS[0].start, staleness_min=10)
    assert d is not None
    assert d.trigger_idx is None  # faded
    assert d.passed is True  # gates still evaluated
    assert d.takeable is False  # no entry -> not takeable


def test_staleness_bound_is_inclusive_at_the_cutoff() -> None:
    """A break at EXACTLY ``staleness_min`` is fresh; one minute of slack later it is faded (#586).

    Staleness is a duration ("the break came within N minutes of the scan"), not a deadline, so the
    Nth minute counts — unlike the selection window's strict ``< window_end`` cutoff. The breakout
    bar of ``_PASS`` opens +15 min after bar 0, which is the boundary under ``staleness_min=15``.
    """
    on_the_bound = detect_day(_PASS, first_hit=_PASS[0].start, staleness_min=15)
    assert on_the_bound is not None
    assert on_the_bound.trigger_idx == 3  # exactly +15 min -> still fresh
    assert on_the_bound.takeable is True

    just_past = detect_day(_PASS, first_hit=_PASS[0].start, staleness_min=14)
    assert just_past is not None
    assert just_past.trigger_idx is None  # +15 min against a 14-min bound -> faded
    assert just_past.takeable is False


# three back-to-back green-thrust pumps; appearance forces the target onto the 3rd -> exhausted
_EXH = [
    _b(0, 9.90, 10.00, 9.90, 9.95, 50_000),
    _b(1, 10.00, 11.00, 10.00, 10.95, 300_000),  # pump 1
    _b(2, 10.45, 10.50, 10.20, 10.30, 80_000),  # fade 1
    _b(3, 10.50, 11.50, 10.50, 11.45, 300_000),  # pump 2
    _b(4, 10.95, 11.00, 10.70, 10.80, 80_000),  # fade 2
    _b(5, 11.00, 12.00, 11.00, 11.95, 300_000),  # pump 3 (target pole)
    _b(6, 11.45, 11.50, 11.20, 11.30, 80_000),  # fade 3 (target consolidation)
    _b(7, 11.50, 12.05, 11.50, 12.00, 300_000),  # target breakout / next pole
]


def test_exhaustion_counts_contiguous_prior_pumps() -> None:
    # Seen only at bar 6, so the earlier pumps' entries aren't takeable and the target re-anchors to
    # the 3rd pump (base 4, peak 5) -> two prior contiguous cycles -> cycle 3 -> EXHAUSTED.
    d = detect_day(_EXH, first_hit=_EXH[6].start)
    assert d is not None
    assert (d.segment.base_idx, d.segment.peak_idx) == (4, 5)
    assert d.cycle_num == 3
    assert d.exhausted is True
    assert d.total_significant_cycles >= 3


def test_exhaustion_cap_is_respected() -> None:
    # Same shape, but a higher cap -> the 3rd cycle is no longer "exhausted".
    d = detect_day(_EXH, first_hit=_EXH[6].start, exhaustion_cap=5)
    assert d is not None and d.cycle_num == 3 and d.exhausted is False


# --- selection: takeable vs merely well-formed (#567) ---------------------------------------
#
# The price band and the trigger-time window moved here from the paper book, where the
# `portfolio_` prefix implied they were execution rules. They bite on `takeable` and deliberately
# NOT on `passed`: a $1.50 name or an 11:00 break can be a textbook flag we simply don't select,
# and Phase 1 needs it to stay visible and scoreable rather than reported as malformed.
#
# `_PASS` triggers at index 3 = T0 + 15 min = 10:15 ET, and its entry_fill is 10.93.


def test_price_band_rejects_takeable_but_leaves_the_shape_passing() -> None:
    out_of_band = detect_day(_PASS, price_min=20.0, price_max=50.0)
    assert out_of_band is not None
    assert out_of_band.passed is True  # the flag is still well-formed...
    assert out_of_band.in_price_band is False  # ...it is simply not one we select
    assert out_of_band.takeable is False
    assert [g.name for g in out_of_band.gates if not g.passed] == []


def test_price_band_accepts_a_name_inside_it() -> None:
    d = detect_day(_PASS, price_min=2.0, price_max=20.0)
    assert d is not None and d.in_price_band is True and d.takeable is True


def test_price_band_bounds_are_inclusive() -> None:
    fill = 10.93  # _PASS's entry_fill
    assert detect_day(_PASS, price_min=fill, price_max=fill).in_price_band is True  # type: ignore[union-attr]
    assert detect_day(_PASS, price_min=fill + 0.01).in_price_band is False  # type: ignore[union-attr]
    assert detect_day(_PASS, price_max=fill - 0.01).in_price_band is False  # type: ignore[union-attr]


def test_window_rejects_a_trigger_outside_it() -> None:
    # _PASS triggers at 10:15 ET. A 05:30-09:15 selection window excludes it.
    d = detect_day(_PASS, window_start=time(5, 30), window_end=time(9, 15))
    assert d is not None
    assert d.passed is True  # again: well-formed, just not selected
    assert d.in_window is False
    assert d.takeable is False


def test_window_floor_is_inclusive_and_cutoff_is_strict() -> None:
    trigger_et = _PASS[3].start.astimezone(ET).time()
    assert detect_day(_PASS, window_start=trigger_et, window_end=time(23, 59)).in_window is True  # type: ignore[union-attr]
    assert detect_day(_PASS, window_start=time(0, 0), window_end=trigger_et).in_window is False  # type: ignore[union-attr]


def test_selection_defaults_are_permissive() -> None:
    """A caller configuring neither selects on shape alone — spikes and unit tests need that."""
    d = detect_day(_PASS)
    assert d is not None and d.in_price_band is True and d.in_window is True
