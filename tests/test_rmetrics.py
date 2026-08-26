"""Tests for R-multiple measurement (#18)."""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings
from small_cap_stack.rmetrics import EntryResolution, compute_r_metrics, resolve_entry_bar
from tests.support import T0 as _T0
from tests.support import bar as _bar
from tests.support import settings


def _settings(**overrides: object) -> Settings:
    return settings(**overrides)


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
    # The trigger bar opens at 10:15 ET, after the default 09:30 SELECTION cutoff (#694, D-45), so
    # the default settings would report this well-formed setup as not takeable. This test is about
    # R measurement, not selection, so it disables the cutoff — selection has its own tests in
    # test_bullflag_day.py.
    m = compute_r_metrics(bars, _settings(select_entry_cutoff=None))
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


def test_max_gain_pct_is_the_same_peak_as_a_plain_move() -> None:
    """``max_gain_pct`` re-expresses Max R as a fraction of the entry price (#390).

    R divides by the stop distance, so it says how the trade paid relative to what it risked — two
    setups can both print 0.9R off a 3% move and a 25% move. This is the second view: same peak,
    same stop-first walk, denominated in the entry price instead of the risk."""
    bars = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # peak 7.64 vs the 6.13 fill
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.max_gain_pct == round((7.64 - 6.13) / 6.13, 5)
    # Both views measure the SAME peak, so they differ by exactly risk/entry.
    assert m.max_r is not None and m.initial_risk is not None and m.entry_price is not None
    assert m.max_gain_pct == pytest.approx(m.max_r * m.initial_risk / m.entry_price, abs=1e-5)


def test_max_gain_pct_respects_the_stop_first_convention() -> None:
    """A post-stop spike inflates neither view — same walk, same closed position."""
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.5, 5.7, 6.0),  # triggers; peak 6.5
        _bar(4, 6.0, 6.1, 5.5, 5.5),  # stopped here
        _bar(5, 5.5, 9.0, 5.5, 8.9),  # must be ignored
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.max_gain_pct == round((6.5 - 6.13) / 6.13, 5)


def test_max_gain_pct_is_zero_on_a_same_bar_stop() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 6.3, 5.4, 5.5)]  # trigger + stop on one bar
    m = compute_r_metrics(bars, _settings())
    assert m.max_r == 0.0 and m.max_gain_pct == 0.0


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
    assert m.same_bar_stop is True  # ...and the flag says that R is an assumption (#581)


# --- #581: the two measurement caveats, which are different defects wearing the same costume ----


def test_same_bar_stop_is_false_when_the_stop_comes_on_a_later_bar() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.2, 5.7, 6.0),  # triggers
        _bar(4, 6.0, 6.1, 5.5, 5.5),  # stopped the bar AFTER entry -> order is not in doubt
    ]
    m = compute_r_metrics(bars, _settings())
    assert m.stopped_out is True and m.entry_index == 3 and m.stop_index == 4
    assert m.same_bar_stop is False


def test_same_bar_stop_is_false_when_the_trade_never_stops() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    m = compute_r_metrics(bars, _settings(select_window_end=time(11, 59)))
    assert m.stopped_out is False and m.stop_index is None
    assert m.same_bar_stop is False


def test_same_bar_stop_is_false_when_nothing_ever_triggered() -> None:
    """Both indices are None on a setup that formed but never fired — the flag must not fire.

    Reached via the staleness path: `detect_day` returns None outright when no cycle has a usable
    entry, so a shape that is found-but-not-triggered is exactly the faded case (#130).
    """
    bars = [
        *_SETUP,
        _bar(3, 5.7, 5.9, 5.6, 5.8),
        _bar(4, 5.8, 5.9, 5.6, 5.7),
        _bar(5, 5.7, 5.9, 5.6, 5.8),
        _bar(6, 5.8, 5.9, 5.6, 5.7),
        _bar(7, 5.7, 5.9, 5.6, 5.8),
        _bar(8, 5.8, 7.0, 5.8, 6.9),  # +40 min: breaks, but past the 30-min staleness bound
    ]
    m = compute_r_metrics(bars, _settings(), first_hit=_T0)
    assert m.setup_found and not m.triggered
    assert m.entry_index is None and m.stop_index is None
    assert m.same_bar_stop is False


def test_fill_above_entry_bar_high_when_the_fill_price_never_printed() -> None:
    """The 1-tick trigger fires but the 3-tick fill sits above the bar's whole range (#555).

    Entry trigger 6.11, fill 6.13: a bar topping out at 6.12 fires the setup, yet R is measured
    against a price that never traded. Deliberate (a worse fill only understates the edge) — but
    it must be *recorded*, not merely inferable.
    """
    bars = [*_SETUP, _bar(3, 5.7, 6.12, 5.7, 6.0), _bar(4, 6.0, 6.5, 5.95, 6.4)]
    m = compute_r_metrics(bars, _settings(select_window_end=time(11, 59)))
    assert m.triggered and m.entry_index == 3
    assert m.entry_price == 6.13  # above the entry bar's high of 6.12
    assert m.fill_above_entry_bar_high is True
    assert m.same_bar_stop is False  # the two flags are independent


def test_fill_above_entry_bar_high_is_false_on_an_ordinary_fill() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    m = compute_r_metrics(bars, _settings(select_window_end=time(11, 59)))
    assert m.entry_price == 6.13 and m.fill_above_entry_bar_high is False


