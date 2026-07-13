"""Tests for the engine-v2 full-day detector (#211 stage 3): detect_day.

Synthetic-bar mechanics — the clean-pass path, no-pole, the peak_green reject, the appearance and
staleness gates, exhaustion, and the entry levels. The end-to-end fidelity to the 25 reviewed
opportunities is pinned separately by the graduated fixtures (stage 4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import DaySetup, detect_day
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)  # 10:00 ET (in the strategy window)


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
