"""Tests for stage 2 segmentation (#177): bars+tokens -> base/POLE/CONSOLIDATION or None."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import Segment, segment_at_end, tokenize
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _bars(highs: list[float], *, colors: list[str] | None = None) -> list[Bar]:
    """Bars carrying meaningful highs. GREEN with a full body (open=low) by default, so grammar
    tests (H/L/E boundaries, longest-match, retracement) aren't blocked by the color/thrust rule.
    Pass ``colors`` (same length as ``highs``, values "green"/"red"/"doji") to test that rule
    directly: green = full body (open=low), red = full red body (open=high), doji = zero body."""
    colors = colors or ["green"] * len(highs)
    bars = []
    for i, (h, c) in enumerate(zip(highs, colors, strict=True)):
        low = h - 1
        if c == "green":
            o, close = low, h
        elif c == "weak_green":  # green, but body < half the range (fails the thrust threshold)
            o, close = h - 0.1, h
        elif c == "red":
            o, close = h, low
        else:  # doji: zero body (flat, neither green nor red)
            mid = (h + low) / 2
            o = close = mid
        start = _T0 + timedelta(minutes=5 * i)
        bars.append(Bar(start=start, open=o, high=h, low=low, close=close, volume=1000.0))
    return bars


def _seg(
    highs: list[float], *, max_pole: int = 4, max_cons: int = 4, colors: list[str] | None = None
) -> Segment | None:
    bars = _bars(highs, colors=colors)
    return segment_at_end(bars, tokenize(bars, eps=0.01), max_pole=max_pole, max_cons=max_cons)


def test_canonical_hhlll() -> None:
    seg = _seg([4.0, 5.0, 6.0, 5.5, 5.2, 5.0])  # HHLLL
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.cons_end_idx) == (0, 2, 5)
    assert (seg.pole_len, seg.cons_len) == (2, 3)
    assert seg.tokens == ("H", "H", "L", "L", "L")


def test_single_higher_high_single_flag() -> None:
    seg = _seg([4.0, 5.0, 4.5])  # HL
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.cons_end_idx) == (0, 1, 2)
    assert (seg.pole_len, seg.cons_len) == (1, 1)


def test_dominant_peak_not_uptick_163() -> None:
    # A mid-pullback up-tick (5.5 -> 5.8) stays below the real peak (6.0). The dominant-high peak is
    # bar 2, whose consolidation then contains a higher-high step -> not a clean flag -> None. The
    # up-tick must NOT be mistaken for the peak (that was #163).
    assert _seg([4.0, 5.0, 6.0, 5.5, 5.8, 5.2]) is None


def test_pole_extends_fully() -> None:
    seg = _seg([4.0, 5.0, 6.0, 7.0, 6.5])  # HHHL
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.pole_len, seg.cons_len) == (0, 3, 3, 1)


def test_equal_high_not_allowed_in_pole() -> None:
    # H E H L: the E splits the pole, so the pole is only the final strict-H step (base at bar 2),
    # NOT the whole run back to bar 0. E is a consolidation-only token.
    seg = _seg([4.0, 5.0, 5.0, 6.0, 5.5])
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.pole_len, seg.cons_len) == (2, 3, 1, 1)


def test_equal_high_permissive_in_cons() -> None:
    seg = _seg([4.0, 5.0, 6.0, 5.5, 5.5])  # H H L E -> flat step inside the pullback is tolerated
    assert seg is not None
    assert (seg.pole_len, seg.cons_len) == (2, 2)
    assert seg.tokens == ("H", "H", "L", "E")


def test_all_equal_tail_rejected() -> None:
    assert _seg([4.0, 5.0, 6.0, 6.0, 6.0]) is None  # H H E E -> flat top, no net lower high


def test_all_equal_ascent_is_not_a_pole() -> None:
    assert _seg([5.0, 5.0, 4.0]) is None  # E L -> no strict higher high -> no pole


def test_still_extending_returns_none() -> None:
    assert _seg([4.0, 5.0, 6.0]) is None  # HH -> new high on the last bar, no completed flag


def test_pole_length_cap() -> None:
    # Six higher highs then a pullback; max_pole=4 keeps the trailing 4 higher highs (bars 2–5)
    # above the launch bar (bar 1); bar 0's step would be a 5th higher high, over the cap.
    seg = _seg([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 5.5], max_pole=4)
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.pole_len, seg.cons_len) == (1, 5, 4, 1)


def test_consolidation_exactly_max_cons() -> None:
    seg = _seg([4.0, 5.0, 4.5, 4.2, 4.1, 4.0], max_cons=4)  # H L L L L
    assert seg is not None
    assert (seg.pole_len, seg.cons_len) == (1, 4)


def test_pullback_beyond_window_has_no_pole() -> None:
    # A long monotone decline: the true peak (bar 0) sits outside the trailing max_cons+1 window, so
    # no ascending pole exists within reach -> None (the too-long pullback is rejected).
    assert _seg([10.0, 4.0, 3.0, 2.0, 1.0, 0.5], max_cons=4) is None


def test_flat_noise_never_yields_zero_pole_span() -> None:
    # Real #181 regression (IVF/ITRG): a long near-flat run on an illiquid name. With E barred from
    # the pole, the base can't drift onto a bar at/above the peak, so peak.high - base.low is always
    # > 0 (the old E-tolerant walk gave a zero span and crashed the retracement division).
    highs = [1.26, 1.259, 1.25, 1.25, 1.25, 1.26, 1.25]
    lows = [1.26, 1.25, 1.24, 1.2319, 1.24, 1.24, 1.24]
    bars = [
        Bar(start=_T0 + timedelta(minutes=5 * i), open=lo, high=h, low=lo, close=h, volume=1000.0)
        for i, (h, lo) in enumerate(zip(highs, lows, strict=True))
    ]
    for i in range(2, len(bars)):
        seg = segment_at_end(
            bars[: i + 1], tokenize(bars[: i + 1], eps=0.01), max_pole=4, max_cons=4
        )
        if seg is not None:
            assert bars[seg.peak_idx].high - bars[seg.base_idx].low > 0


def test_too_few_bars_or_mismatched_tokens() -> None:
    assert _seg([4.0, 5.0]) is None  # only two bars
    bars = _bars([4.0, 5.0, 4.5])
    assert segment_at_end(bars, ["H"], max_pole=4, max_cons=4) is None  # tokens != len(bars)-1


def test_red_peak_disqualifies_the_candidate() -> None:
    # HL, but the peak bar (index 1) is RED (a new high that reverses and closes weak within the
    # bar) -> not a genuine thrust -> no valid pole at all, even though the direction step is H
    # (#182/#190: IRE's shooting-star top).
    assert _seg([4.0, 5.0, 4.5], colors=["green", "red", "green"]) is None


def test_doji_bar_breaks_pole_extension() -> None:
    # Three higher highs (H H H) then a pullback, but the bar immediately BEFORE the peak (index 2)
    # is a doji (zero body) -> the walk can't extend past it (it becomes the base, excluded from
    # the pole), so the pole is just the final strict-H step (base=2, peak=3, pole_len=1) instead
    # of the full 3-step run (#182/#190: MUZ/CRCG/CONL — the doji plays the role of MUZ's bar4).
    seg = _seg(
        [4.0, 5.0, 5.8, 6.5, 6.0],
        colors=["green", "green", "doji", "green", "green"],
    )
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.pole_len, seg.cons_len) == (2, 3, 1, 1)


def test_weak_bodied_peak_is_still_a_valid_single_bar_pole() -> None:
    # The PEAK itself only needs to be green (ANY body size, not the full thrust threshold) — a
    # weak-bodied (but still green) peak forms a valid single-bar pole, matching the existing
    # "single bar pole" tolerance. Only extending PAST the peak's predecessor requires the full
    # thrust body threshold (>=0.5).
    seg = _seg([4.0, 5.0, 4.5], colors=["green", "weak_green", "green"])
    assert seg is not None
    assert (seg.base_idx, seg.peak_idx, seg.pole_len) == (0, 1, 1)


def test_flat_or_red_peak_disqualifies_even_with_valid_direction() -> None:
    assert _seg([4.0, 5.0, 4.5], colors=["green", "doji", "green"]) is None  # flat peak, not green
