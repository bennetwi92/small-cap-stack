"""Engine-v2 rule ports (#211 stage 2): the greedy anchored pole (segment.refine_pole),
the half-tick tokenisation tolerance (tokens.token_eps), and the peak_green gate (#196).

refine_pole is the full-day detector's pole finder — anchored to a peak the greedy cycle walk found,
sharing segment_at_end's colour/thrust extension rule but NOT its dominant-peak/green-peak checks
(a red/flat peak forms a pole here and is rejected downstream by the peak_green gate).
"""

from __future__ import annotations

from datetime import timedelta

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
from tests.support import T0 as _T0
from tests.support import settings


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
    s = settings()
    assert token_eps(s) == s.tick_size / 2 == 0.005


def test_half_tick_eps_keeps_a_one_tick_move_directional() -> None:
    # At eps = 1 tick a +0.01 higher high is E; at half a tick it is a directional H (the SNDQ fix).
    bars = _bars([2.13, 2.14])  # +0.01 step
    assert tokenize(bars, eps=settings().tick_size) == ["E"]  # full-tick eps swallows it
    assert tokenize(bars, eps=token_eps(settings())) == ["H"]  # half-tick keeps it directional


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


# --- #585: a quiet bar must not be swallowed into the pole ---------------------------------------
#
# The discriminating pair, from the real record. AKAN 2026-05-22's 08:00 bar contributed 6.8% of
# its pole's advance and must be rejected; the WULF 09:25 extension a reviewed fixture KEEPS
# contributed 10.6%. The default 0.08 sits between them, and the window is only that wide — every
# other axis tested (step %, ATR multiple, body size, volume) ranks these two the wrong way round.


def _pole_with_step_share(share: float) -> list[Bar]:
    """Three bars whose middle one contributes exactly ``share`` of the pole's advance."""
    base_high, peak_high = 10.0, 20.0
    return _bars([base_high, base_high + share * (peak_high - base_high), peak_high])


def _refined(bars: list[Bar], share: float) -> tuple[int, int] | None:
    return refine_pole(bars, tokenize(bars, eps=0.005), 2, max_pole=4, min_step_share=share)


def test_a_quiet_step_does_not_extend_the_pole() -> None:
    """AKAN-shaped: 6.8% of the advance is a pause, not thrust — the walk stops, it becomes base."""
    assert _refined(_pole_with_step_share(0.0677), 0.08) == (1, 1)


def test_a_real_step_still_extends_the_pole() -> None:
    """WULF-shaped: 10.6% is a genuine part of the move and must survive the default."""
    assert _refined(_pole_with_step_share(0.1058), 0.08) == (0, 2)


def test_the_rule_is_off_by_default_so_shape_only_callers_are_unchanged() -> None:
    """`min_step_share=0.0` reproduces the pre-#585 walk, which `segment_at_end` still relies on."""
    assert _refined(_pole_with_step_share(0.0677), 0.0) == (0, 2)


def test_step_share_is_measured_against_the_pole_it_would_create() -> None:
    """Not against the bar, the prior high, or a trailing baseline — the whole point of the axis.

    Both poles below take the same absolute 0.68 step. The first is a 10.0-wide advance (6.8% —
    blocked), the second a 2.0-wide one (34% — kept). An absolute or trailing-relative rule cannot
    tell them apart, which is why every such alternative broke a reviewed fixture.
    """
    wide = _bars([10.0, 10.68, 20.0])
    narrow = _bars([10.0, 10.68, 12.0])
    assert _refined(wide, 0.08) == (1, 1)  # blocked
    assert _refined(narrow, 0.08) == (0, 2)  # kept


def test_a_non_positive_span_blocks_the_extension() -> None:
    """A peak at or below the would-be base cannot be judged, so it must not extend the pole."""
    flat = _bars([20.0, 20.5, 20.0])
    assert refine_pole(flat, ["H", "L"], 2, max_pole=4, min_step_share=0.08) is None