def test_measurement_flags_default_false_when_no_setup_forms() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]
    m = compute_r_metrics(bars, _settings())
    assert not m.setup_found
    assert m.same_bar_stop is False and m.fill_above_entry_bar_high is False


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


# --- #583: resolving a same-bar entry+stop against 1-min bars -----------------------------------

_BAR_START = _T0
_BAR_END = _T0 + timedelta(minutes=5)


def _min_bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1_000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=i), open=o, high=h, low=low, close=c, volume=vol)


def _resolve(mins: list[Bar]) -> EntryResolution:
    """Trigger 6.11, pessimistic fill 6.13, stop 5.60 — the `_SETUP` levels above."""
    return resolve_entry_bar(
        mins,
        entry_trigger=6.11,
        entry_fill=6.13,
        stop=5.60,
        bar_start=_BAR_START,
        bar_end=_BAR_END,
    )


def test_resolve_ran_when_the_stop_is_untouched_after_the_fill() -> None:
    """The conservative reading was wrong — the only outcome that changes a number."""
    res = _resolve(
        [
            _min_bar(0, 5.90, 6.00, 5.85, 5.95),
            _min_bar(1, 5.95, 6.20, 5.90, 6.15),  # trigger prints here
            _min_bar(2, 6.15, 6.40, 6.10, 6.35),
            _min_bar(3, 6.35, 6.50, 6.30, 6.45),
            _min_bar(4, 6.45, 6.60, 5.70, 6.50),  # dips, but never to the 5.60 stop
        ]
    )
    assert res.outcome == "ran" and res.ran is True
    assert res.entry_at == _T0 + timedelta(minutes=1)
    assert res.entry_price == 6.13  # the pessimistic fill, not the minute's 5.95 open
    assert res.synthetic_bar is not None
    # The re-cut bar spans only the part of the 5-min bar we were actually in, and opens at the
    # realised fill so `_measure` reproduces the same entry.
    assert res.synthetic_bar.open == 6.13
    assert res.synthetic_bar.high == 6.60
    assert res.synthetic_bar.low == 5.70  # the pre-fill 5.85 low is excluded — we weren't in yet
    assert res.synthetic_bar.start == _BAR_START


def test_resolve_confirmed_stop_when_the_stop_follows_the_fill() -> None:
    """The conservative reading stands — now evidenced rather than assumed."""
    res = _resolve(
        [
            _min_bar(0, 5.90, 6.00, 5.85, 5.95),
            _min_bar(1, 5.95, 6.20, 5.90, 6.15),  # fills
            _min_bar(2, 6.15, 6.20, 5.50, 5.55),  # then stops, still inside the 5-min bar
            _min_bar(3, 5.55, 5.60, 5.40, 5.45),
        ]
    )
    assert res.outcome == "confirmed_stop"
    assert res.entry_at == _T0 + timedelta(minutes=1)
    assert res.stopped_at == _T0 + timedelta(minutes=2)
    assert res.synthetic_bar is None and res.ran is False


def test_resolve_ambiguous_when_one_minute_spans_both_levels() -> None:
    """Irreducible at this resolution: a finer grid narrows the window, it never removes it."""
    res = _resolve(
        [
            _min_bar(0, 5.90, 6.00, 5.85, 5.95),
            _min_bar(1, 5.95, 6.20, 5.50, 5.60),  # trigger AND stop inside one minute
        ]
    )
    assert res.outcome == "ambiguous_same_minute"
    assert res.entry_at == _T0 + timedelta(minutes=1)
    assert res.synthetic_bar is None and res.ran is False


def test_resolve_is_unresolved_without_minute_bars_or_a_trigger_print() -> None:
    assert _resolve([]).outcome == "unresolved"
    # Bars exist but never reach the 6.11 trigger.
    never = [_min_bar(0, 5.90, 6.00, 5.85, 5.95), _min_bar(1, 5.95, 6.05, 5.90, 6.00)]
    assert _resolve(never).outcome == "unresolved"
    # Bars exist but all fall outside [bar_start, bar_end) — a neighbouring 5-min bar's minutes.
    outside = [_min_bar(6, 5.95, 7.00, 5.90, 6.90), _min_bar(7, 6.90, 7.20, 6.80, 7.10)]
    assert _resolve(outside).outcome == "unresolved"


def test_resolve_keys_on_the_trigger_not_the_pessimistic_fill() -> None:
    """BIYA 2026-05-22: the trigger printed, the +3-tick fill never did — still a fill (#583).

    Keying the search on `entry_fill` printing would call a real fire a non-event. The fill is a
    deliberately pessimistic *price*; a marketable limit at the trigger fills at or better than it.
    """
    mins = [
        _min_bar(0, 5.90, 6.00, 5.85, 5.95),
        _min_bar(
            1, 5.95, 6.12, 5.90, 6.05
        ),  # high 6.12: over the 6.11 trigger, under the 6.13 fill
        _min_bar(2, 6.05, 6.30, 6.00, 6.25),
    ]
    res = _resolve(mins)
    assert res.outcome == "ran"
    assert res.entry_at == _T0 + timedelta(minutes=1)
    assert res.entry_price == 6.13  # booked at the worse price we never saw print — the point


def test_resolve_gap_through_takes_the_minute_open() -> None:
    """A minute that opens above the fill fills there instead — the same rule `_measure` applies."""
    mins = [_min_bar(0, 5.90, 6.00, 5.85, 5.95), _min_bar(1, 6.50, 6.80, 6.40, 6.70)]
    res = _resolve(mins)
    assert res.outcome == "ran" and res.entry_price == 6.50
