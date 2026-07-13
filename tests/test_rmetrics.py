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


# A bull flag: a launch bar (5.8) + one higher-high green thrust pole bar (6.5, heavier volume) then
# a red flag (6.1). Engine-v2 (detect_day): the entry breaks the last consolidation candle's high
# (6.1) by 1 tick -> trigger 6.11; R is measured against the conservative 3-tick FILL 6.13; stop =
# consolidation low 5.6, so risk = 6.13 - 5.6 = 0.53.
_LAUNCH = _bar(0, 5.0, 5.8, 4.6, 5.7)
_POLE = _bar(1, 5.7, 6.5, 5.6, 6.4, vol=2000)
_FLAG = _bar(2, 6.4, 6.1, 5.6, 5.7)
_SETUP = [_LAUNCH, _POLE, _FLAG]


def test_triggers_and_measures_max_r() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),  # high 7.0 >= 6.11 -> entry at bar 3; run up
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered and m.takeable
    assert m.entry_trigger == 6.11
    assert m.entry_fill == 6.13
    assert m.stop == 5.6
    assert m.initial_risk == 0.53
    assert m.entry_index == 3
    assert m.max_r == round((7.64 - 6.13) / 0.53, 3)  # measured vs the 3-tick fill
    assert not m.stopped_out
    assert m.stop_index is None  # never stopped -> no stop bar (#113)
    assert m.flag_len == 1 and m.retracement is not None  # traded setup's shape (#98)
    assert m.pole_len == 1 and m.cons_vol_reducing is not None  # pole/vol shape recorded (#127)
    assert m.cycle_num == 1 and not m.exhausted  # fresh move (#102)


def test_flag_that_never_breaks_out_is_not_a_setup() -> None:
    # Engine-v2 is entry-driven: a pole+flag whose consolidation high is never broken has no
    # actionable entry, so detect_day returns no setup at all (contrast legacy shape-detection).
    bars = [*_SETUP, _bar(3, 5.7, 6.0, 5.65, 5.8)]  # high 6.0 < the 6.11 trigger -> never breaks
    m = compute_r_metrics(bars, _settings())
    assert not m.setup_found
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
    # Max R reflects only the pre-stop peak (6.5), not the post-stop 9.0 — vs the 3-tick fill 6.13.
    assert m.max_r == round((6.5 - 6.13) / 0.53, 3)


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


def test_appearance_inside_the_breakout_bar_is_not_takeable() -> None:
    # Engine-v2 (#180) gates on the entry bar's START (not its close, #122): the breakout bar opened
    # at +15 but we didn't appear until +17 — the break may have printed before we saw the symbol,
    # so it's not takeable (MSTZ). With no later re-anchored entry here, there's no setup.
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]  # breakout bar opens at +15
    appear = _T0 + timedelta(minutes=17)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert not m.triggered
    assert compute_r_metrics(bars, _settings()).triggered  # sans gate, the same break IS takeable


def test_break_before_appearance_is_not_takeable() -> None:
    # The breakout bar opened at +15 but we didn't appear until +25 — we couldn't have taken it, and
    # there's no later entry, so no takeable setup (#99).
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]
    appear = _T0 + timedelta(minutes=25)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert not m.triggered
    assert compute_r_metrics(bars, _settings()).triggered  # sans gate, it triggers


def test_consolidation_extends_then_breaks_after_appearance() -> None:
    # The flag's consolidation runs an extra bar (+15) before a +20 break we CAN take (appeared +17,
    # so the +20 entry bar opens after us). v2 entry = last consolidation candle high + 1 tick.
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.0, 5.65, 5.8),  # +15min: extends the consolidation (high 6.0, no break)
        _bar(4, 5.8, 7.0, 5.75, 6.9),  # +20min: breaks the 6.0 cons high -> entry
    ]
    appear = _T0 + timedelta(minutes=17)
    m = compute_r_metrics(bars, _settings(), first_hit=appear)
    assert m.triggered
    assert m.entry_trigger == 6.01  # breaks the last cons candle (bar 3, high 6.0) + 1 tick
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


def test_gap_up_entry_fills_at_open_not_trigger() -> None:
    # The trigger bar OPENS at 7.00 — above the 6.15 entry trigger (a gap-through breakout). The
    # realistic fill is the open, not the trigger; crediting the 6.15 -> 7.00 gap would overstate
    # Max R and understate risk. Entry and risk widen to the actual fill (#163).
    bars = [*_SETUP, _bar(3, 7.00, 7.64, 6.95, 7.5)]  # opens 7.00 > trigger 6.15
    m = compute_r_metrics(bars, _settings())
    assert m.triggered
    assert m.entry_price == 7.00  # filled at the open, not 6.15
    assert m.initial_risk == round(7.00 - 5.6, 6)  # realised risk 1.40, not the planned 0.55
    assert m.max_r == round((7.64 - 7.00) / (7.00 - 5.6), 3)
    # The old (buggy) fill at 6.15 would have credited (7.64-6.15)/0.55 = 2.71R.
    assert m.max_r < 1.0


def test_thin_risk_setup_stays_finite() -> None:
    # A very tight flag: the 3-tick fill (6.13) sits just above the stop (6.09) -> risk 0.04, thin
    # but finite R (v2 no longer has the legacy 5-tick floor).
    bars = [
        _bar(0, 5.0, 5.90, 4.9, 5.8),  # launch (green)
        _bar(1, 5.8, 6.20, 5.7, 6.1, vol=2000),  # higher-high pole bar 6.20 (heavier volume)
        _bar(2, 6.10, 6.10, 6.09, 6.095),  # flag (red): high 6.10, low 6.09 -> stop 6.09
        _bar(3, 6.12, 7.00, 6.12, 6.9),  # trigger 6.11 <= high 7.00 -> fills at 6.13, runs up
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.setup_found and m.triggered
    assert m.initial_risk == round(6.13 - 6.09, 6)  # 0.04
    assert m.max_r == round((7.00 - 6.13) / 0.04, 3)
