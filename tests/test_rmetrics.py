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


# A bull flag: green pole then red flag. breakout=flag.high=6.1, entry=6.15 (+5 ticks),
# stop=5.6, risk=0.55.
_POLE = _bar(0, 5.0, 6.2, 4.9, 6.0)
_FLAG = _bar(1, 6.0, 6.1, 5.6, 5.7)


def test_triggers_and_measures_max_r() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 7.0, 5.7, 6.9),  # high 7.0 >= 6.15 -> entry; run up
        _bar(3, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.entry_trigger == 6.15
    assert m.stop == 5.6
    assert m.initial_risk == 0.55
    assert m.entry_index == 2
    assert m.max_r == round((7.64 - 6.15) / 0.55, 3)  # 1.49 / 0.55 == 2.709
    assert not m.stopped_out


def test_setup_but_never_triggers() -> None:
    bars = [_POLE, _FLAG, _bar(2, 5.7, 6.0, 5.65, 5.8)]  # high 6.0 < entry 6.15
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and not m.triggered
    assert m.max_r is None


def test_triggers_then_stops_out() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.2, 5.7, 6.0),  # triggers (high 6.2 >= 6.15)
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


def test_max_r_not_credited_after_stop() -> None:
    # Trigger, then stop out, then a (fictitious) higher high on a later bar: the post-stop
    # spike must NOT inflate Max R, because the position is already closed (H1).
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.5, 5.7, 6.0),  # triggers (high 6.5 >= 6.15); Max R from this bar
        _bar(3, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped here
        _bar(4, 5.5, 9.0, 5.5, 8.9),  # post-stop moonshot — must be ignored
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    # Max R reflects only the pre-stop peak (6.5), not the post-stop 9.0.
    assert m.max_r == round((6.5 - 6.15) / 0.55, 3)


def test_same_bar_trigger_and_stop_counts_as_stopped() -> None:
    # One bar reaches the entry trigger AND breaches the stop. Stop-first convention: the trade
    # is stopped on entry, no favourable excursion credited (H2).
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.3, 5.4, 5.5),  # high 6.3 >= entry 6.15 AND low 5.4 <= stop 5.6
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    assert m.max_r == 0.0  # no favourable excursion credited
    assert m.mae_r is not None and m.mae_r >= 1.0  # adverse excursion reaches >= 1R


def test_pre_appearance_trigger_is_not_counted() -> None:
    # The only breakout fires at +10min, but the symbol didn't appear on the scanner until +12min.
    # We can't take a move we weren't aware of (#99): it must read as setup-found, not triggered.
    bars = [_POLE, _FLAG, _bar(2, 5.7, 7.0, 5.7, 6.9)]  # triggers at bar2 (+10min)
    appear = _T0 + timedelta(minutes=12)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.setup_found and not m.triggered
    assert m.max_r is None
    # Sanity: without the appearance gate the same bars DO trigger (the gate is what changes it).
    assert compute_r_metrics(bars, _settings()).triggered


def test_setup_forms_before_appearance_but_triggers_after() -> None:
    # The flag forms at +5min (pre-appearance) but only triggers at +15min (post): allowed.
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.0, 5.65, 5.8),  # +10min: high 6.0 < entry 6.15, no trigger yet
        _bar(3, 5.8, 7.0, 5.75, 6.9),  # +15min: triggers here
    ]
    appear = _T0 + timedelta(minutes=12)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered
    assert m.entry_trigger == 6.15
    assert m.entry_index == 3


def test_trigger_exactly_at_appearance_counts() -> None:
    # A trigger on the very bar the symbol appears counts (the gate is inclusive: >= first_hit).
    bars = [_POLE, _FLAG, _bar(2, 5.7, 7.0, 5.7, 6.9)]  # triggers at bar2 (+10min)
    appear = _T0 + timedelta(minutes=10)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered and m.entry_index == 2


def test_thin_risk_setup_stays_finite() -> None:
    # The 5-tick entry offset puts a floor on risk (entry is >=5 ticks above breakout, and the
    # stop is at/below breakout, so risk >= $0.05). A tight flag still yields a thin-but-finite R.
    bars = [
        _bar(0, 5.0, 6.20, 4.9, 6.0),  # pole (green)
        _bar(1, 6.10, 6.10, 6.09, 6.095),  # flag (red): high 6.10, low 6.09 -> stop 6.09
        _bar(2, 6.12, 7.00, 6.12, 6.9),  # entry 6.15 <= high 7.00 -> triggers, runs up
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.initial_risk == round(6.15 - 6.09, 6)  # 0.06
    assert m.max_r == round((7.00 - 6.15) / 0.06, 3)
