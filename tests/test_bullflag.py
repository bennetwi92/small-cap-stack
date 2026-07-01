"""Tests for bull-flag detection (#16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import classify, detect, detect_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _bar(i: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=1000.0
    )


def _green(i: int, base: float) -> Bar:
    return _bar(i, base, base + 1.2, base - 0.1, base + 1.0)  # up candle


def _red(i: int, base: float) -> Bar:
    return _bar(i, base, base + 0.1, base - 0.4, base - 0.3)  # down candle, shallow


def test_classify() -> None:
    assert classify(_bar(0, 1, 2, 0.5, 1.5)) == "green"
    assert classify(_bar(0, 2, 2, 0.5, 1.5)) == "red"
    assert classify(_bar(0, 1, 2, 0.5, 1.0)) == "flat"


def test_simple_one_pole_one_flag() -> None:
    bars = [_green(0, 5.0), _red(1, 6.0)]
    bf = detect(bars)  # default entry_offset = 5 ticks ($0.05)
    assert bf is not None
    assert bf.pole_len == 1
    assert bf.flag_len == 1
    assert bf.breakout_level == bars[-1].high
    assert bf.entry_trigger == round(bars[-1].high + 0.05, 4)
    assert bf.stop == bars[-1].low


def test_entry_offset_is_configurable() -> None:
    bars = [_green(0, 5.0), _red(1, 6.0)]
    bf = detect(bars, entry_offset=0.03)
    assert bf is not None
    assert bf.entry_trigger == round(bars[-1].high + 0.03, 4)


def test_two_pole_two_flag() -> None:
    bars = [_green(0, 5.0), _green(1, 6.0), _red(2, 7.0), _red(3, 6.8)]
    bf = detect(bars)
    assert bf is not None
    assert bf.pole_len == 2
    assert bf.flag_len == 2


def test_too_many_green_rejected() -> None:
    bars = [_green(0, 4.0), _green(1, 5.0), _green(2, 6.0), _red(3, 7.0)]
    assert detect(bars, max_green=2) is None


def test_too_many_red_rejected() -> None:
    bars = [_green(0, 5.0), _red(1, 6.0), _red(2, 5.7), _red(3, 5.4)]
    assert detect(bars, max_red=2) is None


def test_no_pole_rejected() -> None:
    bars = [_red(0, 6.0), _red(1, 5.7)]
    assert detect(bars) is None


def test_pullback_erasing_pole_rejected() -> None:
    # deep red whose low drops below the pole's base low
    pole = _green(0, 5.0)  # low 4.9
    deep = _bar(1, 6.0, 6.1, 4.0, 4.2)  # red, low 4.0 < pole low 4.9
    assert detect([pole, deep]) is None


def test_needs_two_bars() -> None:
    assert detect([_green(0, 5.0)]) is None


def test_retracement_measures_pullback_into_pole() -> None:
    # pole: high 6.2, base (low) 4.9 -> height 1.3. flag low 5.6 -> retrace (6.2-5.6)/1.3.
    bars = [_bar(0, 5.0, 6.2, 4.9, 6.0), _bar(1, 6.0, 6.1, 5.6, 5.7)]
    bf = detect(bars)
    assert bf is not None
    assert bf.retracement == round((6.2 - 5.6) / (6.2 - 4.9), 4)
    assert 0.0 <= bf.retracement < 1.0


# Six shallow red consolidation candles that all hold above the pole's base (low 4.9).
_SIX_REDS = [
    _bar(1, 6.00, 6.05, 5.70, 5.75),
    _bar(2, 5.75, 5.80, 5.60, 5.65),
    _bar(3, 5.65, 5.70, 5.55, 5.60),
    _bar(4, 5.60, 5.65, 5.50, 5.55),
    _bar(5, 5.55, 5.60, 5.45, 5.50),
    _bar(6, 5.50, 5.55, 5.40, 5.45),
]


def test_six_consolidations_accepted() -> None:
    bars = [_bar(0, 5.0, 6.2, 4.9, 6.0), *_SIX_REDS]
    bf = detect(bars, max_red=6)
    assert bf is not None
    assert bf.flag_len == 6
    assert bf.stop == 5.40  # flag low across all six reds


def test_seven_consolidations_rejected() -> None:
    bars = [_bar(0, 5.0, 6.2, 4.9, 6.0), *_SIX_REDS, _bar(7, 5.45, 5.50, 5.35, 5.40)]
    assert detect(bars, max_red=6) is None  # consolidation longer than max_red -> invalid


def test_default_settings_allow_six_consolidations() -> None:
    # The default max_red is now 6 (#98) — a six-candle flag is valid under Settings defaults.
    bars = [_bar(0, 5.0, 6.2, 4.9, 6.0), *_SIX_REDS]
    bf = detect_with_settings(bars, Settings(_env_file=None))  # type: ignore[call-arg]
    assert bf is not None and bf.flag_len == 6
