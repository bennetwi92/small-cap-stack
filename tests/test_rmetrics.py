"""Tests for R-multiple measurement (#18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.rmetrics import compute_r_metrics

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bar(i: int, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=1e3)


# A bull flag: green pole then red flag. breakout=flag.high=6.1, entry=6.11, stop=5.6, risk=0.51.
_POLE = _bar(0, 5.0, 6.2, 4.9, 6.0)
_FLAG = _bar(1, 6.0, 6.1, 5.6, 5.7)


def test_triggers_and_measures_max_r() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 7.0, 5.7, 6.9),  # high 7.0 >= 6.11 -> entry; run up
        _bar(3, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.entry_trigger == 6.11
    assert m.stop == 5.6
    assert m.initial_risk == 0.51
    assert m.entry_index == 2
    assert m.max_r == round((7.64 - 6.11) / 0.51, 3)  # == 3.0
    assert not m.stopped_out


def test_setup_but_never_triggers() -> None:
    bars = [_POLE, _FLAG, _bar(2, 5.7, 6.0, 5.65, 5.8)]  # high 6.0 < entry 6.11
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and not m.triggered
    assert m.max_r is None


def test_triggers_then_stops_out() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.2, 5.7, 6.0),  # triggers (high 6.2 >= 6.11)
        _bar(3, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    assert m.mae_r is not None and m.mae_r > 0


def test_no_setup() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no pole
    m = compute_r_metrics(bars, _settings())
    assert not m.setup_found
    assert not m.triggered
