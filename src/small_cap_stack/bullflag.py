"""Bull-flag detection (issue #16): pure functions over a 5-min OHLCV series.

Strategy pattern (Warrior-style, see README). **Redefined 2026-07-03 (issue #127)** from the
trader's annotated chart notes (`notes.md`):

- **Pole** = a run of **higher highs** (each bar's high above the previous bar's). It is *not*
  colour-gated — a non-green bar is allowed as long as the high still rises. A pole can be as short
  as a **single higher-high bar** or as long as ``max_pole`` (SNDQ counted 7). ``pole_len`` is the
  number of higher highs; the run's launch bar (the first bar of the ascending sequence) sets the
  pole base for the retracement.
- **Flag** = a genuine pullback of ``1..max_flag`` consolidation bars that (a) stays below the pole
  peak and (b) makes **lower highs** (a multi-bar flag drifts down in its highs; a single-bar flag
  sits below the peak already). The trader tracks *highs*, not lows, for the consolidation.
- **Retracement gate**: reject flags retracing more than ``max_retracement`` of the pole's height
  (default 50% — a deeper pullback retraces "back through the pole": AHMA/CLRO/CYH/DJT). Measured on
  the flag low (the risk), against the pole base.
- **Volume**: the pole's peak bar volume **must exceed** the consolidation's peak bar volume (a hard
  constraint). Whether the consolidation volume is reducing (preferable) is recorded
  (``cons_vol_reducing``) but not gated — it may be flat.

Entry = **5 ticks above the high of the last complete consolidation candle** (decisions.md); the
stop is the consolidation (flag) low. Pure and replayable over the cached raw bars, so the
definition can change and be recomputed retroactively.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .capture import Bar
from .config import Settings


def classify(bar: Bar) -> str:
    """green (close>open), red (close<open), or flat."""
    if bar.close > bar.open:
        return "green"
    if bar.close < bar.open:
        return "red"
    return "flat"


@dataclass(frozen=True)
class BullFlag:
    pole_len: int  # number of higher highs in the pole (>= 1)
    flag_len: int  # number of consolidation candles (#98 — bucket profitability by this)
    breakout_level: float  # high of the last complete consolidation candle
    entry_trigger: float  # breakout_level + entry offset (5 ticks)
    stop: float  # consolidation (flag) low
    # How deep the flag pulls back into the pole, as a fraction of the pole's height (#98):
    # 0.0 = held at the pole high, → max_retracement at the shallow-rejection boundary. Always in
    # (0, max_retracement] for a valid setup (flag_low is below the peak but held above the base).
    retracement: float
    cons_vol_reducing: bool  # consolidation volume is non-increasing (preferable, soft signal #127)


def _find_pole_peak(bars: list[Bar], max_flag: int) -> int | None:
    """Index of the pole peak: the bar the trailing flag pulls back from.

    Grow the trailing flag from the end (up to ``max_flag`` bars); the peak is the first bar such
    that it (a) stands above every flag bar's high — nothing after it broke out again — and (b) is a
    **higher high than its own predecessor** (it is the top of an ascending thrust). Condition (b)
    is what lets a *descending* flag work: a classic flag makes lower highs, so its earlier bars sit
    above its later ones yet still below the peak — they fail (b) and stay in the flag rather than
    being mistaken for the peak. Returns None if the last bar is still extending (a new high) or the
    pullback is longer than ``max_flag``.
    """
    n = len(bars)
    flag_max_high = float("-inf")
    for flag_len in range(1, min(max_flag, n - 2) + 1):
        p = n - 1 - flag_len  # bar just before the flag (>= 1, so it has a predecessor)
        flag_max_high = max(flag_max_high, bars[p + 1].high)
        if bars[p].high > flag_max_high and bars[p].high > bars[p - 1].high:
            return p
    return None


def _flag_makes_lower_highs(flag: list[Bar]) -> bool:
    """A genuine pullback drifts down in its HIGHS. A single-bar flag already sits below the pole
    peak, so it qualifies; a multi-bar flag must have non-increasing highs and a net lower high
    (rejects consolidations that tick back up, e.g. ETHT/NBIZ)."""
    if len(flag) == 1:
        return True
    highs = [b.high for b in flag]
    non_increasing = all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))
    return non_increasing and highs[-1] < highs[0]


def _non_increasing(values: Sequence[float]) -> bool:
    return all(values[i] <= values[i - 1] for i in range(1, len(values)))


def detect(
    bars: list[Bar],
    *,
    min_pole: int = 1,
    max_pole: int = 8,
    max_flag: int = 6,
    max_retracement: float = 0.50,
    entry_offset: float = 0.05,
) -> BullFlag | None:
    """Detect a bull flag at the END of the series (the just-formed setup), else None."""
    if len(bars) < min_pole + 2:  # launch bar + >= min_pole higher highs + >= 1 flag bar
        return None

    peak = _find_pole_peak(bars, max_flag)
    if peak is None:
        return None  # still extending, or the pullback is longer than max_flag -> no flag

    flag = bars[peak + 1 :]

    # Pole: the ascending-highs run ending at the peak (each high above the previous), capped at
    # max_pole higher highs. The run's first bar is the launch/base; pole_len counts the higher
    # highs. Colour-agnostic — a non-green bar is fine as long as the high still steps up.
    start = peak
    while start - 1 >= 0 and bars[start].high > bars[start - 1].high and (peak - start) < max_pole:
        start -= 1
    pole = bars[start : peak + 1]
    pole_len = peak - start  # number of higher highs (>= 1, since the peak rose above start)
    if pole_len < min_pole:
        return None

    if not _flag_makes_lower_highs(flag):
        return None  # consolidation didn't pull back in its highs -> not a flag

    pole_base = bars[start].low
    pole_high = bars[peak].high
    flag_low = min(b.low for b in flag)
    if flag_low <= pole_base:
        return None  # pullback erased the pole (retraced through its base)

    retracement = (pole_high - flag_low) / (pole_high - pole_base)
    if retracement > max_retracement:
        return None  # pullback too deep -> flag invalidated ("back through the pole")

    if max(b.volume for b in pole) <= max(b.volume for b in flag):
        return None  # consolidation volume matched/exceeded the pole -> not a clean flag

    breakout = flag[-1].high
    return BullFlag(
        pole_len=pole_len,
        flag_len=len(flag),
        breakout_level=round(breakout, 4),
        entry_trigger=round(breakout + entry_offset, 4),
        stop=round(flag_low, 4),
        retracement=round(retracement, 4),
        cons_vol_reducing=_non_increasing([b.volume for b in flag]),
    )


def detect_with_settings(bars: list[Bar], settings: Settings) -> BullFlag | None:
    return detect(
        bars,
        min_pole=settings.bull_flag_min_pole,
        max_pole=settings.bull_flag_max_pole,
        max_flag=settings.bull_flag_max_flag,
        max_retracement=settings.bull_flag_max_retracement,
        entry_offset=settings.entry_offset_ticks * settings.tick_size,
    )
