"""Engine-v2 rule ports (#211 stage 2): the greedy anchored pole (segment.refine_pole),
the half-tick tokenisation tolerance (tokens.token_eps), and the peak_green gate (#196).

refine_pole is the full-day detector's pole finder — anchored to a peak the greedy cycle walk found,
sharing segment_at_end's colour/thrust extension rule but NOT its dominant-peak/green-peak checks
(a red/flat peak forms a pole here and is rejected downstream by the peak_green gate).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import (
    Segment,
    evaluate,
    extract,
    refine_pole,
    token_eps,
    tokenize,
)
from small_cap_stack.bullflag.gates import passed
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings

_T0 = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def _bars(highs: list[float], *, colors: list[str] | None = None) -> list[Bar]:
    """Bars carrying meaningful highs; full green body by default (open=low). ``colors`` values:
    green / red / doji (zero body) to exercise the thrust/colour rules."""
    colors = colors or ["green"] * len(highs)
    out = []
    for i, (h, c) in enumerate(zip(highs, colors, strict=True)):
        low = h - 1.0
        if c == "green":
            o, close = low, h
        elif c == "red":
            o, close = h, low
        else:  # doji: zero body
            o = close = (h + low) / 2
        out.append(
            Bar(
                start=_T0 + timedelta(minutes=5 * i),
                open=o,
                high=h,
                low=low,
                close=close,
                volume=1000.0,
            )
        )
    return out


def _refine(highs: list[float], peak: int, *, max_pole: int = 4, colors: list[str] | None = None):
    bars = _bars(highs, colors=colors)
    return refine_pole(bars, tokenize(bars, eps=0.01), peak, max_pole=max_pole)


# ---- refine_pole ----


def test_single_bar_pole() -> None:
    assert _refine([4.0, 5.0, 4.5], peak=1) == (0, 1)


def test_multi_bar_thrust_pole_extends() -> None:
    # three green thrusts H H H into the peak -> base 0, pole_len 3
    assert _refine([4.0, 5.0, 6.0, 6.5], peak=3) == (0, 3)


def test_doji_bar_stops_extension_and_becomes_base() -> None:
    # H H H, but the bar before the peak (index 2) is a doji -> walk stops (base=2, pole_len 1)
    assert _refine([4.0, 5.0, 5.8, 6.5], peak=3, colors=["green", "green", "doji", "green"]) == (
        2,
        1,
    )


def test_red_peak_still_forms_a_pole() -> None:
    # THE key difference from segment_at_end: a red peak is NOT skipped here (rejected later by
    # the peak_green gate), so refine_pole still returns a pole anchored to it.
    assert _refine([4.0, 5.0, 4.5], peak=1, colors=["green", "red", "green"]) == (0, 1)


def test_no_higher_high_into_peak_is_none() -> None:
    # peak at index 2 but the step into it (5.0 -> 4.5) is L, not H -> no pole
    assert _refine([4.0, 5.0, 4.5], peak=2) is None


def test_max_pole_cap() -> None:
    # five green higher highs; max_pole=2 keeps only the trailing two steps
    assert _refine([1.0, 2.0, 3.0, 4.0, 5.0], peak=4, max_pole=2) == (2, 2)


def test_max_pole_zero_disables() -> None:
    assert _refine([4.0, 5.0, 4.5], peak=1, max_pole=0) is None


# ---- token_eps ----


def test_token_eps_is_half_a_tick() -> None:
    s = Settings()
    assert token_eps(s) == s.tick_size / 2 == 0.005


def test_half_tick_eps_keeps_a_one_tick_move_directional() -> None:
    # At eps = 1 tick a +0.01 higher high is E; at half a tick it is a directional H (the SNDQ fix).
    bars = _bars([2.13, 2.14])  # +0.01 step
    assert tokenize(bars, eps=Settings().tick_size) == ["E"]  # full-tick eps swallows it
    assert tokenize(bars, eps=token_eps(Settings())) == ["H"]  # half-tick keeps it directional


# ---- peak_green gate (via a red-peaked segment) ----


def test_peak_green_gate_fails_on_a_red_peak() -> None:
    # A red peak forms a pole (refine_pole) but must be REJECTED by the peak_green gate.
    bars = _bars([3.0, 6.0, 5.9, 5.85], colors=["green", "red", "green", "red"])
    tokens = tokenize(bars, eps=0.01)
    pole = refine_pole(bars, tokens, peak=1, max_pole=4)
    assert pole == (0, 1)
    base, pole_len = pole
    seg = Segment(
        base_idx=base,
        peak_idx=1,
        cons_end_idx=3,
        tokens=tuple(tokens[base:]),
        pole_len=pole_len,
        cons_len=2,
    )
    fv = extract(bars, seg)
    assert fv.peak_is_green is False
    gates = evaluate(
        fv, max_pole=4, max_cons=4, max_peak_wick=0.5, min_pole_pct=0.0, max_retracement=0.5
    )
    peak_green = next(g for g in gates if g.name == "peak_green")
    assert peak_green.passed is False
    assert passed(gates) is False  # a red-peaked shape is not takeable
