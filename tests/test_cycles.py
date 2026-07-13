"""Tests for the engine-v2 exhaustion primitives (#211 port of the visual-review rules, #102/#194).

segment_cycles (loose H/E/L walk) -> significant_cycles (green thrust + volume floor) ->
contiguous_prior_cycles / prior_cycle_count (the contiguous run abutting the pole). Case names in
parentheses are the reviewed opportunities each rule was validated against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag.cycles import (
    Cycle,
    contiguous_prior_cycles,
    prior_cycle_count,
    segment_cycles,
    significant_cycles,
)
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def _bar(i: int, *, vol: float = 200_000.0, green: bool = True) -> Bar:
    """A bar whose only meaningful axes are volume and body strength (for significance). ``green``
    True = a full-bodied green thrust (``_is_big_green``); False = a zero-body doji."""
    high, low = 10.0, 9.0
    if green:
        o, c = low, high  # full green body -> body == range -> big green
    else:
        o = c = (high + low) / 2  # doji: zero body
    return Bar(
        start=_T0 + timedelta(minutes=5 * i), open=o, high=high, low=low, close=c, volume=vol
    )


# ---- segment_cycles: the loose grammar ----


def test_segment_cycles_two_closed_cycles() -> None:
    # H L H L H : two flags then a new pole. The boundary H both ends a cycle and starts the next.
    cycles = segment_cycles(["H", "L", "H", "L", "H"])
    assert len(cycles) == 3
    assert (cycles[0].pole_start, cycles[0].peak, cycles[0].cons_end, cycles[0].breakout) == (
        0,
        1,
        2,
        3,
    )
    assert (cycles[1].pole_start, cycles[1].peak, cycles[1].cons_end, cycles[1].breakout) == (
        2,
        3,
        4,
        5,
    )
    # the trailing cycle is still mid-pole (opened by the final H) -> open (breakout None)
    assert cycles[2].pole_start == 4 and cycles[2].peak == 5 and cycles[2].breakout is None


def test_segment_cycles_multibar_pole_and_flag() -> None:
    # H H L L H : one cycle with a 2-step pole and a 2-bar flag, then a new open pole.
    cycles = segment_cycles(["H", "H", "L", "L", "H"])
    assert len(cycles) == 2
    c = cycles[0]
    assert (c.pole_start, c.peak, c.cons_start, c.cons_end, c.breakout) == (0, 2, 3, 4, 5)


def test_segment_cycles_leading_noise_and_equal_highs() -> None:
    # Leading L/E before the first H are ignored (searching state); E is tolerated inside the flag.
    cycles = segment_cycles(["L", "E", "H", "L", "E", "H"])
    assert len(cycles) == 2
    assert cycles[0].pole_start == 2 and cycles[0].peak == 3  # first pole starts at the first H
    assert cycles[0].cons_end == 5  # flag L,E ends at the next H (index 5)


def test_segment_cycles_empty_and_no_pole() -> None:
    assert segment_cycles([]) == []
    assert segment_cycles(["L", "L", "E"]) == []  # never a higher high -> no cycle


# ---- significant_cycles: structure AND volume ----

_FLOOR = 50_000.0


def _cycle(pole_start: int, peak: int) -> Cycle:
    return Cycle(
        pole_start=pole_start, peak=peak, cons_start=peak + 1, cons_end=peak + 1, breakout=None
    )


def test_significant_keeps_green_pole_over_floor() -> None:
    bars = [_bar(0), _bar(1, vol=80_000, green=True), _bar(2, vol=80_000, green=True)]
    assert significant_cycles(bars, [_cycle(0, 2)], _FLOOR) == [_cycle(0, 2)]


def test_significant_drops_doji_churn_even_at_high_volume() -> None:
    # SNDQ: flat/doji bars trading heavily are NOT a pump — no green thrust body -> dropped.
    bars = [_bar(0), _bar(1, vol=500_000, green=False), _bar(2, vol=500_000, green=False)]
    assert significant_cycles(bars, [_cycle(0, 2)], _FLOOR) == []


def test_significant_drops_tiny_green_blip_under_floor() -> None:
    # ARCT/FCEL/SDOT: a real green body but sub-floor volume -> not a real pump.
    bars = [_bar(0), _bar(1, vol=5_000, green=True), _bar(2, vol=5_000, green=True)]
    assert significant_cycles(bars, [_cycle(0, 2)], _FLOOR) == []


def test_significant_checks_whole_span_not_just_peak_bar() -> None:
    # FWDI: volume front-loads on the breakout bar and tapers to the peak. Checking the whole pole
    # span (not just the peak bar) keeps it; peak-only would wrongly drop it.
    bars = [_bar(0), _bar(1, vol=265_000, green=True), _bar(2, vol=10_000, green=True)]
    assert significant_cycles(bars, [_cycle(0, 2)], _FLOOR) == [_cycle(0, 2)]


def test_significant_needs_a_green_bar_and_volume_together() -> None:
    # A green bar under the floor plus a doji over the floor -> neither is a green-AND-loud bar,
    # but the rule is "any green in span AND any bar over floor", so this span (green bar1 + loud
    # doji bar2) qualifies: real body present, real volume present.
    bars = [_bar(0), _bar(1, vol=5_000, green=True), _bar(2, vol=500_000, green=False)]
    assert significant_cycles(bars, [_cycle(0, 2)], _FLOOR) == [_cycle(0, 2)]


# ---- contiguous_prior_cycles / prior_cycle_count ----


def _cyc(pole_start: int, peak: int, cons_end: int) -> Cycle:
    return Cycle(
        pole_start=pole_start, peak=peak, cons_start=None, cons_end=cons_end, breakout=None
    )


def test_prior_count_zero_when_no_priors() -> None:
    bars = [_bar(i) for i in range(20)]
    assert prior_cycle_count(bars, [], pole_base_idx=10) == 0


def test_prior_count_contiguous_chain() -> None:
    # Three cycles abutting each other and the pole (gap 0-1) -> all count.
    bars = [_bar(i) for i in range(20)]
    sig = [_cyc(2, 3, 4), _cyc(4, 5, 6), _cyc(6, 7, 8)]  # pole base at 9
    assert prior_cycle_count(bars, sig, pole_base_idx=9) == 3


def test_prior_count_gap_breaks_the_chain() -> None:
    # MARA: a disconnected earlier pump (gap of 7 bars) does NOT count; the chain stops at the gap.
    bars = [_bar(i) for i in range(40)]
    sig = [_cyc(0, 1, 2), _cyc(20, 25, 28), _cyc(28, 29, 30)]  # pole base at 31
    counted = contiguous_prior_cycles(bars, sig, pole_base_idx=31)
    assert [c.peak for c in counted] == [25, 29]  # the 09:00-style blip at peak 1 is dropped
    assert prior_cycle_count(bars, sig, pole_base_idx=31) == 2


def test_prior_count_fading_sequence_still_counts() -> None:
    # OPEN: a DESCENDING run of ever-lower pumps is still exhaustion (no ascending requirement).
    bars = [_bar(i) for i in range(20)]
    # peaks descend 8 -> 6 -> 4 but abut the pole -> all three count
    sig = [_cyc(2, 3, 4), _cyc(4, 5, 6), _cyc(6, 7, 8)]
    assert prior_cycle_count(bars, sig, pole_base_idx=9) == 3


def test_prior_count_ignores_cycles_at_or_after_the_pole() -> None:
    bars = [_bar(i) for i in range(20)]
    sig = [_cyc(6, 7, 8), _cyc(10, 12, 13)]  # second cycle peaks AFTER the pole base
    assert prior_cycle_count(bars, sig, pole_base_idx=9) == 1


def test_prior_count_first_prior_must_abut_the_pole() -> None:
    # If the nearest significant cycle is itself gapped from the pole, nothing counts (CONL: the
    # prior drift sits a few bars back from the target pole).
    bars = [_bar(i) for i in range(20)]
    sig = [_cyc(2, 3, 4), _cyc(5, 6, 7)]  # cons_end 7, pole base at 10 -> gap 3 -> breaks
    assert prior_cycle_count(bars, sig, pole_base_idx=10) == 0
