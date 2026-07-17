"""Shared bar primitives for the detection pipeline (#296): pure functions over raw ``Bar``s.

These are the vocabulary the engine's stages are written in — bar colour, body/wick shape, and the
dominant-high search — factored out of the superseded anchored detector so that :mod:`.segment` and
:mod:`.features` depend on a named module rather than reaching into a legacy one for private names.

Every function here is a pure function of the cached raw bars (CLAUDE.md: store-raw /
compute-on-read), so the definitions can change and be recomputed retroactively over history.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..capture import Bar


def classify(bar: Bar) -> str:
    """green (close>open), red (close<open), or flat."""
    if bar.close > bar.open:
        return "green"
    if bar.close < bar.open:
        return "red"
    return "flat"


def is_big_green(bar: Bar) -> bool:
    """A strong-bodied green candle: green with a body >= half its range (#127 'big green')."""
    rng = bar.high - bar.low
    return classify(bar) == "green" and rng > 0 and (bar.close - bar.open) / rng >= 0.5


def upper_wick_frac(bar: Bar) -> float:
    """Upper wick as a fraction of the bar's range (0 = closed at its high, → 1 = all upper wick).
    A zero-range bar has no wick."""
    rng = bar.high - bar.low
    if rng <= 0:
        return 0.0
    return (bar.high - max(bar.open, bar.close)) / rng


def non_increasing(values: Sequence[float]) -> bool:
    return all(values[i] <= values[i - 1] for i in range(1, len(values)))


def find_pole_peak(bars: list[Bar], max_cons: int) -> int | None:
    """Index of the pole peak: the bar the trailing consolidation pulls back from.

    The peak is the **dominant high** of the trailing window — the highest high among the last
    ``max_cons`` bars plus the peak itself — i.e. the top the pullback descends from. Taking the
    dominant high (not merely the nearest local one) is what stops a small up-tick *inside* a deeper
    pullback from being mistaken for the peak: a nearest-peak search collapses the real pole onto
    that up-tick and mis-computes entry/stop/retracement (#163). Returns None if that high lands on
    the last bar (still extending — no completed consolidation). If the dominant high sits earlier
    than ``max_cons`` bars back, the in-window candidate won't form an ascending pole and the
    caller's pole-length / lower-high rules reject it.
    """
    n = len(bars)
    # peak needs a predecessor (>= 1); the consolidation after it spans <= max_cons bars
    lo = max(1, n - 1 - max_cons)
    peak = max(range(lo, n), key=lambda i: bars[i].high)  # dominant high; ties -> earliest
    if peak == n - 1:
        return None  # a new high on the last bar -> still extending, no completed consolidation
    return peak
