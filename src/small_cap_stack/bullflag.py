"""Bull-flag detection (issue #16): pure functions over a 5-min OHLCV series.

Strategy pattern (Warrior-style, see README): a short up-thrust *pole* of green extension
candles, immediately followed by a shallow *flag* of red consolidation candles that holds above
the pole's base. The breakout/entry is the tick above the high of the last consolidation candle
(notes.md); the stop is the consolidation low (decisions.md). Constraints: **≤2 green extension
candles, ≤2 red consolidation candles**.

Pure and replayable — runs over the cached raw bars, so the definition can change retroactively.
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
    pole_len: int
    flag_len: int
    breakout_level: float  # high of the last consolidation candle
    entry_trigger: float  # breakout_level + one tick
    stop: float  # consolidation (flag) low


def detect(
    bars: list[Bar],
    *,
    max_green: int = 2,
    max_red: int = 2,
    tick: float = 0.01,
) -> BullFlag | None:
    """Detect a bull flag at the END of the series (the just-formed setup), else None."""
    if len(bars) < 2:
        return None

    j = len(bars) - 1

    # Trailing red run = the consolidation/flag.
    flag: list[Bar] = []
    while j >= 0 and classify(bars[j]) == "red" and len(flag) < max_red:
        flag.append(bars[j])
        j -= 1
    if not flag:
        return None  # no consolidation
    if j >= 0 and classify(bars[j]) == "red":
        return None  # consolidation longer than max_red -> invalid setup
    flag.reverse()

    # Green run immediately before = the pole.
    pole: list[Bar] = []
    while j >= 0 and classify(bars[j]) == "green" and len(pole) < max_green:
        pole.append(bars[j])
        j -= 1
    if not pole:
        return None  # no pole
    if j >= 0 and classify(bars[j]) == "green":
        return None  # pole longer than max_green -> violates the strategy
    pole.reverse()

    # The pullback must hold above the pole's base (not erase the move).
    flag_low = min(b.low for b in flag)
    if flag_low <= pole[0].low:
        return None

    breakout = flag[-1].high
    return BullFlag(
        pole_len=len(pole),
        flag_len=len(flag),
        breakout_level=round(breakout, 4),
        entry_trigger=round(breakout + tick, 4),
        stop=round(flag_low, 4),
    )


def detect_with_settings(bars: list[Bar], settings: Settings) -> BullFlag | None:
    return detect(
        bars,
        max_green=settings.bull_flag_max_green,
        max_red=settings.bull_flag_max_red,
        tick=settings.entry_tick,
    )
