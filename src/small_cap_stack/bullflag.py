"""Bull-flag detection (issue #16): pure functions over a 5-min OHLCV series.

Strategy pattern (Warrior-style, see README). **Redefined 2026-07-03 (issue #127)** from the
trader's annotated chart notes (`notes.md`), which showed the earlier "≤2 green candles" pole was
wrong:

- **Pole** = a run of **higher highs** (each bar's high above the previous bar's), length
  ``min_pole..max_pole``. It is *not* colour-gated — an occasional non-green bar is allowed as long
  as the high still makes a higher high (poles are often wicky; the trader counted a 7-bar pole on
  SNDQ). Rising volume across the pole is recorded (``vol_increasing``) but not gated.
- **Flag** = a genuine pullback of ``1..max_flag`` consolidation bars that (a) stays below the pole
  peak, (b) **makes a lower low** (a multi-bar flag dips below its first bar's low; a single-bar
  flag must be a red pullback candle — the trader rejects consolidations with "no lower lows", e.g.
  ETHT/NBIZ), and (c) retraces no more than ``max_retracement`` of the pole's height (default 50% —
  a deeper pullback retraces "back through the pole" and invalidates the setup: AHMA/CLRO/CYH/DJT).

Entry = **5 ticks above the high of the last complete consolidation candle** (decisions.md); the
stop is the consolidation (flag) low. Pure and replayable over the cached raw bars, so the
definition can change and be recomputed retroactively.
"""

from __future__ import annotations

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
    pole_len: int  # number of higher-high bars in the pole
    flag_len: int  # number of consolidation candles (#98 — bucket profitability by this)
    breakout_level: float  # high of the last complete consolidation candle
    entry_trigger: float  # breakout_level + entry offset (5 ticks)
    stop: float  # consolidation (flag) low
    # How deep the flag pulls back into the pole, as a fraction of the pole's height (#98):
    # 0.0 = held at the pole high, → max_retracement at the shallow-rejection boundary. Always in
    # (0, max_retracement] for a valid setup (flag_low is below the peak but held above the base).
    retracement: float
    vol_increasing: bool  # pole volume rose from its first bar to the peak (a quality signal, SNDQ)


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


def _flag_makes_lower_low(flag: list[Bar]) -> bool:
    """A genuine pullback: a multi-bar flag dips below its first bar's low; a single-bar flag is a
    red pullback candle. Rejects consolidations with "no lower lows" (ETHT/NBIZ)."""
    if len(flag) == 1:
        return classify(flag[0]) == "red"
    return min(b.low for b in flag) < flag[0].low


def detect(
    bars: list[Bar],
    *,
    min_pole: int = 2,
    max_pole: int = 8,
    max_flag: int = 6,
    max_retracement: float = 0.50,
    entry_offset: float = 0.05,
) -> BullFlag | None:
    """Detect a bull flag at the END of the series (the just-formed setup), else None."""
    if len(bars) < min_pole + 1:
        return None

    peak = _find_pole_peak(bars, max_flag)
    if peak is None:
        return None  # still extending, or the pullback is longer than max_flag -> no flag

    flag = bars[peak + 1 :]

    # Pole: the strictly-higher-highs run ending at the peak, capped at max_pole. Colour-agnostic —
    # a non-green bar is fine as long as the high still steps up. `bars[peak]` is above every flag
    # bar (by _find_pole_peak), and its predecessor is at/below the flag, so the run is >= 2 bars
    # whenever a bar exists to the peak's left.
    pole = [bars[peak]]
    q = peak - 1
    while q >= 0 and len(pole) < max_pole and bars[q + 1].high > bars[q].high:
        pole.insert(0, bars[q])
        q -= 1
    if len(pole) < min_pole:
        return None  # too short a thrust to count as a pole

    if not _flag_makes_lower_low(flag):
        return None  # consolidation never pulled back -> not a flag

    pole_base = pole[0].low
    pole_high = max(b.high for b in pole)  # == bars[peak].high
    flag_low = min(b.low for b in flag)
    if flag_low <= pole_base:
        return None  # pullback erased the pole (retraced through its base)

    retracement = (pole_high - flag_low) / (pole_high - pole_base)
    if retracement > max_retracement:
        return None  # pullback too deep -> flag invalidated ("back through the pole")

    breakout = flag[-1].high
    return BullFlag(
        pole_len=len(pole),
        flag_len=len(flag),
        breakout_level=round(breakout, 4),
        entry_trigger=round(breakout + entry_offset, 4),
        stop=round(flag_low, 4),
        retracement=round(retracement, 4),
        vol_increasing=pole[-1].volume > pole[0].volume,
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
