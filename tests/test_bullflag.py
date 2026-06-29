"""Tests for bull-flag detection (#16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import classify, detect
from small_cap_stack.capture import Bar

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
    bf = detect(bars, tick=0.01)
    assert bf is not None
    assert bf.pole_len == 1
    assert bf.flag_len == 1
    assert bf.breakout_level == bars[-1].high
    assert bf.entry_trigger == round(bars[-1].high + 0.01, 4)
    assert bf.stop == bars[-1].low


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
