"""Tests for bull-flag detection (#16, redefined #127).

Pole = a run of higher highs (colour-agnostic; even a single higher-high bar), pole_len counts the
higher highs. Flag = a trailing pullback that makes lower highs and retraces <= max_retracement of
the pole; the pole's peak-volume bar must exceed the consolidation's. Entry = 5 ticks above the last
consolidation high; stop = flag low.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import classify, detect, detect_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# Canonical valid setup: a launch bar (high 5.8), one higher-high pole bar (high 6.5, vol 2000 so it
# tops the flag), then a red flag (high 6.1). pole_len = 1 (one higher high); pole base 4.6 (launch
# low); flag high 6.1 (breakout), low 5.6 (stop). retrace (6.5-5.6)/(6.5-4.6) = 0.474 (< 0.50).
_LAUNCH = _bar(0, 5.0, 5.8, 4.6, 5.7)
_POLE = _bar(1, 5.7, 6.5, 5.6, 6.4, vol=2000)
_FLAG = _bar(2, 6.4, 6.1, 5.6, 5.7)
_SETUP = [_LAUNCH, _POLE, _FLAG]


def test_classify() -> None:
    assert classify(_bar(0, 1, 2, 0.5, 1.5)) == "green"
    assert classify(_bar(0, 2, 2, 0.5, 1.5)) == "red"
    assert classify(_bar(0, 1, 2, 0.5, 1.0)) == "flat"


def test_single_higher_high_is_a_pole() -> None:
    bf = detect(_SETUP)
    assert bf is not None
    assert bf.pole_len == 1  # one higher high
    assert bf.flag_len == 1
    assert bf.breakout_level == 6.1
    assert bf.entry_trigger == 6.15  # 6.1 + 5 ticks
    assert bf.stop == 5.6
    assert bf.retracement == round((6.5 - 5.6) / (6.5 - 4.6), 4)
    assert bf.cons_vol_reducing is True  # single-bar flag -> trivially non-increasing


def test_multi_higher_high_pole() -> None:
    # launch 4.0, then three higher highs 5.0/6.0/7.0 -> pole_len 3.
    bars = [
        _bar(0, 3.9, 4.0, 3.5, 3.95),
        _bar(1, 3.95, 5.0, 3.9, 4.9, vol=2000),
        _bar(2, 4.9, 6.0, 4.8, 5.9, vol=2000),
        _bar(3, 5.9, 7.0, 5.8, 6.9, vol=2000),
        _bar(4, 6.9, 6.5, 5.9, 6.0),  # red flag
    ]
    bf = detect(bars)
    assert bf is not None
    assert bf.pole_len == 3


def test_pole_capped_at_max_pole() -> None:
    # Ten strictly-higher-high bars (nine higher highs) then a shallow flag: pole_len caps at 8.
    pole = [
        _bar(
            i,
            5.0 + 0.2 * i - 0.3,
            5.0 + 0.2 * i,
            5.0 + 0.2 * i - 0.4,
            5.0 + 0.2 * i - 0.05,
            vol=2000,
        )
        for i in range(10)
    ]
    flag = _bar(10, 6.7, 6.7, 6.4, 6.5)  # red, high < 6.8 (bar 9), shallow
    bf = detect([*pole, flag])
    assert bf is not None
    assert bf.pole_len == 8  # capped, not 9


def test_pole_allows_a_non_green_bar_with_a_higher_high() -> None:
    # Middle pole bar is RED but makes a higher high (5.8 -> 6.2 -> 6.6): two higher highs counted.
    bars = [
        _bar(0, 5.2, 5.8, 5.0, 5.7),  # launch (green)
        _bar(1, 5.9, 6.2, 5.5, 5.6, vol=2000),  # red, but high 6.2 > 5.8 -> a higher high
        _bar(2, 5.6, 6.6, 5.5, 6.5, vol=2000),  # green, high 6.6
        _bar(3, 6.5, 6.3, 5.9, 6.0),  # red flag (high 6.3 < 6.6, low 5.9)
    ]
    bf = detect(bars)
    assert bf is not None
    assert bf.pole_len == 2  # the red bar is one of the two higher highs


def test_single_flag_candle_valid_regardless_of_colour() -> None:
    # A single consolidation candle below the peak is a lower high -> valid even if it is green
    # (the trader tracks highs, not lows).
    green_flag = _bar(2, 5.7, 6.1, 5.6, 6.0)  # green, high 6.1 < peak 6.5
    assert detect([_LAUNCH, _POLE, green_flag]) is not None


def test_multi_bar_flag_without_lower_highs_rejected() -> None:
    # Second flag bar makes a HIGHER high (6.0 -> 6.3): not a pullback -> rejected.
    f1 = _bar(2, 6.4, 6.0, 5.7, 5.8)  # high 6.0
    f2 = _bar(3, 5.8, 6.3, 5.7, 6.2)  # high 6.3 > 6.0 (ticks back up)
    assert detect([_LAUNCH, _POLE, f1, f2]) is None


def test_multi_bar_flag_with_lower_highs_accepted() -> None:
    f1 = _bar(2, 6.4, 6.3, 5.7, 5.9)  # high 6.3
    f2 = _bar(3, 5.9, 6.0, 5.6, 5.7)  # high 6.0 < 6.3 -> lower high
    bf = detect([_LAUNCH, _POLE, f1, f2])
    assert bf is not None
    assert bf.pole_len == 1 and bf.flag_len == 2
    assert bf.breakout_level == 6.0  # high of the LAST consolidation candle
    assert bf.stop == 5.6  # flag low across both bars


def test_deep_retracement_rejected() -> None:
    # flag low 5.4 -> retrace (6.5-5.4)/(6.5-4.6) = 0.579 > 0.50 -> invalid (back through the pole).
    deep = _bar(2, 6.4, 6.1, 5.4, 5.5)
    assert detect([_LAUNCH, _POLE, deep]) is None


def test_retracement_at_boundary_accepted() -> None:
    # Exactly 50% retrace passes (the gate rejects only > max_retracement).
    launch = _bar(0, 5.0, 5.8, 4.5, 5.7)  # base 4.5, height 2.0
    flag = _bar(2, 6.4, 6.1, 5.5, 5.6)  # low 5.5 -> retrace (6.5-5.5)/2.0 = 0.50
    bf = detect([launch, _POLE, flag])
    assert bf is not None
    assert bf.retracement == 0.5


def test_pullback_below_pole_base_rejected() -> None:
    below = _bar(2, 6.4, 6.1, 4.5, 4.6)  # low 4.5 <= pole base 4.6
    assert detect([_LAUNCH, _POLE, below]) is None


def test_consolidation_volume_must_be_below_the_pole() -> None:
    # Flag volume (2500) exceeds the pole's peak bar (2000) -> not a clean flag -> rejected.
    heavy_flag = _bar(2, 6.4, 6.1, 5.6, 5.7, vol=2500)
    assert detect([_LAUNCH, _POLE, heavy_flag]) is None
    # Equal volume is also rejected (strictly greater required).
    equal_flag = _bar(2, 6.4, 6.1, 5.6, 5.7, vol=2000)
    assert detect([_LAUNCH, _POLE, equal_flag]) is None


def test_cons_vol_reducing_recorded_not_gated() -> None:
    f1 = _bar(2, 6.4, 6.3, 5.7, 5.9, vol=1500)  # high 6.3
    f2_down = _bar(3, 5.9, 6.0, 5.6, 5.7, vol=1000)  # lower high, lower volume
    f2_up = _bar(3, 5.9, 6.0, 5.6, 5.7, vol=1900)  # lower high, HIGHER volume (still < pole 2000)
    reducing = detect([_LAUNCH, _POLE, f1, f2_down])
    rising = detect([_LAUNCH, _POLE, f1, f2_up])
    assert reducing is not None and reducing.cons_vol_reducing is True
    assert rising is not None and rising.cons_vol_reducing is False  # not gated, just recorded


def test_wicky_peak_bar_rejected() -> None:
    # The peak bar closes at 5.9, well below its 6.5 high: upper wick 0.6 / range 0.9 = 0.67 > 0.50.
    wicky_peak = _bar(1, 5.7, 6.5, 5.6, 5.9, vol=2000)
    assert detect([_LAUNCH, wicky_peak, _FLAG]) is None


def test_peak_wick_at_boundary_accepted() -> None:
    # Upper wick exactly 50% of range passes (rejected only when > max_peak_wick).
    launch = _bar(0, 5.0, 5.8, 4.5, 5.7)
    peak = _bar(1, 5.7, 6.5, 5.5, 6.0, vol=2000)  # upper wick 0.5 / range 1.0 = 0.50
    assert detect([launch, peak, _FLAG]) is not None


def test_pole_has_big_green_recorded_not_gated() -> None:
    bf = detect(_SETUP)  # the pole's launch bar is a strong-bodied green
    assert bf is not None and bf.pole_has_big_green is True
    # A tiny-body launch + a red (but clean-closing) peak has no big green -> False, still valid.
    launch = _bar(0, 5.6, 5.8, 4.6, 5.65)  # green body 0.05 / range 1.2 -> not "big"
    red_peak = _bar(1, 6.4, 6.5, 5.6, 5.9, vol=2000)  # red higher high, small upper wick (0.1/0.9)
    bf2 = detect([launch, red_peak, _FLAG])
    assert bf2 is not None and bf2.pole_has_big_green is False


def test_flag_longer_than_max_flag_rejected() -> None:
    launch = _bar(0, 5.0, 5.8, 3.0, 5.7)  # tall pole base so a 7-bar flag can stay < 50%
    flag = [
        _bar(2 + k, 6.4 - 0.1 * k, 6.4 - 0.1 * k, 5.5 - 0.05 * k, 5.6 - 0.1 * k) for k in range(7)
    ]
    bars = [launch, _POLE, *flag]
    assert detect(bars, max_flag=6) is None  # 7-bar pullback exceeds max_flag -> no valid peak...
    bf = detect(bars, max_flag=7)  # ...but a 7-bar flag IS valid with max_flag raised
    assert bf is not None and bf.flag_len == 7


def test_still_extending_returns_none() -> None:
    bars = [_LAUNCH, _POLE, _bar(2, 5.7, 7.0, 5.7, 6.9)]  # last bar makes a NEW high
    assert detect(bars) is None


def test_entry_offset_is_configurable() -> None:
    bf = detect(_SETUP, entry_offset=0.03)
    assert bf is not None
    assert bf.entry_trigger == round(6.1 + 0.03, 4)


def test_needs_minimum_bars() -> None:
    assert detect([_LAUNCH, _POLE]) is None  # pole only, no flag
    assert detect([_LAUNCH]) is None


def test_no_higher_high_rejected() -> None:
    # Descending highs 6.1 -> 6.0 -> 5.9: nothing makes a higher high -> no pole.
    bars = [
        _bar(0, 6.0, 6.1, 5.9, 5.95),
        _bar(1, 5.95, 6.0, 5.8, 5.85),
        _bar(2, 5.85, 5.9, 5.7, 5.75),
    ]
    assert detect(bars) is None


def test_detect_with_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    bf = detect_with_settings(_SETUP, s)
    assert bf is not None and bf.entry_trigger == 6.15 and bf.pole_len == 1
    # A deep-retrace variant is rejected under the default 0.50 gate.
    deep = _bar(2, 6.4, 6.1, 5.4, 5.5)
    assert detect_with_settings([_LAUNCH, _POLE, deep], s) is None
