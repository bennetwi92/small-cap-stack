"""Tests for bull-flag detection (#16, redefined #127).

Pole = a run of higher highs (colour-agnostic, len min_pole..max_pole); flag = a trailing pullback
that makes a lower low and retraces <= max_retracement of the pole. Entry = 5 ticks above the last
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


# Canonical valid setup: a 2-bar higher-highs pole then a single red flag.
# pole highs 5.8 -> 6.5 (higher high); flag high 6.1 (breakout), low 5.6 (stop). pole base 4.6 ->
# retracement (6.5-5.6)/(6.5-4.6) = 0.474 (< 0.50 gate). entry = 6.15 (+5 ticks).
_POLE1 = _bar(0, 5.0, 5.8, 4.6, 5.7)
_POLE2 = _bar(1, 5.7, 6.5, 5.6, 6.4)
_FLAG = _bar(2, 6.4, 6.1, 5.6, 5.7)
_SETUP = [_POLE1, _POLE2, _FLAG]


def test_classify() -> None:
    assert classify(_bar(0, 1, 2, 0.5, 1.5)) == "green"
    assert classify(_bar(0, 2, 2, 0.5, 1.5)) == "red"
    assert classify(_bar(0, 1, 2, 0.5, 1.0)) == "flat"


def test_two_bar_pole_one_flag() -> None:
    bf = detect(_SETUP)
    assert bf is not None
    assert bf.pole_len == 2
    assert bf.flag_len == 1
    assert bf.breakout_level == 6.1
    assert bf.entry_trigger == 6.15  # 6.1 + 5 ticks
    assert bf.stop == 5.6
    assert bf.retracement == round((6.5 - 5.6) / (6.5 - 4.6), 4)


def test_single_bar_pole_needs_two_higher_highs() -> None:
    # A lone pre-flag bar (no ascending predecessor) is not a pole: high 6.0 does not rise above the
    # earlier 7.0 bar, so the higher-highs run is length 1 (< min_pole) -> rejected.
    bars = [_bar(0, 6.5, 7.0, 6.4, 6.9), _bar(1, 5.5, 6.0, 5.4, 5.9), _bar(2, 5.9, 5.8, 5.3, 5.4)]
    assert detect(bars) is None


def test_pole_allows_a_non_green_bar_with_a_higher_high() -> None:
    # Middle pole bar is RED but makes a higher high (5.8 -> 6.2 -> 6.6): it counts (pole_len=3).
    bars = [
        _bar(0, 5.2, 5.8, 5.0, 5.7),  # green
        _bar(1, 5.9, 6.2, 5.5, 5.6),  # red, but high 6.2 > 5.8 -> still a higher high
        _bar(2, 5.6, 6.6, 5.5, 6.5),  # green, high 6.6
        _bar(3, 6.5, 6.3, 5.9, 6.0),  # red flag (high 6.3 < 6.6, low 5.9)
    ]
    bf = detect(bars)
    assert bf is not None
    assert bf.pole_len == 3  # the red bar is part of the pole
    assert bf.flag_len == 1


def test_pole_capped_at_max_pole() -> None:
    # Nine strictly-higher-high bars then a shallow flag: the pole truncates to the most recent 8.
    pole = [
        _bar(i, 6.0 + 0.1 * i - 0.05, 6.0 + 0.1 * i, 5.7 + 0.1 * i, 5.95 + 0.1 * i)
        for i in range(9)
    ]
    flag = _bar(9, 6.7, 6.7, 6.5, 6.55)  # red, high < 6.8, shallow
    bf = detect([*pole, flag])
    assert bf is not None
    assert bf.pole_len == 8  # capped, not 9


def test_single_green_flag_rejected() -> None:
    # A single consolidation candle that is GREEN is not a pullback (no lower low).
    green_flag = _bar(2, 5.7, 6.1, 5.6, 6.0)
    assert detect([_POLE1, _POLE2, green_flag]) is None


def test_multi_bar_flag_without_lower_low_rejected() -> None:
    # Two flag bars whose lows do NOT step down (5.7 then 5.8) -> "no lower lows" -> rejected.
    f1 = _bar(2, 6.4, 6.3, 5.7, 5.9)  # red, high 6.3
    f2 = _bar(3, 5.9, 6.0, 5.8, 5.85)  # red, low 5.8 >= 5.7 (no lower low)
    assert detect([_POLE1, _POLE2, f1, f2]) is None


def test_multi_bar_flag_with_lower_low_accepted() -> None:
    # Lower base (4.0) keeps the retracement under 50% for the deeper 2-bar flag.
    pole1 = _bar(0, 5.0, 5.8, 4.0, 5.7)
    f1 = _bar(2, 6.4, 6.3, 5.7, 5.9)  # red, low 5.7
    f2 = _bar(3, 5.9, 6.0, 5.5, 5.6)  # red, low 5.5 < 5.7 -> lower low
    bf = detect([pole1, _POLE2, f1, f2])
    assert bf is not None
    assert bf.pole_len == 2 and bf.flag_len == 2
    assert bf.stop == 5.5  # flag low across both bars
    assert bf.breakout_level == 6.0  # high of the LAST consolidation candle


def test_deep_retracement_rejected() -> None:
    # flag low 5.4 -> retrace (6.5-5.4)/(6.5-4.6) = 0.579 > 0.50 -> invalid (back through the pole).
    deep = _bar(2, 6.4, 6.1, 5.4, 5.5)
    assert detect([_POLE1, _POLE2, deep]) is None


def test_retracement_at_boundary_accepted() -> None:
    # Exactly 50% retrace passes (the gate rejects only > max_retracement).
    pole1 = _bar(0, 5.0, 5.8, 4.5, 5.7)  # base 4.5, height 2.0
    flag = _bar(2, 6.4, 6.1, 5.5, 5.6)  # low 5.5 -> retrace (6.5-5.5)/2.0 = 0.50
    bf = detect([pole1, _POLE2, flag])
    assert bf is not None
    assert bf.retracement == 0.5


def test_pullback_below_pole_base_rejected() -> None:
    below = _bar(2, 6.4, 6.1, 4.5, 4.6)  # low 4.5 <= pole base 4.6
    assert detect([_POLE1, _POLE2, below]) is None


def test_flag_longer_than_max_flag_rejected() -> None:
    # A 7-bar descending flag exceeds max_flag=6, so no valid peak can be placed -> None...
    pole1 = _bar(0, 5.0, 5.8, 3.0, 5.7)
    flag = [
        _bar(2 + k, 6.6 - 0.1 * k, 6.4 - 0.1 * k, 5.9 - 0.05 * k, 6.0 - 0.1 * k) for k in range(7)
    ]
    bars = [pole1, _POLE2, *flag]
    assert detect(bars, max_flag=6) is None
    # ...but with max_flag raised to 7 the same series IS a valid setup.
    bf = detect(bars, max_flag=7)
    assert bf is not None and bf.flag_len == 7


def test_max_flag_six_accepted() -> None:
    # Six lower-high / lower-low consolidation candles, all holding within 50% of a tall pole.
    pole1 = _bar(0, 5.0, 6.0, 3.0, 5.9)
    pole2 = _bar(1, 5.9, 8.0, 6.0, 7.9)  # pole high 8.0, base 3.0, height 5.0
    highs = [7.5, 7.2, 6.9, 6.6, 6.3, 6.0]
    lows = [7.0, 6.7, 6.4, 6.1, 5.8, 5.6]  # descending, all > 50% level (5.5)
    flag = [_bar(2 + k, highs[k] + 0.1, highs[k], lows[k], lows[k] + 0.05) for k in range(6)]
    bf = detect([pole1, pole2, *flag])
    assert bf is not None
    assert bf.flag_len == 6
    assert bf.stop == 5.6


def test_still_extending_returns_none() -> None:
    # Last bar makes a NEW high above the pole -> no flag has formed yet.
    bars = [_POLE1, _POLE2, _bar(2, 5.7, 7.0, 5.7, 6.9)]
    assert detect(bars) is None


def test_vol_increasing_flag() -> None:
    rising = [_bar(0, 5.0, 5.8, 4.6, 5.7, vol=1000), _bar(1, 5.7, 6.5, 5.6, 6.4, vol=2000), _FLAG]
    falling = [_bar(0, 5.0, 5.8, 4.6, 5.7, vol=2000), _bar(1, 5.7, 6.5, 5.6, 6.4, vol=800), _FLAG]
    assert detect(rising) is not None and detect(rising).vol_increasing is True  # type: ignore[union-attr]
    assert detect(falling) is not None and detect(falling).vol_increasing is False  # type: ignore[union-attr]


def test_entry_offset_is_configurable() -> None:
    bf = detect(_SETUP, entry_offset=0.03)
    assert bf is not None
    assert bf.entry_trigger == round(6.1 + 0.03, 4)


def test_needs_minimum_bars() -> None:
    assert detect([_POLE1, _POLE2]) is None  # pole only, no flag
    assert detect([_POLE1]) is None


def test_no_pole_rejected() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no higher highs
    assert detect(bars) is None


def test_detect_with_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    bf = detect_with_settings(_SETUP, s)
    assert bf is not None and bf.entry_trigger == 6.15  # 5 ticks @ $0.01
    # A deep-retrace variant is rejected under the default 0.50 gate.
    deep = _bar(2, 6.4, 6.1, 5.4, 5.5)
    assert detect_with_settings([_POLE1, _POLE2, deep], s) is None
