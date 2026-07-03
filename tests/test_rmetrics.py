"""Tests for R-multiple measurement (#18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.rmetrics import compute_r_metrics

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1e3) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# A bull flag: a launch bar (5.8) + one higher-high pole bar (6.5, heavier volume) then a red flag.
# breakout = flag high 6.1, entry = 6.15 (+5 ticks), stop = 5.6, risk = 0.55. pole base 4.6 ->
# retrace 0.474 (< 0.50). The setup is detected at index 2 (the flag); the breakout is a LATER bar.
_LAUNCH = _bar(0, 5.0, 5.8, 4.6, 5.7)
_POLE = _bar(1, 5.7, 6.5, 5.6, 6.4, vol=2000)
_FLAG = _bar(2, 6.4, 6.1, 5.6, 5.7)
_SETUP = [_LAUNCH, _POLE, _FLAG]


def test_triggers_and_measures_max_r() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),  # high 7.0 >= 6.15 -> entry at bar 3; run up
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.entry_trigger == 6.15
    assert m.stop == 5.6
    assert m.initial_risk == 0.55
    assert m.entry_index == 3
    assert m.max_r == round((7.64 - 6.15) / 0.55, 3)
    assert not m.stopped_out
    assert m.stop_index is None  # never stopped -> no stop bar (#113)
    assert m.flag_len == 1 and m.retracement is not None  # traded setup's shape (#98)
    assert m.pole_len == 1 and m.cons_vol_reducing is not None  # pole/vol shape recorded (#127)


def test_setup_but_never_triggers() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 6.0, 5.65, 5.8)]  # high 6.0 < entry 6.15
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and not m.triggered
    assert m.max_r is None


def test_triggers_then_stops_out() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.2, 5.7, 6.0),  # triggers (high 6.2 >= 6.15)
        _bar(4, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    assert m.mae_r is not None and m.mae_r > 0
    assert m.entry_index == 3 and m.stop_index == 4  # stop breached on the bar after entry (#113)


def test_no_setup() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no pole
    m = compute_r_metrics(bars, _settings())
    assert not m.setup_found
    assert not m.triggered


def test_max_r_not_credited_after_stop() -> None:
    # Trigger, then stop out, then a (fictitious) higher high on a later bar: the post-stop
    # spike must NOT inflate Max R, because the position is already closed (H1).
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.5, 5.7, 6.0),  # triggers (high 6.5 >= 6.15); Max R from this bar
        _bar(4, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped here
        _bar(5, 5.5, 9.0, 5.5, 8.9),  # post-stop moonshot — must be ignored
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    # Max R reflects only the pre-stop peak (6.5), not the post-stop 9.0.
    assert m.max_r == round((6.5 - 6.15) / 0.55, 3)


def test_same_bar_trigger_and_stop_counts_as_stopped() -> None:
    # One bar reaches the entry trigger AND breaches the stop. Stop-first convention: the trade
    # is stopped on entry, no favourable excursion credited (H2).
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.3, 5.4, 5.5),  # high 6.3 >= entry 6.15 AND low 5.4 <= stop 5.6
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.triggered and m.stopped_out
    assert m.max_r == 0.0  # no favourable excursion credited
    assert m.mae_r is not None and m.mae_r >= 1.0  # adverse excursion reaches >= 1R
    assert m.entry_index == 3 and m.stop_index == 3  # same-bar trigger+stop share the bar (#113)


def test_trigger_on_the_appearance_bar_counts() -> None:
    # The breakout bar is [+15, +20); the symbol appeared at +17 — i.e. DURING that bar. Bar-close
    # gate (#122): we'd appeared before it closed, so the break is takeable (matches how it trades).
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]  # breakout bar 3
    appear = _T0 + timedelta(minutes=17)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered and m.entry_index == 3


def test_trigger_bar_that_closed_before_appearance_is_not_counted() -> None:
    # The only breakout bar closed at +20, but the symbol didn't appear until +25 — the move was
    # already over, so it reads as setup-found, not triggered (#99/#122).
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]  # breakout bar 3 closes at +20
    appear = _T0 + timedelta(minutes=25)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.setup_found and not m.triggered
    assert m.max_r is None
    # Sanity: without the appearance gate the same bars DO trigger (the gate is what changes it).
    assert compute_r_metrics(bars, _settings()).triggered


def test_setup_forms_before_appearance_but_triggers_after() -> None:
    # The flag forms at +10min (pre-appearance) but only triggers at +20min (post): allowed.
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.0, 5.65, 5.8),  # +15min: high 6.0 < entry 6.15, no trigger yet
        _bar(4, 5.8, 7.0, 5.75, 6.9),  # +20min: triggers here
    ]
    appear = _T0 + timedelta(minutes=17)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered
    assert m.entry_trigger == 6.15
    assert m.entry_index == 4


def test_trigger_exactly_at_appearance_counts() -> None:
    # A trigger on the very bar the symbol appears counts (the gate is inclusive: >= first_hit).
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]  # triggers at bar 3 (+15min)
    appear = _T0 + timedelta(minutes=15)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered and m.entry_index == 3


def test_entry_within_staleness_window_counts() -> None:
    # A break 25min after the scan is inside the 30min window -> a takeable entry.
    bars = [
        *_SETUP,  # flag at +10, entry 6.15
        _bar(3, 5.7, 5.9, 5.6, 5.8),  # +15: below entry
        _bar(4, 5.8, 5.9, 5.6, 5.7),  # +20: below entry
        _bar(5, 5.8, 7.0, 5.8, 6.9),  # +25: breaks 6.15
    ]
    m = compute_r_metrics(bars, _settings(), first_hit=_T0)  # appeared at +0
    assert m.triggered and m.entry_index == 5


def test_entry_beyond_staleness_window_is_faded() -> None:
    # The setup forms at the scan, but the only break comes ~40min later (> the 30min window):
    # the opportunity has faded, so it reads as setup-found, not triggered (#130, AHMA).
    bars = [
        *_SETUP,  # flag at +10, entry 6.15
        _bar(3, 5.7, 5.9, 5.6, 5.8),  # +15
        _bar(4, 5.8, 5.9, 5.6, 5.7),  # +20
        _bar(5, 5.7, 5.9, 5.6, 5.8),  # +25
        _bar(6, 5.8, 5.9, 5.6, 5.7),  # +30
        _bar(7, 5.7, 5.9, 5.6, 5.8),  # +35
        _bar(8, 5.8, 7.0, 5.8, 6.9),  # +40: breaks 6.15, but too stale
    ]
    m = compute_r_metrics(bars, _settings(), first_hit=_T0)  # appeared at +0
    assert m.setup_found and not m.triggered
    assert m.max_r is None
    # Sanity: with the gate disabled (no appearance) the same break DOES trigger.
    assert compute_r_metrics(bars, _settings(), first_hit=None).triggered


def test_thin_risk_setup_stays_finite() -> None:
    # The 5-tick entry offset puts a floor on risk (entry is >=5 ticks above breakout, and the
    # stop is at/below breakout, so risk >= $0.05). A tight flag still yields a thin-but-finite R.
    bars = [
        _bar(0, 5.0, 5.90, 4.9, 5.8),  # launch (green)
        _bar(1, 5.8, 6.20, 5.7, 6.1, vol=2000),  # higher-high pole bar 6.20 (heavier volume)
        _bar(2, 6.10, 6.10, 6.09, 6.095),  # flag (red): high 6.10, low 6.09 -> stop 6.09
        _bar(3, 6.12, 7.00, 6.12, 6.9),  # entry 6.15 <= high 7.00 -> triggers, runs up
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.initial_risk == round(6.15 - 6.09, 6)  # 0.06
    assert m.max_r == round((7.00 - 6.15) / 0.06, 3)
