"""Tests for stage 3 feature extraction (#178): the six-area FeatureVector over a Segment."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from small_cap_stack.bullflag import extract, segment_at_end, tokenize, trailing_atr
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # 10:00 ET (EDT) -> inside the 04:00-11:59 window


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


def _hbar(i: int, high: float, vol: float = 1000.0) -> Bar:
    """A valid green bar parameterised by its high (for tests that only care about the high)."""
    return _bar(i, high - 0.3, high, high - 0.5, high - 0.1, vol)


# Clean 3-bar setup with valid OHLC: base (h5.8), pole peak (h6.5, vol 2000), red flag (h6.1).
_BARS = [
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=800),
]


def _seg_of(bars, *, max_pole=4, max_cons=4):
    seg = segment_at_end(bars, tokenize(bars, eps=0.01), max_pole=max_pole, max_cons=max_cons)
    assert seg is not None
    return seg


def test_full_feature_vector() -> None:
    fv = extract(_BARS, _seg_of(_BARS))
    # SHAPE
    assert (fv.pole_len, fv.cons_len) == (1, 1)
    assert fv.pole_strictness == 1.0
    assert fv.cons_strictness == 1.0
    assert fv.token_string == "HL"
    # VOL
    assert fv.peak_gt_cons is True
    assert fv.vol_ratio == pytest.approx(2.5)
    assert fv.cons_vol_reducing is True
    assert fv.pole_vol_concentration == pytest.approx(1.0)  # single-bar thrust -> all vol on peak
    # WICK
    assert fv.peak_upper_wick == pytest.approx(0.1)  # (6.5-6.4)/(6.5-5.5)
    assert fv.pole_has_big_green is True  # base bar: body 0.6 / range 1.2 = 0.5, green
    assert fv.pole_avg_body == pytest.approx((0.6 / 1.2 + 0.8 / 1.0) / 2)
    assert fv.cons_indecision == 0.0  # flag body 0.3/0.5 = 0.6, not a doji
    # POLE
    assert fv.pole_height_abs == pytest.approx(1.9)
    assert fv.pole_height_pct == pytest.approx(1.9 / 4.6)
    assert fv.pole_velocity == pytest.approx(1.9 / 4.6)  # /pole_len(1)
    assert fv.pole_extension_atr is None  # no atr passed
    # CONS
    assert fv.retracement == pytest.approx((6.5 - 5.6) / 1.9)
    assert fv.holds_base is True
    assert fv.cons_tightness == pytest.approx((6.1 - 5.6) / 6.5)
    assert fv.cons_drift_slope == 0.0  # single-bar flag
    # LOC
    assert fv.trigger_in_window is True
    assert fv.bars_before_scan is None


def test_retracement_matches_legacy_anchor() -> None:
    # Same anchors as the legacy detector: (pole_high - flag_low) / (pole_high - pole_base).
    fv = extract(_BARS, _seg_of(_BARS))
    assert fv.retracement == pytest.approx((6.5 - 5.6) / (6.5 - 4.6))


def test_pole_extension_atr_uses_baseline() -> None:
    fv = extract(_BARS, _seg_of(_BARS), atr=0.95)
    assert fv.pole_extension_atr == pytest.approx(1.9 / 0.95)  # 2x ATR -> abnormal
    assert extract(_BARS, _seg_of(_BARS), atr=0.0).pole_extension_atr is None  # guard div-by-zero


def test_trailing_atr() -> None:
    # Five flat-ish bars of range 1.0 then a base; TR of each (no gap) = high-low = 1.0.
    bars = [_bar(i, 5.0, 5.5, 4.5, 5.0) for i in range(5)]
    assert trailing_atr(bars, base_idx=4, window=4) == pytest.approx(1.0)  # bars 0-3, each TR = 1.0
    assert trailing_atr(bars, base_idx=3, window=4) is None  # fewer than window bars before base


def test_strictness_with_equal_highs() -> None:
    # Pole H E H (2 strict of 3 steps); consolidation L E L (2 strict of 3).
    bars = [_hbar(i, h) for i, h in enumerate([4.0, 5.0, 5.0, 6.0, 5.5, 5.5, 5.2])]
    fv = extract(bars, _seg_of(bars))
    assert fv.token_string == "HEHLEL"
    assert (fv.pole_len, fv.cons_len) == (2, 3)
    assert fv.pole_strictness == pytest.approx(2 / 3)
    assert fv.cons_strictness == pytest.approx(2 / 3)


def test_cons_drift_slope_negative() -> None:
    # Multi-bar pullback with descending highs -> negative per-step drift.
    bars = [_hbar(i, h) for i, h in enumerate([4.0, 6.0, 5.6, 5.4, 5.2])]
    fv = extract(bars, _seg_of(bars))
    assert fv.cons_len == 3
    assert fv.cons_drift_slope == pytest.approx((5.2 - 5.6) / 2)  # (last-first)/(steps)


def test_vol_ratio_infinite_when_cons_has_no_volume() -> None:
    bars = [
        _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
        _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
        _bar(2, 6.0, 6.1, 5.6, 5.7, vol=0.0),
    ]
    fv = extract(bars, _seg_of(bars))
    assert fv.vol_ratio == float("inf")
    assert fv.peak_gt_cons is True


def test_cons_vol_not_reducing() -> None:
    bars = [
        _hbar(0, 4.0, vol=1000),
        _hbar(1, 6.0, vol=2000),
        _hbar(2, 5.6, vol=100),
        _hbar(3, 5.4, vol=500),
    ]
    fv = extract(bars, _seg_of(bars))
    assert fv.cons_vol_reducing is False  # 100 -> 500 rises


def test_trigger_out_of_window() -> None:
    # Same bars, but a narrow window that excludes 10:xx ET -> False (plumbing check).
    fv = extract(_BARS, _seg_of(_BARS), window_start=time(11, 0), window_end=time(11, 30))
    assert fv.trigger_in_window is False


def _timed_setup(base_utc: datetime) -> list[Bar]:
    """The clean 3-bar setup shifted to start at ``base_utc`` (for window-boundary tests)."""
    return [
        Bar(start=base_utc + timedelta(minutes=5 * i), open=o, high=h, low=lo, close=c, volume=v)
        for i, (o, h, lo, c, v) in enumerate(
            [
                (5.0, 5.8, 4.6, 5.6, 1000.0),
                (5.6, 6.5, 5.5, 6.4, 2000.0),
                (6.0, 6.1, 5.6, 5.7, 800.0),
            ]
        )
    ]


def test_trigger_window_anchors_on_breakout_not_cons_open() -> None:
    # Consolidation completes on the 11:55 ET bar; the breakout bar opens 12:00 ET (past 11:59).
    # Anchoring on cons_end's OPEN (11:55) would wrongly read in-window — the fix anchors on the
    # next-bar open (12:00) -> out of window.
    bars = _timed_setup(datetime(2026, 6, 29, 15, 45, tzinfo=UTC))  # bar2 @ 15:55 UTC = 11:55 ET
    assert extract(bars, _seg_of(bars)).trigger_in_window is False


def test_trigger_window_true_when_breakout_inside() -> None:
    bars = _timed_setup(datetime(2026, 6, 29, 15, 40, tzinfo=UTC))  # bar2 @ 11:50 ET, trigger 11:55
    assert extract(bars, _seg_of(bars)).trigger_in_window is True


def test_retracement_uses_e_tolerant_base() -> None:
    # A pole with a leading equal-high (E) step: v2 anchors the base at bar 0 (E-tolerant), which
    # intentionally differs from the legacy strict walk (which stops at the flat). Documents the
    # #179 parity scoping — parity is claimed for strict poles only.
    bars = [_hbar(i, h) for i, h in enumerate([4.0, 5.0, 5.0, 6.0, 5.5, 5.5, 5.2])]
    seg = _seg_of(bars)
    assert seg.base_idx == 0  # E-tolerant base includes the pre-flat bar
    fv = extract(bars, seg)
    pole_high, pole_base = bars[3].high, bars[0].low
    cons_low = min(b.low for b in bars[4:])
    assert fv.retracement == pytest.approx((pole_high - cons_low) / (pole_high - pole_base))


def test_trigger_window_uses_modal_interval_not_last_gap() -> None:
    # A missing bar before cons_end makes the LAST gap 10 min, but the modal spacing is 5 min.
    # cons_end @ 11:50 ET -> modal trigger 11:55 (in window); the naive last-gap trigger would be
    # 12:00 (out). trigger_in_window must use the modal interval -> True.
    base = datetime(2026, 6, 29, 15, 30, tzinfo=UTC)  # 11:30 ET
    starts = [0, 5, 10, 20]  # gaps 5,5,10 -> modal 5; last gap 10 (a missing 11:45 bar)
    highs = [4.0, 6.0, 5.6, 5.4]
    bars = [
        Bar(
            start=base + timedelta(minutes=m),
            open=h - 0.3,
            high=h,
            low=h - 0.5,
            close=h - 0.1,
            volume=1000.0,
        )
        for m, h in zip(starts, highs, strict=True)
    ]
    assert extract(bars, _seg_of(bars)).trigger_in_window is True
